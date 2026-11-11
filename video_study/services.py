import html
import ipaddress
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from requests import Session
from requests.exceptions import ProxyError, Timeout

from core.language_packs import pack_for_mode
from core.rotation import rotating_choice

logger = logging.getLogger(__name__)

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
WORD_PATTERN = re.compile(r"^[^\W\d_]+(?:['’\-][^\W\d_]+)*$", re.UNICODE)
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
LANGUAGE_NAMES = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "ru": "Russian",
    "nl": "Dutch",
}


class InvalidYouTubeUrl(ValueError):
    pass


class TranscriptUnavailable(RuntimeError):
    pass


class TranslationUnavailable(RuntimeError):
    pass


class TranslationRateLimited(TranslationUnavailable):
    """A provider answered with HTTP 429 (too many requests)."""


KURZGESAGT_DE_CHANNEL_KEY = "kurzgesagt-de"
KURZGESAGT_DE_CHANNEL_URL = "https://www.youtube.com/@KurzgesagtDE"
KURZGESAGT_DE_CHANNEL_ID = "UCwRH985XgMYXQ6NxXDo8npw"
KURZGESAGT_DE_FEED_URL = (
    "https://www.youtube.com/feeds/videos.xml?channel_id=" + KURZGESAGT_DE_CHANNEL_ID
)
ATOM_NS = "http://www.w3.org/2005/Atom"
YOUTUBE_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"


@dataclass(frozen=True)
class MediaChannel:
    key: str
    name: str
    feed_url: str
    source_language: str


CHANNEL_FEED_PREFIX = "https://www.youtube.com/feeds/videos.xml?channel_id="
KURZGESAGT_DE = MediaChannel(KURZGESAGT_DE_CHANNEL_KEY, "Kurzgesagt", KURZGESAGT_DE_FEED_URL, "de")
NL_CHANNEL_ID = "UCmyAAcFzmkD5SHIa0olqhgw"
NL_CHANNEL = MediaChannel("nl-channel", "Dutch channel", CHANNEL_FEED_PREFIX + NL_CHANNEL_ID, "nl")
MEDIA_CHANNELS = {channel.key: channel for channel in (KURZGESAGT_DE, NL_CHANNEL)}


def channel_for_mode(language_mode):
    return MEDIA_CHANNELS[pack_for_mode(language_mode).video_channel_key]


class ChannelUnavailable(RuntimeError):
    pass


def parse_channel_feed(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ChannelUnavailable("The channel feed could not be parsed.") from exc

    videos = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        youtube_id = (entry.findtext(f"{{{YOUTUBE_NS}}}videoId") or "").strip()
        title = html.unescape((entry.findtext(f"{{{ATOM_NS}}}title") or "").strip())
        if not VIDEO_ID_PATTERN.fullmatch(youtube_id) or not title:
            continue

        published_at = parse_datetime((entry.findtext(f"{{{ATOM_NS}}}published") or "").strip())
        if published_at and timezone.is_naive(published_at):
            published_at = timezone.make_aware(published_at, timezone=UTC)

        link = next(
            (
                candidate
                for candidate in entry.findall(f"{{{ATOM_NS}}}link")
                if candidate.get("rel") == "alternate"
            ),
            None,
        )
        thumbnail = entry.find(f"{{{MEDIA_NS}}}group/{{{MEDIA_NS}}}thumbnail")
        videos.append(
            {
                "youtube_id": youtube_id,
                "title": title,
                "source_url": (link.get("href") if link is not None else "")
                or canonical_youtube_url(youtube_id),
                "thumbnail_url": thumbnail.get("url", "") if thumbnail is not None else "",
                "published_at": published_at,
            }
        )
    if not videos:
        raise ChannelUnavailable("No videos were found in the channel feed.")
    return videos


def fetch_channel_videos(feed_url=KURZGESAGT_DE_FEED_URL, name="Kurzgesagt"):
    request = Request(
        feed_url,
        headers={
            "Accept": "application/atom+xml, application/xml, text/xml",
            "User-Agent": "GermanFlashcards/1.0",
        },
    )
    try:
        with urlopen(
            request,
            timeout=getattr(settings, "VIDEO_CHANNEL_FETCH_TIMEOUT", 8),
        ) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError) as exc:
        logger.warning("%s channel feed fetch failed: %s", name, exc)
        raise ChannelUnavailable(f"The {name} channel is unavailable right now.") from exc
    return parse_channel_feed(payload)


