import datetime
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from core.completion import mark_task_complete, studied_video_ids
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from reviews.daily_plan import _media_item
from video_study.models import ChannelVideo
from video_study.services import (
    DREAMING_SPANISH,
    KURZGESAGT_DE,
    get_channel_video_of_day,
    get_kurzgesagt_video_of_day,
)

ES = Deck.LanguageMode.ES_RU
DE = Deck.LanguageMode.DE_RU


def make_catalog(channel, count):
    now = timezone.now()
    ids = [f"vid{index:08d}" for index in range(count)]
    for youtube_id in ids:
        ChannelVideo.objects.create(
            channel_key=channel.key,
            youtube_id=youtube_id,
            title=f"Title {youtube_id}",
            source_url=f"https://www.youtube.com/watch?v={youtube_id}",
            synced_at=now,
        )
    return ids


class StudiedVideoIdsTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.yesterday = self.today - datetime.timedelta(days=1)

    def test_reads_the_video_labels_of_earlier_days(self):
        mark_task_complete(self.user, "media", ES, "video:abcdefghijk", date=self.yesterday)

        self.assertEqual(studied_video_ids(self.user, ES, self.today), {"abcdefghijk"})

    def test_a_mark_of_today_is_not_before_today(self):
        mark_task_complete(self.user, "media", ES, "video:abcdefghijk", date=self.today)

        self.assertEqual(studied_video_ids(self.user, ES, self.today), set())
        self.assertEqual(
            studied_video_ids(self.user, ES, self.today + datetime.timedelta(days=1)),
            {"abcdefghijk"},
        )

    def test_marks_of_another_user_or_another_mode_exclude_nothing(self):
        other = create_user("other")
        mark_task_complete(other, "media", ES, "video:abcdefghijk", date=self.yesterday)
        mark_task_complete(self.user, "media", DE, "video:lmnopqrstuv", date=self.yesterday)

        self.assertEqual(studied_video_ids(self.user, ES, self.today), set())

    def test_a_podcast_label_excludes_nothing(self):
        mark_task_complete(self.user, "media", DE, "podcast:12", date=self.yesterday)
        mark_task_complete(self.user, "media", ES, "", date=self.yesterday - datetime.timedelta(1))

        self.assertEqual(studied_video_ids(self.user, DE, self.today), set())
        self.assertEqual(studied_video_ids(self.user, ES, self.today), set())

    def test_an_unsupported_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            studied_video_ids(self.user, "XX-YY", self.today)


class VideoOfTheDayExclusionTests(AuthedTestCase):
    def test_excluded_ids_are_never_picked_over_a_run_longer_than_the_catalog(self):
        ids = make_catalog(DREAMING_SPANISH, 5)
        excluded = frozenset(ids[:2])
        now = timezone.now()

        picks = {
            get_channel_video_of_day(
                DREAMING_SPANISH, now=now + datetime.timedelta(days=day), exclude_ids=excluded
            ).youtube_id
            for day in range(20)
        }

        self.assertEqual(picks, set(ids[2:]))

    def test_the_default_excludes_nothing(self):
        ids = make_catalog(DREAMING_SPANISH, 3)
        now = timezone.now()

        picks = {
            get_channel_video_of_day(
                DREAMING_SPANISH, now=now + datetime.timedelta(days=day)
            ).youtube_id
            for day in range(12)
        }

        self.assertEqual(picks, set(ids))

    def test_nothing_is_left_when_every_video_is_excluded(self):
        ids = make_catalog(DREAMING_SPANISH, 2)

        self.assertIsNone(get_channel_video_of_day(DREAMING_SPANISH, exclude_ids=frozenset(ids)))

    def test_the_kurzgesagt_helper_takes_the_same_parameter(self):
        ids = make_catalog(KURZGESAGT_DE, 4)
        now = timezone.now()

        picks = {
            get_kurzgesagt_video_of_day(
                now=now + datetime.timedelta(days=day), exclude_ids=frozenset(ids[:3])
            ).youtube_id
            for day in range(8)
        }

        self.assertEqual(picks, {ids[3]})


class MediaItemStudiedVideosTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.ids = make_catalog(DREAMING_SPANISH, 4)

    def media_on(self, offset_days, mode=ES):
        day = self.today + datetime.timedelta(days=offset_days)
        moment = timezone.now() + datetime.timedelta(days=offset_days)
        with patch("django.utils.timezone.now", return_value=moment):
            return _media_item(self.user, mode, day)

    def test_a_video_marked_done_yesterday_never_comes_back(self):
        mark_task_complete(self.user, "media", ES, f"video:{self.ids[0]}", date=self.yesterday)

        picks = {self.media_on(day)["video"].youtube_id for day in range(0, 16)}

        self.assertEqual(picks, set(self.ids[1:]))

    def test_every_day_of_a_long_run_studies_a_new_video_until_none_is_left(self):
        seen = []
        for day in range(len(self.ids) + 2):
            media = self.media_on(day)
            if media["video"] is None:
                self.assertTrue(media["all_videos_studied"])
                break
            seen.append(media["video"].youtube_id)
            mark_task_complete(
                self.user,
                "media",
                ES,
                f"video:{media['video'].youtube_id}",
                date=self.today + datetime.timedelta(days=day),
            )
        self.assertCountEqual(seen, self.ids)

    def test_a_video_marked_done_today_is_still_todays_video(self):
        before = self.media_on(0)["video"]
        mark_task_complete(self.user, "media", ES, f"video:{before.youtube_id}", date=self.today)

        after = self.media_on(0)

        self.assertEqual(after["video"], before)
        self.assertTrue(after["is_done"])
        self.assertFalse(after["all_videos_studied"])

    def test_marks_of_another_user_or_another_mode_exclude_nothing(self):
        baseline = {self.media_on(day)["video"].youtube_id for day in range(8)}
        other = create_user("other")
        mark_task_complete(other, "media", ES, f"video:{self.ids[0]}", date=self.yesterday)
        mark_task_complete(self.user, "media", DE, f"video:{self.ids[1]}", date=self.yesterday)

        picks = {self.media_on(day)["video"].youtube_id for day in range(8)}

        self.assertEqual(picks, baseline)

    def test_a_podcast_label_excludes_nothing(self):
        mark_task_complete(self.user, "media", ES, "podcast:3", date=self.yesterday)

        picks = {self.media_on(day)["video"].youtube_id for day in range(16)}

        self.assertEqual(picks, set(self.ids))

    def test_with_every_video_studied_there_is_no_video_and_the_flag_is_set(self):
        for index, youtube_id in enumerate(self.ids, start=1):
            day = self.yesterday - datetime.timedelta(days=index)
            mark_task_complete(self.user, "media", ES, f"video:{youtube_id}", date=day)

        media = self.media_on(0)

        self.assertIsNone(media["video"])
        self.assertIsNone(media["video_url"])
        self.assertTrue(media["all_videos_studied"])

    def test_an_empty_catalog_is_not_all_studied(self):
        ChannelVideo.objects.all().delete()

        media = self.media_on(0)

        self.assertIsNone(media["video"])
        self.assertFalse(media["all_videos_studied"])


class DashboardStudiedVideosTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = ES
        session.save()
        self.today = timezone.localdate()
        self.ids = make_catalog(DREAMING_SPANISH, 2)

    def mark_all_studied(self):
        for index, youtube_id in enumerate(self.ids, start=1):
            day = self.today - datetime.timedelta(days=index)
            mark_task_complete(self.user, "media", ES, f"video:{youtube_id}", date=day)

    def test_the_dashboard_says_every_video_is_studied_and_links_to_video_study(self):
        self.mark_all_studied()

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Every video of the channel is studied")
        self.assertContains(response, f'href="{reverse("video_study:workspace")}"')
        self.assertNotContains(response, "Open a captioned video or podcast")
        self.assertNotContains(response, ">Watch</a>")

    def test_an_empty_catalog_keeps_the_old_wording(self):
        ChannelVideo.objects.all().delete()

        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, "Open a captioned video or podcast")
        self.assertNotContains(response, "Every video of the channel is studied")

    def test_with_videos_left_the_dashboard_shows_one_with_a_watch_link(self):
        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, ">Watch</a>")
        self.assertNotContains(response, "Every video of the channel is studied")

    def test_the_dashboard_keeps_showing_the_video_just_finished(self):
        video = self.client.get(reverse("core:dashboard")).context["daily_plan"]["media"]["video"]
        mark_task_complete(self.user, "media", ES, f"video:{video.youtube_id}")

        again = self.client.get(reverse("core:dashboard")).context["daily_plan"]["media"]

        self.assertEqual(again["video"], video)
        self.assertTrue(again["is_done"])
