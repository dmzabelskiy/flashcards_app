from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.testutils import AuthedTestCase
from video_study.models import ChannelVideo, PodcastEpisode
from video_study.services import (
    KURZGESAGT_DE,
    get_channel_video_of_day,
    get_podcast_of_day,
    refresh_podcast_catalog,
)

CHANNEL_ENTRY = {
    "youtube_id": "dQw4w9WgXcQ",
    "title": "Warum ist der Himmel blau?",
    "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "thumbnail_url": "",
    "published_at": timezone.now(),
}
PODCAST_ENTRY = {
    "source_url": "https://14minuten.de/folge-1/",
    "youtube_id": "abcdefghijk",
    "title": "Folge 1",
    "description": "",
    "level": "",
    "thumbnail_url": "",
    "published_at": timezone.now(),
}


class ImplicitCatalogRefreshSwitchTests(AuthedTestCase):
    """The test runner turns the switch off; production leaves it on."""

    def test_the_runner_leaves_the_switch_off(self):
        from django.conf import settings

        self.assertFalse(settings.VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH)

    @patch("video_study.services.fetch_podcast_posts")
    @patch("video_study.services.fetch_channel_videos")
    def test_dashboard_with_empty_catalogs_performs_no_fetch(self, channel_fetch, podcast_fetch):
        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        channel_fetch.assert_not_called()
        podcast_fetch.assert_not_called()
        media = response.context["daily_plan"]["media"]
        self.assertIsNone(media["video"])
        self.assertIsNone(media["podcast"])

    @patch("video_study.services.fetch_podcast_posts")
    @patch("video_study.services.fetch_channel_videos")
    def test_daily_selection_with_empty_catalogs_yields_nothing(self, channel_fetch, podcast_fetch):
        self.assertIsNone(get_channel_video_of_day(KURZGESAGT_DE))
        self.assertIsNone(get_podcast_of_day())

        channel_fetch.assert_not_called()
        podcast_fetch.assert_not_called()

    @override_settings(VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH=True)
    @patch("video_study.services.fetch_podcast_posts", return_value=[PODCAST_ENTRY])
    @patch("video_study.services.fetch_channel_videos", return_value=[CHANNEL_ENTRY])
    def test_with_the_switch_on_an_empty_catalog_is_bootstrapped(
        self, channel_fetch, podcast_fetch
    ):
        video = get_channel_video_of_day(KURZGESAGT_DE)
        podcast = get_podcast_of_day()

        self.assertEqual(video.youtube_id, "dQw4w9WgXcQ")
        self.assertEqual(podcast.youtube_id, "abcdefghijk")
        channel_fetch.assert_called_once()
        podcast_fetch.assert_called_once()

    @patch("video_study.services.fetch_podcast_posts", return_value=[PODCAST_ENTRY])
    def test_an_explicit_refresh_ignores_the_switch(self, podcast_fetch):
        refresh_podcast_catalog()

        podcast_fetch.assert_called_once()
        self.assertEqual(PodcastEpisode.objects.count(), 1)

    @patch("video_study.views.refresh_podcast_catalog")
    def test_podcast_workspace_does_not_bootstrap_an_empty_catalog(self, refresh):
        self.client.get(reverse("podcasts:workspace"))

        refresh.assert_not_called()

    @patch("video_study.views.refresh_podcast_catalog")
    def test_podcast_workspace_refresh_parameter_still_refreshes(self, refresh):
        self.client.get(reverse("podcasts:workspace"), {"refresh": "1"})

        refresh.assert_called_once()

    def test_seeded_rows_are_still_selected_with_the_switch_off(self):
        ChannelVideo.objects.create(
            channel_key=KURZGESAGT_DE.key,
            youtube_id="dQw4w9WgXcQ",
            title="Seeded",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            synced_at=timezone.now(),
        )

        self.assertEqual(get_channel_video_of_day(KURZGESAGT_DE).title, "Seeded")