def refresh_channel_catalog(channel, now=None):
    from video_study.models import ChannelVideo

    synced_at = now or timezone.now()
    videos = fetch_channel_videos(channel.feed_url, name=channel.name)
    for video in videos:
        ChannelVideo.objects.update_or_create(
            channel_key=channel.key,
            youtube_id=video["youtube_id"],
            defaults={
                "title": video["title"],
                "source_url": video["source_url"],
                "thumbnail_url": video["thumbnail_url"],
                "published_at": video["published_at"],
                "synced_at": synced_at,
            },
        )
    return videos


def refresh_kurzgesagt_catalog(now=None):
    return refresh_channel_catalog(KURZGESAGT_DE, now=now)


def is_catalog_fresh(model, hours, now, channel_key=None):
    if hours is None:
        return False
    queryset = model.objects.all()
    if channel_key is not None:
        queryset = queryset.filter(channel_key=channel_key)
    latest = queryset.order_by("-synced_at").values_list("synced_at", flat=True).first()
    if latest is None:
        return False
    return (now - latest) < timezone.timedelta(hours=hours)


def implicit_catalog_refresh_enabled():
    """Whether a view path may fetch a catalog because it is empty.

    Off in the test suite (see config/test_runner.py). Explicit refreshes, such
    as the refresh_media_catalogs command, ignore this setting.
    """
    return getattr(settings, "VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH", True)


def get_channel_video_of_day(channel, now=None):
    from video_study.models import ChannelVideo

    current_time = now or timezone.now()
    # Only fetch synchronously to bootstrap an empty catalog (a one-time cost on
    # first use). Periodic freshness is handled out-of-band by the
    # refresh_media_catalogs management command, so the dashboard never blocks on
    # a network round-trip once the catalog is populated. This matters because the
    # production box runs a single gunicorn worker: an in-request fetch would stall
    # every user for the duration of the timeout.
    if (
        implicit_catalog_refresh_enabled()
        and not ChannelVideo.objects.filter(channel_key=channel.key).exists()
    ):
        try:
            refresh_channel_catalog(channel, now=current_time)
        except ChannelUnavailable:
            pass

    videos = list(ChannelVideo.objects.filter(channel_key=channel.key).order_by("youtube_id"))
    return rotating_choice(
        videos,
        timezone.localdate(current_time),
        salt=channel.key,
    )


def get_kurzgesagt_video_of_day(now=None):
    return get_channel_video_of_day(KURZGESAGT_DE, now=now)


PODCAST_API_URL = "https://14minuten.de/wp-json/wp/v2/posts"
PODCAST_CATALOG_LIMIT = 50
PODCAST_YOUTUBE_ID_PATTERN = re.compile(
    r"(?:youtube(?:-nocookie)?[.]com/(?:embed/|watch[?]v=)|youtu[.]be/)([A-Za-z0-9_-]{11})"
)


class PodcastUnavailable(RuntimeError):
    pass


def _plain_text(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _podcast_youtube_id(content):
    content = html.unescape(str(content or "")).replace(r"\/", "/")
    match = PODCAST_YOUTUBE_ID_PATTERN.search(content)
    return match.group(1) if match else ""


def parse_podcast_posts(payload):
    try:
        posts = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, json.JSONDecodeError) as exc:
        raise PodcastUnavailable("The 14 Minuten catalog could not be parsed.") from exc
    if not isinstance(posts, list):
        raise PodcastUnavailable("The 14 Minuten catalog returned an unexpected format.")

    episodes = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        title = _plain_text(post.get("title", {}).get("rendered", ""))
        source_url = html.unescape(str(post.get("link", ""))).strip()
        if not title or not source_url:
            continue
        content = post.get("content", {}).get("rendered", "")
        plain_content = _plain_text(content)
        level_match = re.search(r"Niveau\s*:\s*([A-Za-zÄÖÜäöüß]+)", plain_content)
        published_at = parse_datetime(str(post.get("date_gmt") or post.get("date") or "").strip())
        if published_at and timezone.is_naive(published_at):
            published_at = timezone.make_aware(published_at, timezone=UTC)
        embedded = post.get("_embedded", {}) or {}
        featured_media = embedded.get("wp:featuredmedia", [])
        thumbnail_url = ""
        if featured_media and isinstance(featured_media[0], dict):
            thumbnail_url = str(featured_media[0].get("source_url", "")).strip()
        episodes.append(
            {
                "source_url": source_url,
                "youtube_id": _podcast_youtube_id(content),
                "title": title,
                "description": _plain_text(post.get("excerpt", {}).get("rendered", "")),
                "level": level_match.group(1) if level_match else "",
                "thumbnail_url": thumbnail_url,
                "published_at": published_at,
            }
        )
    if not episodes:
        raise PodcastUnavailable("No public 14 Minuten episodes were found.")
    return episodes


