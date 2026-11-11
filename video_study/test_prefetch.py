import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.rotation import rotating_choice
from video_study import services
from video_study.models import ChannelVideo, TranscriptVideo
from video_study.services import (
    _BLOCKED_CONNECTION_MESSAGE,
    ChannelUnavailable,
    TranscriptUnavailable,
    prefetch_kurzgesagt_transcripts,
    start_kurzgesagt_prefetch,
)

TRANSCRIPT = [{"text": "Hallo.", "start": 0.0, "duration": 1.0}]


def _channel_video(youtube_id, synced_at):
    return ChannelVideo.objects.create(
        channel_key="kurzgesagt-de",
        youtube_id=youtube_id,
        title=f"Video {youtube_id}",
        source_url=f"https://www.youtube.com/watch?v={youtube_id}",
        synced_at=synced_at,
    )


class TranscriptPrefetchGateTests(SimpleTestCase):
    def test_gate_is_disabled_for_the_whole_test_suite(self):
        self.assertFalse(settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH)


class PrefetchServiceTests(TestCase):
    def test_stale_catalog_is_refreshed(self):
        now = timezone.now()
        old = now - datetime.timedelta(hours=48)
        _channel_video("aaaaaaaaaaa", old)

        with (
            patch(
                "video_study.services.fetch_channel_videos",
                return_value=[
                    {
                        "youtube_id": "aaaaaaaaaaa",
                        "title": "Video aaaaaaaaaaa",
                        "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                        "thumbnail_url": "",
                        "published_at": now,
                    }
                ],
            ) as fetch_channel_videos,
            patch(
                "video_study.services.fetch_transcript", return_value=TRANSCRIPT
            ) as fetch_transcript,
        ):
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_channel_videos.assert_called_once()
        fetch_transcript.assert_called_once_with("aaaaaaaaaaa", languages=("de",))
        self.assertTrue(summary["catalog_refreshed"])
        self.assertEqual(summary["fetched"], 1)

    def test_fresh_catalog_is_not_refreshed(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)

        with (
            patch("video_study.services.fetch_channel_videos") as fetch_channel_videos,
            patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT),
        ):
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_channel_videos.assert_not_called()
        self.assertFalse(summary["catalog_refreshed"])

    def test_only_uncached_videos_are_fetched(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        _channel_video("bbbbbbbbbbb", now)
        TranscriptVideo.objects.create(
            youtube_id="aaaaaaaaaaa",
            source_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            source_language="de",
            transcript=TRANSCRIPT,
        )

        with patch(
            "video_study.services.fetch_transcript", return_value=TRANSCRIPT
        ) as fetch_transcript:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_transcript.assert_called_once_with("bbbbbbbbbbb", languages=("de",))
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["remaining"], 0)

    def test_cached_videos_are_left_untouched(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        cached = TranscriptVideo.objects.create(
            youtube_id="aaaaaaaaaaa",
            source_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            source_language="de",
            transcript=TRANSCRIPT,
        )
        original_updated_at = cached.updated_at

        with patch("video_study.services.fetch_transcript") as fetch_transcript:
            prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_transcript.assert_not_called()
        cached.refresh_from_db()
        self.assertEqual(cached.updated_at, original_updated_at)

    def test_one_failure_does_not_stop_the_run(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        _channel_video("bbbbbbbbbbb", now)

        def fake_fetch(video_id, languages):
            if video_id == "aaaaaaaaaaa":
                raise TranscriptUnavailable("No German captions are available for this video.")
            return TRANSCRIPT

        with patch(
            "video_study.services.fetch_transcript", side_effect=fake_fetch
        ) as fetch_transcript:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        self.assertEqual(fetch_transcript.call_count, 2)
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_blocked_connection_stops_the_run(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        _channel_video("bbbbbbbbbbb", now)

        with patch(
            "video_study.services.fetch_transcript",
            side_effect=TranscriptUnavailable(_BLOCKED_CONNECTION_MESSAGE),
        ) as fetch_transcript:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_transcript.assert_called_once()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["remaining"], 1)

    def test_channel_failure_still_prefetches_the_existing_catalog(self):
        now = timezone.now()
        old = now - datetime.timedelta(hours=48)
        _channel_video("aaaaaaaaaaa", old)

        with (
            patch(
                "video_study.services.fetch_channel_videos",
                side_effect=ChannelUnavailable("down"),
            ),
            patch(
                "video_study.services.fetch_transcript", return_value=TRANSCRIPT
            ) as fetch_transcript,
        ):
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_transcript.assert_called_once_with("aaaaaaaaaaa", languages=("de",))
        self.assertFalse(summary["catalog_refreshed"])
        self.assertEqual(summary["fetched"], 1)

    def test_nothing_to_do_makes_no_network_call(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        TranscriptVideo.objects.create(
            youtube_id="aaaaaaaaaaa",
            source_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            source_language="de",
            transcript=TRANSCRIPT,
        )

        with (
            patch("video_study.services.fetch_channel_videos") as fetch_channel_videos,
            patch("video_study.services.fetch_transcript") as fetch_transcript,
        ):
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)

        fetch_channel_videos.assert_not_called()
        fetch_transcript.assert_not_called()
        self.assertEqual(summary["fetched"], 0)
        self.assertEqual(summary["remaining"], 0)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=2)
    def test_cap_counts_failed_attempts_and_keeps_remaining_accurate(self):
        now = timezone.now()
        for video_id in ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]:
            _channel_video(video_id, now)
        with (
            patch(
                "video_study.services.fetch_transcript",
                side_effect=[TranscriptUnavailable("No captions"), TRANSCRIPT],
            ) as fetch,
            patch("video_study.services.time.sleep") as sleep,
        ):
            summary = prefetch_kurzgesagt_transcripts(pause=2, now=now)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(
            summary, {"catalog_refreshed": False, "fetched": 1, "failed": 1, "remaining": 2}
        )

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=0)
    def test_zero_cap_still_refreshes_catalog_without_caption_requests(self):
        now = timezone.now()
        with (
            patch(
                "video_study.services.fetch_channel_videos",
                return_value=[
                    {
                        "youtube_id": "aaaaaaaaaaa",
                        "title": "Video",
                        "source_url": "https://youtu.be/aaaaaaaaaaa",
                        "thumbnail_url": "",
                        "published_at": now,
                    }
                ],
            ) as refresh,
            patch("video_study.services.fetch_transcript") as fetch,
            patch("video_study.services.time.sleep") as sleep,
        ):
            summary = prefetch_kurzgesagt_transcripts(now=now)
        refresh.assert_called_once()
        fetch.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(
            summary, {"catalog_refreshed": True, "fetched": 0, "failed": 0, "remaining": 1}
        )

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=1)
    def test_cap_is_per_run_and_cached_videos_do_not_consume_it(self):
        now = timezone.now()
        for video_id in ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]:
            _channel_video(video_id, now)
        with patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT) as fetch:
            first = prefetch_kurzgesagt_transcripts(pause=0, now=now)
            second = prefetch_kurzgesagt_transcripts(pause=0, now=now)
        catalog_ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
        today = timezone.localdate(now)
        expected = [
            rotating_choice(
                catalog_ids, today + datetime.timedelta(days=offset), salt="kurzgesagt-de"
            )
            for offset in range(2)
        ]
        self.assertEqual([call.args[0] for call in fetch.call_args_list], expected)
        self.assertEqual(first["remaining"], 2)
        self.assertEqual(second["remaining"], 1)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=1)
    def test_each_channel_has_its_own_run_cap(self):
        now = timezone.now()
        for video_id in ["aaaaaaaaaaa", "bbbbbbbbbbb"]:
            _channel_video(video_id, now)
            ChannelVideo.objects.create(
                channel_key=services.NL_CHANNEL.key,
                youtube_id=video_id,
                title="Dutch",
                source_url=f"https://youtu.be/{video_id}",
                synced_at=now,
            )
        with patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT) as fetch:
            german = prefetch_kurzgesagt_transcripts(pause=0, now=now)
            dutch = services.prefetch_channel_transcripts(services.NL_CHANNEL, pause=0, now=now)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(german["fetched"], 1)
        self.assertEqual(dutch["fetched"], 1)
        self.assertEqual(german["remaining"], 1)
        self.assertEqual(dutch["remaining"], 1)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=1)
    def test_todays_video_is_first_even_when_not_lowest_sorted(self):
        catalog_ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]
        # Pick a fixed schedule day where the daily recommendation is not the
        # lexicographic first item, rather than depending on the date of the run.
        day = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.UTC)
        for offset in range(len(catalog_ids)):
            candidate = day + datetime.timedelta(days=offset)
            if (
                rotating_choice(catalog_ids, timezone.localdate(candidate), salt="kurzgesagt-de")
                != catalog_ids[0]
            ):
                day = candidate
                break
        for video_id in catalog_ids:
            _channel_video(video_id, day)
        expected = services.get_kurzgesagt_video_of_day(now=day).youtube_id
        self.assertNotEqual(expected, catalog_ids[0])
        with patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT) as fetch:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=day)
        fetch.assert_called_once_with(expected, languages=("de",))
        self.assertEqual(summary["remaining"], 3)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=2)
    def test_cached_today_is_skipped_using_the_full_catalog_schedule(self):
        now = timezone.now()
        catalog_ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]
        for video_id in catalog_ids:
            _channel_video(video_id, now)
        today = timezone.localdate(now)
        schedule = [
            rotating_choice(
                catalog_ids, today + datetime.timedelta(days=offset), salt="kurzgesagt-de"
            )
            for offset in range(len(catalog_ids))
        ]
        TranscriptVideo.objects.create(
            youtube_id=schedule[0],
            source_language="de",
            source_url=f"https://youtu.be/{schedule[0]}",
            transcript=TRANSCRIPT,
        )
        with patch("video_study.services.fetch_transcript", return_value=TRANSCRIPT) as fetch:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], schedule[1:3])
        self.assertEqual(summary["remaining"], 1)

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=2)
    def test_persistent_lowest_sorted_failures_do_not_starve_future_days(self):
        now = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.UTC)
        catalog_ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]
        for video_id in catalog_ids:
            _channel_video(video_id, now)
        failing = set(catalog_ids[:2])

        def fetch_captions(video_id, languages):
            if video_id in failing:
                raise TranscriptUnavailable("No German captions are available for this video.")
            return TRANSCRIPT

        with (
            patch("video_study.services.is_catalog_fresh", return_value=True),
            patch("video_study.services.fetch_transcript", side_effect=fetch_captions) as fetch,
        ):
            for offset in range(len(catalog_ids)):
                day = now + datetime.timedelta(days=offset)
                before = fetch.call_count
                summary = prefetch_kurzgesagt_transcripts(pause=0, now=day)
                attempted = fetch.call_args_list[before:]
                self.assertLessEqual(len(attempted), 2)
                self.assertEqual(summary["fetched"] + summary["failed"], len(attempted))
                self.assertEqual(
                    summary["remaining"], 4 - TranscriptVideo.objects.count() - summary["failed"]
                )
        cached = set(TranscriptVideo.objects.values_list("youtube_id", flat=True))
        self.assertEqual(cached, set(catalog_ids) - failing)
        self.assertTrue(failing.isdisjoint(cached))

    @override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS=0)
    def test_zero_cap_does_not_compute_rotation(self):
        now = timezone.now()
        _channel_video("aaaaaaaaaaa", now)
        with patch("video_study.services.rotating_choice") as rotation:
            summary = prefetch_kurzgesagt_transcripts(pause=0, now=now)
        rotation.assert_not_called()
        self.assertEqual(summary["remaining"], 1)


class ThreadTriggerTests(TestCase):
    def setUp(self):
        super().setUp()
        services._last_prefetch_date = None

    def test_disabled_setting_starts_nothing(self):
        with (
            override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH=False),
            patch("video_study.services.threading.Thread") as thread,
        ):
            start_kurzgesagt_prefetch()

        thread.assert_not_called()

    def test_enabled_setting_starts_once_per_day_and_not_while_running(self):
        with (
            override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH=True),
            patch("video_study.services.threading.Thread") as thread,
        ):
            start_kurzgesagt_prefetch()
            start_kurzgesagt_prefetch()

        thread.assert_called_once()
        _args, kwargs = thread.call_args
        self.assertTrue(kwargs["daemon"])
