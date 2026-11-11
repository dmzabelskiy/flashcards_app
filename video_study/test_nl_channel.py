from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study import services
from video_study.models import ChannelVideo, TranscriptVideo
from video_study.services import (
    KURZGESAGT_DE,
    NL_CHANNEL,
    channel_for_mode,
    get_channel_video_of_day,
    is_catalog_fresh,
    prefetch_channel_transcripts,
    refresh_channel_catalog,
    start_channel_prefetch,
)

FEED_ENTRY = {
    "youtube_id": "nlnlnlnlnl1",
    "title": "Aflevering",
    "source_url": "https://www.youtube.com/watch?v=nlnlnlnlnl1",
    "thumbnail_url": "",
    "published_at": None,
}
TRANSCRIPT = [{"text": "Hallo allemaal", "start": 0.0, "duration": 1.0}]


class NlChannelTests(TestCase):
    def test_channel_for_mode(self):
        self.assertIs(channel_for_mode("NL-EN"), NL_CHANNEL)
        self.assertIs(channel_for_mode("DE-ES"), KURZGESAGT_DE)
        self.assertEqual(
            NL_CHANNEL.feed_url,
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCmyAAcFzmkD5SHIa0olqhgw",
        )

    @patch("video_study.services.fetch_channel_videos", return_value=[FEED_ENTRY])
    def test_refresh_stores_rows_under_the_dutch_key(self, fetch):
        refresh_channel_catalog(NL_CHANNEL)
        fetch.assert_called_once_with(NL_CHANNEL.feed_url, name=NL_CHANNEL.name)
        self.assertTrue(
            ChannelVideo.objects.filter(channel_key="nl-channel", youtube_id="nlnlnlnlnl1").exists()
        )

    def test_freshness_is_per_channel(self):
        now = timezone.now()
        ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="dededede001",
            title="x",
            source_url="https://www.youtube.com/watch?v=dededede001",
            synced_at=now,
        )
        self.assertTrue(is_catalog_fresh(ChannelVideo, 24, now, channel_key="kurzgesagt-de"))
        self.assertFalse(is_catalog_fresh(ChannelVideo, 24, now, channel_key="nl-channel"))

    @override_settings(VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH=True)
    @patch("video_study.services.fetch_channel_videos", return_value=[FEED_ENTRY])
    def test_video_of_day_never_returns_a_german_video(self, _fetch):
        ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="dededede001",
            title="x",
            source_url="https://www.youtube.com/watch?v=dededede001",
            synced_at=timezone.now(),
        )
        video = get_channel_video_of_day(NL_CHANNEL)
        self.assertEqual(video.youtube_id, "nlnlnlnlnl1")

    @patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT)
    @patch("video_study.services.fetch_channel_videos", return_value=[FEED_ENTRY])
    def test_prefetch_caches_dutch_transcripts(self, _fetch, fetch_transcript):
        summary = prefetch_channel_transcripts(NL_CHANNEL, pause=0)
        fetch_transcript.assert_called_once_with("nlnlnlnlnl1", languages=("nl",))
        self.assertTrue(
            TranscriptVideo.objects.filter(youtube_id="nlnlnlnlnl1", source_language="nl").exists()
        )
        self.assertEqual(summary["fetched"], 1)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH=True)
    @patch("video_study.services.threading.Thread")
    def test_start_channel_prefetch_runs_once_a_day_per_channel(self, thread):
        services._channel_prefetch_dates.clear()
        start_channel_prefetch(NL_CHANNEL)
        start_channel_prefetch(NL_CHANNEL)
        thread.assert_called_once()


class NlDashboardMediaTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()
        self.video = ChannelVideo.objects.create(
            channel_key="nl-channel",
            youtube_id="nlnlnlnlnl1",
            title="Aflevering",
            source_url="https://www.youtube.com/watch?v=nlnlnlnlnl1",
            synced_at=timezone.now(),
        )

    @patch("core.views.start_channel_prefetch")
    @patch("core.views.start_kurzgesagt_prefetch")
    @patch("reviews.daily_plan.get_podcast_of_day")
    def test_dashboard_uses_the_dutch_channel(self, podcast, start_de, start_nl):
        response = self.client.get(reverse("core:dashboard"))
        media = response.context["daily_plan"]["media"]
        self.assertEqual(media["video"], self.video)
        self.assertIn("lang=nl&autofetch=1", media["video_url"])
        self.assertIsNone(media["podcast"])
        podcast.assert_not_called()
        start_nl.assert_called_once_with(NL_CHANNEL)
        start_de.assert_not_called()


class NlWorkspaceDefaultTests(AuthedTestCase):
    def test_workspace_defaults_to_dutch_in_nl_mode(self):
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()
        response = self.client.get(reverse("video_study:workspace"))
        self.assertEqual(response.context["source_language"], "nl")

    @patch("video_study.views.fetch_transcript", return_value=TRANSCRIPT)
    def test_autofetch_uses_dutch_captions(self, fetch_transcript):
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()
        self.client.get(reverse("video_study:workspace") + "?video=nlnlnlnlnl1&lang=nl&autofetch=1")
        fetch_transcript.assert_called_once_with("nlnlnlnlnl1", languages=("nl",))


class RefreshMediaCatalogsNlTests(TestCase):
    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH=True)
    @patch(
        "video_study.management.commands.refresh_media_catalogs.refresh_podcast_catalog",
        return_value=[],
    )
    @patch(
        "video_study.management.commands.refresh_media_catalogs.prefetch_kurzgesagt_transcripts",
        return_value={},
    )
    @patch(
        "video_study.management.commands.refresh_media_catalogs.refresh_kurzgesagt_catalog",
        return_value=[],
    )
    @patch(
        "video_study.management.commands.refresh_media_catalogs.prefetch_channel_transcripts",
        return_value={},
    )
    @patch(
        "video_study.management.commands.refresh_media_catalogs.refresh_channel_catalog",
        return_value=[],
    )
    def test_boot_refresh_covers_the_dutch_channel(self, refresh_nl, prefetch_nl, *_):
        call_command(
            "refresh_media_catalogs", "--if-stale", "24", stdout=StringIO(), stderr=StringIO()
        )
        refresh_nl.assert_any_call(NL_CHANNEL)
        prefetch_nl.assert_any_call(NL_CHANNEL)