def fetch_podcast_posts(api_url=PODCAST_API_URL, limit=PODCAST_CATALOG_LIMIT):
    query = urlencode(
        {
            "per_page": min(max(int(limit), 1), 100),
            "orderby": "date",
            "order": "desc",
            "_embed": "1",
        }
    )
    request = Request(
        f"{api_url}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "GermanFlashcards/1.0",
        },
    )
    try:
        with urlopen(
            request,
            timeout=getattr(settings, "PODCAST_FETCH_TIMEOUT", 8),
        ) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError) as exc:
        logger.warning("14 Minuten podcast catalog fetch failed: %s", exc)
        raise PodcastUnavailable(
            "The 14 Minuten podcast catalog is unavailable right now."
        ) from exc
    return parse_podcast_posts(payload)


def refresh_podcast_catalog(now=None):
    from video_study.models import PodcastEpisode

    synced_at = now or timezone.now()
    episodes = fetch_podcast_posts()
    for episode in episodes:
        existing = PodcastEpisode.objects.filter(source_url=episode["source_url"]).first()
        defaults = {**episode, "synced_at": synced_at}
        if existing is not None:
            defaults["transcript"] = existing.transcript
        PodcastEpisode.objects.update_or_create(
            source_url=episode["source_url"],
            defaults=defaults,
        )
    return episodes


def get_podcast_of_day(now=None):
    from video_study.models import PodcastEpisode

    current_time = now or timezone.now()
    # See get_kurzgesagt_video_of_day: fetch only to bootstrap an empty catalog;
    # the refresh_media_catalogs command owns periodic freshness so the dashboard
    # stays non-blocking on the single-worker production box.
    if implicit_catalog_refresh_enabled() and not PodcastEpisode.objects.exists():
        try:
            refresh_podcast_catalog(now=current_time)
        except PodcastUnavailable:
            pass

    episodes = list(PodcastEpisode.objects.exclude(youtube_id="").order_by("source_url"))
    return rotating_choice(
        episodes,
        timezone.localdate(current_time),
        salt="14minuten",
    )


def extract_youtube_id(value):
    candidate = (value or "").strip()
    if VIDEO_ID_PATTERN.fullmatch(candidate):
        return candidate

    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise InvalidYouTubeUrl("Enter a valid youtube.com or youtu.be video URL.")

    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path.rstrip("/") == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            video_id = parts[1]

    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidYouTubeUrl("The URL does not contain a valid YouTube video ID.")
    return video_id


def canonical_youtube_url(video_id):
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidYouTubeUrl("Invalid YouTube video ID.")
    return f"https://www.youtube.com/watch?v={video_id}"


def _pick_transcript_track(transcript_list, languages):
    """Match a requested language to a track, tolerating regional codes.

    youtube-transcript-api matches language codes by exact dict key, so a
    request for "nl" misses a video that only offers "nl-NL". For each
    requested language (in priority order) prefer, in turn: an exact-code
    manual track, a regional manual track, an exact-code generated track,
    then a regional generated track.
    """
    for code in languages:
        code_lower = code.lower()
        regional_prefix = f"{code_lower}-"
        manual_exact = manual_regional = generated_exact = generated_regional = None
        for track in transcript_list:
            track_code = track.language_code.lower()
            if track_code == code_lower:
                if track.is_generated:
                    generated_exact = generated_exact or track
                else:
                    manual_exact = manual_exact or track
            elif track_code.startswith(regional_prefix):
                if track.is_generated:
                    generated_regional = generated_regional or track
                else:
                    manual_regional = manual_regional or track
        match = manual_exact or manual_regional or generated_exact or generated_regional
        if match is not None:
            return match
    return None


class _CaptionSession(Session):
    """Apply bounded inactivity waits to every library request, including POST."""

    def __init__(self, read_timeout):
        super().__init__()
        self.trust_env = False
        self.caption_timeout = (min(5.0, read_timeout), read_timeout)

    def request(self, method, url, **kwargs):
        kwargs["timeout"] = self.caption_timeout
        return super().request(method, url, **kwargs)


