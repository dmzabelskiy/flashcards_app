from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study.models import ChannelVideo
from video_study.services import (
    KURZGESAGT_DE,
    NL_CHANNEL,
    SPANISH_AFTER_HOURS,
    ChannelUnavailable,
    channel_for_mode,
    refresh_channel_catalog,
)

FEED_PREFIX = "https://www.youtube.com/feeds/videos.xml?playlist_id="
UPLOADS_FEED = FEED_PREFIX + "UULFfG2VhlQgy5bHGmkpeKcjVA"
BEGINNER_FEED = FEED_PREFIX + "PLLFFrODykXG95MQpEK28TGfV4qL0LUlMe"
COMPREHENSIBLE_FEED = FEED_PREFIX + "PLLFFrODykXG96ZZ-JQ4Upppexe_CCVRFo"
VLOGS_FEED = FEED_PREFIX + "PLLFFrODykXG96K3DvC1lmWFzjEctkOLuJ"


def entry(youtube_id, title="Video"):
    return {
        "youtube_id": youtube_id,
        "title": title,
        "source_url": f"https://www.youtube.com/watch?v={youtube_id}",
        "thumbnail_url": "",
        "published_at": None,
    }


class SpanishAfterHoursChannelTests(TestCase):
    def test_channel_for_mode(self):
        self.assertIs(channel_for_mode("ES-RU"), SPANISH_AFTER_HOURS)
        self.assertIs(channel_for_mode("NL-EN"), NL_CHANNEL)
        self.assertIs(channel_for_mode("DE-RU"), KURZGESAGT_DE)

    def test_the_channel_lists_the_uploads_feed_and_three_playlists_in_order(self):
        self.assertEqual(SPANISH_AFTER_HOURS.key, "spanish-after-hours")
        self.assertEqual(SPANISH_AFTER_HOURS.name, "Spanish After Hours")
        self.assertEqual(SPANISH_AFTER_HOURS.source_language, "es")
        self.assertEqual(SPANISH_AFTER_HOURS.feed_url, UPLOADS_FEED)
        self.assertEqual(
            SPANISH_AFTER_HOURS.feed_urls,
            (UPLOADS_FEED, BEGINNER_FEED, COMPREHENSIBLE_FEED, VLOGS_FEED),
        )

    def test_the_other_channels_keep_one_feed(self):
        self.assertEqual(KURZGESAGT_DE.feed_urls, (KURZGESAGT_DE.feed_url,))
        self.assertEqual(NL_CHANNEL.feed_urls, (NL_CHANNEL.feed_url,))

    @patch("video_study.services.fetch_channel_videos")
    def test_refresh_reads_every_feed_and_stores_both_under_the_channel_key(self, fetch):
        fetch.side_effect = [
            [entry("esesesesab1")],
            [entry("esesesesab2")],
            [entry("esesesesab3")],
            [entry("esesesesab4")],
        ]

        videos = refresh_channel_catalog(SPANISH_AFTER_HOURS)

        self.assertEqual(
            [video["youtube_id"] for video in videos],
            ["esesesesab1", "esesesesab2", "esesesesab3", "esesesesab4"],
        )
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            [UPLOADS_FEED, BEGINNER_FEED, COMPREHENSIBLE_FEED, VLOGS_FEED],
        )
        self.assertEqual(
            set(
                ChannelVideo.objects.filter(channel_key="spanish-after-hours").values_list(
                    "youtube_id", flat=True
                )
            ),
            {"esesesesab1", "esesesesab2", "esesesesab3", "esesesesab4"},
        )

    @patch("video_study.services.fetch_channel_videos")
    def test_one_failing_feed_does_not_lose_the_other_feeds_videos(self, fetch):
        fetch.side_effect = [
            ChannelUnavailable("down"),
            [entry("esesesesab2")],
            ChannelUnavailable("down"),
            ChannelUnavailable("down"),
        ]

        videos = refresh_channel_catalog(SPANISH_AFTER_HOURS)

        self.assertEqual([video["youtube_id"] for video in videos], ["esesesesab2"])
        self.assertEqual(
            list(ChannelVideo.objects.filter(channel_key="spanish-after-hours")).pop().youtube_id,
            "esesesesab2",
        )

    @patch("video_study.services.fetch_channel_videos")
    def test_the_first_feed_alone_is_enough(self, fetch):
        fetch.side_effect = [
            [entry("esesesesab1")],
            ChannelUnavailable("down"),
            ChannelUnavailable("down"),
            ChannelUnavailable("down"),
        ]

        refresh_channel_catalog(SPANISH_AFTER_HOURS)

        self.assertTrue(ChannelVideo.objects.filter(youtube_id="esesesesab1").exists())

    @patch("video_study.services.fetch_channel_videos", side_effect=ChannelUnavailable("down"))
    def test_every_feed_failing_raises(self, _fetch):
        with self.assertRaises(ChannelUnavailable):
            refresh_channel_catalog(SPANISH_AFTER_HOURS)
        self.assertFalse(ChannelVideo.objects.exists())

    @patch("video_study.services.fetch_channel_videos")
    def test_a_video_in_both_feeds_is_stored_once(self, fetch):
        fetch.side_effect = [
            [entry("esesesesab1")],
            [entry("esesesesab1")],
            [],
            [entry("esesesesab1")],
        ]

        refresh_channel_catalog(SPANISH_AFTER_HOURS)

        self.assertEqual(ChannelVideo.objects.filter(channel_key="spanish-after-hours").count(), 1)


class RefreshMediaCatalogsEsTests(TestCase):
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
    def test_boot_refresh_covers_the_spanish_channel(self, refresh, prefetch, *_):
        call_command(
            "refresh_media_catalogs", "--if-stale", "24", stdout=StringIO(), stderr=StringIO()
        )
        refresh.assert_any_call(SPANISH_AFTER_HOURS)
        prefetch.assert_any_call(SPANISH_AFTER_HOURS)

    @patch("video_study.management.commands.refresh_media_catalogs.refresh_podcast_catalog")
    @patch("video_study.management.commands.refresh_media_catalogs.refresh_kurzgesagt_catalog")
    @patch(
        "video_study.management.commands.refresh_media_catalogs.refresh_channel_catalog",
        side_effect=ChannelUnavailable("down"),
    )
    def test_an_unavailable_channel_is_reported_and_does_not_stop_the_command(self, *_):
        stderr = StringIO()
        call_command("refresh_media_catalogs", stdout=StringIO(), stderr=stderr)
        self.assertIn("Spanish After Hours unavailable", stderr.getvalue())


class EsModePagesTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.ES_RU
        session.save()

    def test_the_dashboard_answers_200_with_the_spanish_channel(self):
        ChannelVideo.objects.create(
            channel_key="spanish-after-hours",
            youtube_id="esesesesab1",
            title="Hola",
            source_url="https://www.youtube.com/watch?v=esesesesab1",
            synced_at="2026-01-01T00:00:00Z",
        )
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        media = response.context["daily_plan"]["media"]
        self.assertEqual(media["video"].youtube_id, "esesesesab1")
        self.assertIn("lang=es&autofetch=1", media["video_url"])
        self.assertIsNone(media["podcast"])

    def test_the_drills_page_answers_200(self):
        response = self.client.get(reverse("reviews:drills"))
        self.assertEqual(response.status_code, 200)
