import re

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.completion import mark_task_complete
from core.language_modes import get_language_mode
from core.language_packs import pack_for_mode
from video_study.models import PodcastEpisode, TranscriptVideo
from video_study.views import _allowed_source_languages

# Positive ASCII ids that fit SQLite's signed 64-bit integer primary key.
EPISODE_ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")
SQLITE_MAX_PK = 2**63 - 1


def _episode_pk(value):
    if not EPISODE_ID_PATTERN.fullmatch(value):
        return None
    pk = int(value)
    return pk if pk <= SQLITE_MAX_PK else None


def _offered(language_mode, feature):
    return feature in pack_for_mode(language_mode).features


def _announce(request, created, done_text):
    if created:
        messages.success(request, done_text)
    else:
        messages.info(request, "Already marked done today.")


@require_POST
def complete_media(request):
    """Mark the media task done for today from an already cached video or episode."""
    language_mode = get_language_mode(request)
    kind = request.POST.get("kind", "")

    if kind == "video":
        if not _offered(language_mode, "video"):
            return HttpResponseBadRequest("Video study is not offered in this study language.")
        source_language = request.POST.get("lang", "")
        if source_language not in _allowed_source_languages(language_mode):
            return HttpResponseBadRequest("Unsupported caption language for this study mode.")
        video = TranscriptVideo.objects.filter(
            youtube_id=request.POST.get("video", ""), source_language=source_language
        ).first()
        if video is None or not video.transcript:
            raise Http404("Video is not in the transcript cache")
        label = f"video:{video.youtube_id}"
        target = (
            f"{reverse('video_study:workspace')}?video={video.youtube_id}&lang={source_language}"
        )
    elif kind == "podcast":
        if not _offered(language_mode, "podcast"):
            return HttpResponseBadRequest("Podcast study is not offered in this study language.")
        episode_id = request.POST.get("episode", "")
        episode_pk = _episode_pk(episode_id)
        episode = (
            PodcastEpisode.objects.filter(pk=episode_pk).first() if episode_pk is not None else None
        )
        if episode is None or not episode.transcript:
            raise Http404("Episode is not in the transcript cache")
        label = f"podcast:{episode.pk}"
        target = f"{reverse('podcasts:workspace')}?episode={episode.pk}"
    else:
        return HttpResponseBadRequest("Unknown media type.")

    _, created = mark_task_complete(request.user, "media", language_mode, item_label=label)
    _announce(request, created, "Media marked done for today.")
    return redirect(target)