def _caption_proxy_config():
    from youtube_transcript_api.proxies import GenericProxyConfig

    value = settings.VIDEO_STUDY_YOUTUBE_PROXY_URL
    if not value:
        return None
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)
            or "\\" in value
            or re.search(r"%(?![0-9a-fA-F]{2})", value)
            or parsed.netloc.count("@") > 1
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        # Accessing port validates both numeric syntax and range.
        if parsed.port is not None and parsed.port < 1:
            raise ValueError
        hostname = parsed.hostname.encode("idna").decode("ascii")
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        elif not all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in hostname.rstrip(".").split(".")
        ):
            raise ValueError
        if parsed.username is not None and (not parsed.username or parsed.password is None):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        logger.warning("YouTube caption proxy configuration is invalid")
        raise TranscriptUnavailable(
            "YouTube caption proxy configuration is invalid. Contact the administrator."
        ) from None
    return GenericProxyConfig(http_url=value, https_url=value)


@contextmanager
def _caption_client(video_id, languages):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise TranscriptUnavailable("Transcript support is not installed.") from None

    try:
        proxy = _caption_proxy_config()
        read_timeout = float(settings.VIDEO_STUDY_YOUTUBE_TIMEOUT)
        if not math.isfinite(read_timeout) or read_timeout <= 0:
            raise ValueError
        with _CaptionSession(read_timeout) as session:
            yield YouTubeTranscriptApi(http_client=session, proxy_config=proxy)
    except TranscriptUnavailable:
        raise
    except Exception as exc:
        logger.warning("YouTube transcript fetch failed for %s: %s", video_id, type(exc).__name__)
        raise TranscriptUnavailable(_transcript_error_message(exc, languages)) from None


def _list_transcript_tracks(client, video_id):
    return list(client.list(video_id))


def fetch_transcript(video_id, languages=("de",)):
    with _caption_client(video_id, languages) as client:
        return _fetch_primary_captions(client, video_id, languages)


def _fetch_primary_captions(client, video_id, languages):
    transcript_list = _list_transcript_tracks(client, video_id)
    track = _pick_transcript_track(transcript_list, languages)
    if track is None:
        available = [t.language_code for t in transcript_list]
        logger.warning(
            "Transcript fetch for %s failed: no matching track for %s (available: %s)",
            video_id,
            languages,
            available,
        )
        requested = LANGUAGE_NAMES.get(languages[0], languages[0])
        raise TranscriptUnavailable(f"No {requested} captions are available for this video.")

    return _fetch_transcript_track(video_id, track, languages)


def fetch_english_captions(video_id):
    """Fetch only on bilingual opt-in, using YouTube tracks and translation.

    The cache key is the base language `en`; the actual regional language and
    whether YouTube translated Dutch are returned separately for provenance.
    Never use the word-translation providers for a whole transcript.
    """
    with _caption_client(video_id, ("en",)) as client:
        return _fetch_english_captions(client, video_id)


def _fetch_english_captions(client, video_id):
    tracks = _list_transcript_tracks(client, video_id)
    track = _pick_transcript_track(tracks, ("en",))
    origin = "manual"
    if track is None:
        dutch = _pick_transcript_track(tracks, ("nl",))
        if dutch is None or not dutch.is_translatable:
            raise TranscriptUnavailable(
                "No English captions or YouTube English translation are available for this video."
            )
        # Translation availability uses exact codes in youtube-transcript-api.
        codes = [
            item["language_code"] if isinstance(item, dict) else item.language_code
            for item in dutch.translation_languages
        ]
        english_code = next((code for code in codes if code.lower() == "en"), None)
        if english_code is None:
            english_code = next((code for code in codes if code.lower().startswith("en-")), None)
        if english_code is None:
            raise TranscriptUnavailable(
                "YouTube cannot translate these Dutch captions into English."
            )
        track = dutch.translate(english_code)
        origin = "translated"
    elif track.is_generated:
        origin = "generated"
    return {
        "transcript": _fetch_transcript_track(video_id, track, ("en",)),
        "caption_origin": origin,
        "caption_language": track.language_code,
    }


def _fetch_transcript_track(video_id, track, languages):
    try:
        fetched = track.fetch()
        raw_items = fetched.to_raw_data()
        cues = []
        for item in raw_items:
            text = html.unescape(str(item.get("text", ""))).replace("\n", " ").strip()
            if not text:
                continue
            start = max(float(item.get("start", 0)), 0)
            duration = max(float(item.get("duration", 0)), 0)
            if not math.isfinite(start) or not math.isfinite(duration):
                raise ValueError("Invalid caption timestamp")
            cues.append(
                {
                    "text": text,
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                }
            )
    except Exception as exc:
        logger.warning("YouTube transcript fetch failed for %s: %s", video_id, type(exc).__name__)
        raise TranscriptUnavailable(_transcript_error_message(exc, languages)) from None

    if not cues:
        logger.warning("Transcript fetch for %s failed: empty transcript returned", video_id)
        raise TranscriptUnavailable("YouTube returned an empty transcript for this video.")
    return cues


_BLOCKED_CONNECTION_MESSAGE = "YouTube blocked transcript requests from this connection."


def _transcript_error_message(exc, languages):
    if isinstance(exc, ProxyError):
        return "The YouTube caption proxy could not be reached. Please try again later."
    if isinstance(exc, Timeout):
        return "The YouTube caption request timed out. Please try again later."
    name = type(exc).__name__
    if name in {"NoTranscriptFound", "TranscriptsDisabled"}:
        requested = LANGUAGE_NAMES.get(languages[0], languages[0])
        return f"No {requested} captions are available for this video."
    if name in {"VideoUnavailable", "InvalidVideoId"}:
        return "This YouTube video is unavailable."
    if name in {"RequestBlocked", "IpBlocked"}:
        return _BLOCKED_CONNECTION_MESSAGE
    return "The transcript could not be loaded from YouTube. Please try again later."


def prefetch_channel_transcripts(channel, pause=2.0, now=None):
    """Cache transcripts for a channel's catalog in the background.

    Refreshes the catalog first if it is empty or stale, then fetches a
    transcript for a bounded number of uncached videos per run. Meant to run off
    the request path (see start_channel_prefetch) so the video of the day
    is already cached before anyone opens it.
    """
    from video_study.models import ChannelVideo, TranscriptVideo

    current_time = now or timezone.now()
    catalog_refreshed = False
    if not is_catalog_fresh(ChannelVideo, 24, current_time, channel_key=channel.key):
        try:
            refresh_channel_catalog(channel, now=current_time)
            catalog_refreshed = True
        except ChannelUnavailable as exc:
            logger.warning("%s prefetch: catalog refresh failed: %s", channel.name, exc)

    catalog_ids = list(
        ChannelVideo.objects.filter(channel_key=channel.key)
        .order_by("youtube_id")
        .values_list("youtube_id", flat=True)
    )
    cached_ids = set(
        TranscriptVideo.objects.filter(
            youtube_id__in=catalog_ids,
            source_language=channel.source_language,
        ).values_list("youtube_id", flat=True)
    )
    pending_ids = [video_id for video_id in catalog_ids if video_id not in cached_ids]

    fetched = 0
    failed = 0
    attempted_ids = []
    cap = settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS
    if cap and pending_ids:
        today = timezone.localdate(current_time)
        # Walk the same full-catalog rotation as the dashboard, starting today.
        # Rebuilding a rotation from pending IDs would change the schedule.
        for offset in range(len(catalog_ids)):
            video_id = rotating_choice(
                catalog_ids, today + timedelta(days=offset), salt=channel.key
            )
            if video_id not in cached_ids:
                attempted_ids.append(video_id)
                if len(attempted_ids) == cap:
                    break
    for index, video_id in enumerate(attempted_ids):
        try:
            transcript = fetch_transcript(video_id, languages=(channel.source_language,))
        except TranscriptUnavailable as exc:
            failed += 1
            logger.warning(
                "%s prefetch: transcript fetch failed for %s: %s",
                channel.name,
                video_id,
                type(exc).__name__,
            )
            if str(exc) == _BLOCKED_CONNECTION_MESSAGE:
                break
        else:
            TranscriptVideo.objects.update_or_create(
                youtube_id=video_id,
                source_language=channel.source_language,
                defaults={
                    "source_url": canonical_youtube_url(video_id),
                    "transcript": transcript,
                },
            )
            fetched += 1
        if pause and index < len(attempted_ids) - 1:
            time.sleep(pause)

    summary = {
        "catalog_refreshed": catalog_refreshed,
        "fetched": fetched,
        "failed": failed,
        "remaining": len(pending_ids) - fetched - failed,
    }
    logger.info("%s prefetch summary: %s", channel.name, summary)
    return summary


def prefetch_kurzgesagt_transcripts(pause=2.0, now=None):
    return prefetch_channel_transcripts(KURZGESAGT_DE, pause=pause, now=now)


_prefetch_lock = threading.Lock()
_last_prefetch_date = None


def start_kurzgesagt_prefetch():
    """Start prefetch_kurzgesagt_transcripts in a daemon thread, at most once a day.

    Safe to call on every dashboard view: the lock and date check make
    repeated calls on the same local day a cheap no-op, so gunicorn's
    request threads never block on it.
    """
    global _last_prefetch_date

    if not getattr(settings, "VIDEO_STUDY_TRANSCRIPT_PREFETCH", True):
        return

    today = timezone.localdate()
    with _prefetch_lock:
        if _last_prefetch_date == today:
            return
        _last_prefetch_date = today

    def _run():
        try:
            prefetch_kurzgesagt_transcripts()
        except Exception as exc:
            logger.warning("Kurzgesagt prefetch thread failed: %s", type(exc).__name__)
        finally:
            connection.close()

    threading.Thread(target=_run, daemon=True).start()


_channel_prefetch_lock = threading.Lock()
_channel_prefetch_dates = {}


def start_channel_prefetch(channel):
    """Start prefetch_channel_transcripts in a daemon thread, at most once a day.

    Safe to call on every dashboard view: the lock and date check make
    repeated calls on the same local day a cheap no-op, so gunicorn's
    request threads never block on it.
    """
    if not getattr(settings, "VIDEO_STUDY_TRANSCRIPT_PREFETCH", True):
        return

    today = timezone.localdate()
    with _channel_prefetch_lock:
        if _channel_prefetch_dates.get(channel.key) == today:
            return
        _channel_prefetch_dates[channel.key] = today

    def _run():
        try:
            prefetch_channel_transcripts(channel)
        except Exception as exc:
            logger.warning("%s prefetch thread failed: %s", channel.name, type(exc).__name__)
        finally:
            connection.close()

    threading.Thread(target=_run, daemon=True).start()


def normalize_selection(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if not text or not re.search(r"[^\W\d_]", text, re.UNICODE):
        raise TranslationUnavailable("Select one or more words containing letters.")
    if len(text.encode("utf-8")) > 500:
        raise TranslationUnavailable("The selected text is too long to translate.")
    if any(ord(character) < 32 for character in text):
        raise TranslationUnavailable("The selected text contains unsupported characters.")
    return text.casefold()


def normalize_word(value):
    word = normalize_selection(value)
    if not WORD_PATTERN.fullmatch(word):
        raise TranslationUnavailable("Select a single word containing letters.")
    return word


CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04ff]")


def translation_uses_target_script(value, target_language):
    """Reject provider output that is not written in the target script."""

    translated = str(value or "").strip()
    if not translated:
        return False
    if target_language == "ru":
        return bool(CYRILLIC_PATTERN.search(translated))
    return bool(re.search(r"[^\W\d_]", translated, re.UNICODE))


def _configured_translation_providers():
    providers = getattr(
        settings,
        "VIDEO_STUDY_TRANSLATION_PROVIDERS",
        ("wiktionary", "deepl", "libretranslate", "mymemory"),
    )
    if isinstance(providers, str):
        providers = providers.split(",")
    return tuple(str(provider).strip().lower() for provider in providers if str(provider).strip())


# Set by request_translation to the seconds left in the request's overall
# budget for the provider call currently in flight, so _read_translation_response
# can cap its HTTP timeout without changing any provider function's signature.
_remaining_budget = ContextVar("_remaining_budget", default=None)


def _translation_timeout():
    configured = getattr(settings, "VIDEO_STUDY_TRANSLATION_TIMEOUT", 8)
    remaining = _remaining_budget.get()
    if remaining is None:
        return configured
    return max(0.001, min(configured, remaining))


def _translation_score(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _read_translation_response(request, provider):
    try:
        with urlopen(request, timeout=_translation_timeout()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (
        HTTPError,
        URLError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        status = getattr(exc, "code", None) or type(exc).__name__
        logger.warning("Translation provider %s failed (status=%s): %s", provider, status, exc)
        if isinstance(exc, HTTPError) and exc.code == 429:
            raise TranslationRateLimited(
                "The translation service is rate-limiting requests. Please try again."
            ) from exc
        raise TranslationUnavailable(
            "The translation service is unavailable. Please try again."
        ) from exc


DEEPL_TARGET_CODES = {"en": "EN-GB"}


def _request_deepl_translation(source_text, source_language, target_language):
    api_key = getattr(settings, "VIDEO_STUDY_DEEPL_API_KEY", "").strip()
    if not api_key:
        return ""
    endpoint = getattr(
        settings,
        "VIDEO_STUDY_DEEPL_ENDPOINT",
        "https://api-free.deepl.com/v2/translate",
    )
    request = Request(
        endpoint,
        data=json.dumps(
            {
                "text": [source_text],
                "source_lang": source_language.upper(),
                "target_lang": DEEPL_TARGET_CODES.get(target_language, target_language.upper()),
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "GermanFlashcards/1.0",
        },
    )
    translations = _read_translation_response(request, "deepl").get("translations", [])
    if not translations:
        return ""
    return html.unescape(str(translations[0].get("text", ""))).strip()


def _strip_balanced_wiki_templates(value):
    """Remove MediaWiki ``{{...}}`` templates, including nested templates."""

    output = []
    depth = 0
    index = 0
    while index < len(value):
        if value.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if depth and value.startswith("}}", index):
            depth -= 1
            index += 2
            continue
        if depth == 0:
            output.append(value[index])
        index += 1
    return "".join(output)


def clean_translation_text(value, max_alternatives=None):
    """Turn provider output into concise, readable flashcard text.

    Dictionary links keep their visible label, while usage/example templates,
    references, HTML, and other presentation markup are removed.  Callers that
    create a card may also cap long lists of alternative meanings.
    """

    text = html.unescape(str(value or ""))
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref\b[^>]*>.*?</ref\s*>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<ref\b[^>]*/\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "; ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _strip_balanced_wiki_templates(text)
    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]",
        lambda match: match.group(2),
        text,
    )
    text = re.sub(r"\[\[([^\]]+)\]\]", lambda match: match.group(1), text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"[{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ;,:")

    alternatives = []
    seen = set()
    for alternative in re.split(r"\s*(?:;|\s+/\s+)\s*", text):
        alternative = re.sub(r"\s+", " ", alternative).strip(" -;,:|")
        normalized = alternative.casefold()
        if not alternative or normalized in seen:
            continue
        alternatives.append(alternative)
        seen.add(normalized)
        if max_alternatives and len(alternatives) >= max_alternatives:
            break
    return "; ".join(alternatives)


def _strip_wiktionary_markup(value):
    return clean_translation_text(value)


def _russian_wiktionary_meanings(wikitext, source_language):
    language_marker = f"= {{{{-{source_language}-}}}} ="
    if language_marker not in wikitext:
        return []
    language_section = wikitext.split(language_marker, 1)[1]
    next_language = re.search(
        r"^=\s*\{\{-[a-z-]+-\}\}\s*=\s*$",
        language_section,
        re.MULTILINE,
    )
    if next_language:
        language_section = language_section[: next_language.start()]
    meaning_match = re.search(
        r"====\s*Значение\s*====(?P<body>.*?)(?:\n====|\n===|\Z)",
        language_section,
        re.DOTALL,
    )
    if not meaning_match:
        return []
    meanings = []
    for line in meaning_match.group("body").splitlines():
        if not re.match(r"^#(?![#*:])", line):
            continue
        meaning = _strip_wiktionary_markup(line[1:])
        if translation_uses_target_script(meaning, "ru"):
            meanings.append(meaning)
    return meanings


def _request_wiktionary_translation(source_text, source_language, target_language):
    if target_language != "ru" or not WORD_PATTERN.fullmatch(source_text):
        return ""
    endpoint = getattr(
        settings,
        "VIDEO_STUDY_WIKTIONARY_ENDPOINT",
        "https://ru.wiktionary.org/w/api.php",
    )
    query = urlencode(
        {
            "action": "parse",
            "page": source_text,
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
        }
    )
    payload = _read_translation_response(
        Request(
            f"{endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "GermanFlashcards/1.0",
            },
        ),
        "wiktionary",
    )
    meanings = _russian_wiktionary_meanings(
        str(payload.get("parse", {}).get("wikitext", "")),
        source_language,
    )
    return " / ".join(meanings[:3])


def _request_libretranslate_translation(source_text, source_language, target_language):
    endpoint = getattr(settings, "VIDEO_STUDY_LIBRETRANSLATE_ENDPOINT", "").strip()
    if not endpoint:
        return ""
    if not endpoint.rstrip("/").endswith("/translate"):
        endpoint = endpoint.rstrip("/") + "/translate"
    body = {
        "q": source_text,
        "source": source_language,
        "target": target_language,
        "format": "text",
        "alternatives": 3,
    }
    api_key = getattr(settings, "VIDEO_STUDY_LIBRETRANSLATE_API_KEY", "").strip()
    if api_key:
        body["api_key"] = api_key
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "GermanFlashcards/1.0",
        },
    )
    payload = _read_translation_response(request, "libretranslate")
    candidates = [payload.get("translatedText", ""), *payload.get("alternatives", [])]
    return next(
        (
            html.unescape(str(candidate)).strip()
            for candidate in candidates
            if translation_uses_target_script(candidate, target_language)
        ),
        "",
    )


def _request_mymemory_translation(source_text, source_language, target_language):
    endpoint = getattr(
        settings,
        "VIDEO_STUDY_TRANSLATION_ENDPOINT",
        "https://api.mymemory.translated.net/get",
    )
    query = {
        "q": source_text,
        "langpair": f"{source_language}|{target_language}",
        "mt": "1",
    }
    contact = getattr(settings, "VIDEO_STUDY_TRANSLATION_EMAIL", "")
    if contact:
        query["de"] = contact

    request = Request(
        f"{endpoint}?{urlencode(query)}",
        headers={
            "Accept": "application/json",
            "User-Agent": "GermanFlashcards/1.0",
        },
    )
    payload = _read_translation_response(request, "mymemory")
    candidates = [payload.get("responseData", {}).get("translatedText", "")]
    normalized_source = " ".join(source_text.split()).casefold()
    exact_matches = [
        match
        for match in payload.get("matches", [])
        if " ".join(str(match.get("segment", "")).split()).casefold() == normalized_source
    ]
    exact_matches.sort(
        key=lambda match: (
            _translation_score(match.get("match")),
            _translation_score(match.get("quality")),
        ),
        reverse=True,
    )
    candidates.extend(match.get("translation", "") for match in exact_matches)
    return next(
        (
            html.unescape(str(candidate)).strip()
            for candidate in candidates
            if translation_uses_target_script(candidate, target_language)
        ),
        "",
    )


def request_translation_detailed(
    source_text,
    source_language="de",
    target_language="ru",
    source_form=None,
    budget_start=None,
):
    """Run the provider chain and return (translated_text, provider, rate_limited).

    ``rate_limited`` is true when some provider tried before the one that
    finally answered was skipped because it returned HTTP 429, so the
    winning translation may be lower quality than usual.
    """
    source_form = " ".join(str(source_form or source_text).split()).strip()
    source_text = normalize_selection(source_text)
    provider_functions = {
        "deepl": lambda: _request_deepl_translation(source_text, source_language, target_language),
        "wiktionary": lambda: _request_wiktionary_translation(
            source_form, source_language, target_language
        ),
        "libretranslate": lambda: _request_libretranslate_translation(
            source_text, source_language, target_language
        ),
        "mymemory": lambda: _request_mymemory_translation(
            source_text, source_language, target_language
        ),
    }
    budget = getattr(settings, "VIDEO_STUDY_TRANSLATION_BUDGET", 12)
    start = budget_start if budget_start is not None else time.monotonic()
    last_error = None
    rate_limited = False
    for provider in _configured_translation_providers():
        provider_function = provider_functions.get(provider)
        if provider_function is None:
            continue
        if provider == "wiktionary" and target_language != "ru":
            continue
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0:
            break
        token = _remaining_budget.set(remaining)
        try:
            translated = clean_translation_text(provider_function())
        except TranslationRateLimited as exc:
            last_error = exc
            rate_limited = True
            continue
        except TranslationUnavailable as exc:
            last_error = exc
            continue
        finally:
            _remaining_budget.reset(token)
        if translation_uses_target_script(translated, target_language):
            return translated, provider, rate_limited

    if target_language == "ru":
        raise TranslationUnavailable(
            "No reliable Cyrillic Russian translation was found. Try selecting the full phrase."
        )
    if last_error:
        raise last_error
    target_name = LANGUAGE_NAMES.get(target_language, target_language)
    raise TranslationUnavailable(f"No {target_name} translation was found for this selection.")


def request_translation(
    source_text,
    source_language="de",
    target_language="ru",
    source_form=None,
    budget_start=None,
):
    translated, _provider, _rate_limited = request_translation_detailed(
        source_text,
        source_language=source_language,
        target_language=target_language,
        source_form=source_form,
        budget_start=budget_start,
    )
    return translated
