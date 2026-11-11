import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.completion import mark_task_complete, task_is_complete
from core.models import DailyTaskCompletion
from core.testutils import AuthedTestCase, create_user
from video_study.models import PodcastEpisode, TranscriptVideo


class CompletionHelperTests(TestCase):
    def setUp(self):
        self.user = create_user("completer")
        self.other = create_user("someone-else")

    def test_nothing_is_complete_by_default(self):
        self.assertFalse(task_is_complete(self.user, "grammar", "DE-RU"))
        self.assertFalse(task_is_complete(self.user, "media", "DE-RU"))

    def test_marking_completes_only_that_task_mode_and_owner(self):
        mark_task_complete(self.user, "grammar", "DE-RU", item_label="modal-verbs")

        self.assertTrue(task_is_complete(self.user, "grammar", "DE-RU"))
        self.assertFalse(task_is_complete(self.user, "media", "DE-RU"))
        self.assertFalse(task_is_complete(self.user, "grammar", "NL-EN"))
        self.assertFalse(task_is_complete(self.other, "grammar", "DE-RU"))

    def test_completion_is_per_local_date(self):
        yesterday = datetime.date(2026, 9, 30)
        mark_task_complete(self.user, "media", "DE-RU", date=yesterday)

        self.assertTrue(task_is_complete(self.user, "media", "DE-RU", date=yesterday))
        self.assertFalse(task_is_complete(self.user, "media", "DE-RU"))

    def test_repeat_marks_are_idempotent_and_keep_the_first_label(self):
        first, created = mark_task_complete(self.user, "grammar", "DE-RU", item_label="first")
        second, created_again = mark_task_complete(
            self.user, "grammar", "DE-RU", item_label="second"
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DailyTaskCompletion.objects.count(), 1)
        self.assertEqual(DailyTaskCompletion.objects.get().item_label, "first")

    def test_item_label_is_truncated_to_the_column_width(self):
        completion, _ = mark_task_complete(self.user, "media", "DE-RU", item_label="x" * 500)
        self.assertEqual(len(completion.item_label), 240)

    def test_unknown_task_or_mode_is_rejected(self):
        for task, mode in [("grammar", "FR-EN"), ("", "DE-RU")]:
            with self.subTest(task=task, mode=mode):
                with self.assertRaises(ValueError):
                    mark_task_complete(self.user, task, mode)
                with self.assertRaises(ValueError):
                    task_is_complete(self.user, task, mode)
        self.assertEqual(DailyTaskCompletion.objects.count(), 0)


def _set_mode(client, mode):
    session = client.session
    session["language_mode"] = mode
    session.save()


class MediaCompletionViewTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.video = TranscriptVideo.objects.create(
            youtube_id="abcdefghijk",
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
            source_language="de",
            transcript=[{"start": 0, "duration": 2, "text": "Hallo Welt"}],
        )
        self.episode = PodcastEpisode.objects.create(
            source_url="https://example.invalid/episode-1",
            youtube_id="zyxwvutsrqp",
            title="Episode one",
            transcript=[{"start": 0, "duration": 2, "text": "Guten Tag"}],
            synced_at=timezone.now(),
        )

    def post(self, **data):
        return self.client.post(reverse("core:complete_media"), data)

    def video_post(self, **overrides):
        return self.post(**{"kind": "video", "video": "abcdefghijk", "lang": "de", **overrides})

    def test_cached_video_marks_media_and_returns_to_the_video(self):
        response = self.video_post()
        self.assertRedirects(
            response,
            f"{reverse('video_study:workspace')}?video=abcdefghijk&lang=de",
            fetch_redirect_response=False,
        )
        self.assertTrue(task_is_complete(self.user, "media", "DE-RU"))
        self.assertEqual(DailyTaskCompletion.objects.get().item_label, "video:abcdefghijk")

    def test_cached_podcast_marks_media_and_returns_to_the_episode(self):
        response = self.post(kind="podcast", episode=str(self.episode.pk))
        self.assertRedirects(
            response,
            f"{reverse('podcasts:workspace')}?episode={self.episode.pk}",
            fetch_redirect_response=False,
        )
        self.assertTrue(task_is_complete(self.user, "media", "DE-RU"))
        self.assertEqual(DailyTaskCompletion.objects.get().item_label, f"podcast:{self.episode.pk}")

    def test_video_or_episode_missing_from_the_cache_is_rejected_without_fetching(self):
        with mock.patch("video_study.views.fetch_transcript") as fetch:
            self.assertEqual(self.video_post(video="zzzzzzzzzzz").status_code, 404)
            self.assertEqual(self.video_post(lang="en").status_code, 404)
            self.assertEqual(self.post(kind="podcast", episode="999999").status_code, 404)
            self.assertEqual(self.post(kind="podcast", episode="abc").status_code, 404)
        fetch.assert_not_called()
        self.assertEqual(DailyTaskCompletion.objects.count(), 0)

    def test_entries_without_a_transcript_cannot_be_completed(self):
        self.video.transcript = []
        self.video.save()
        self.episode.transcript = []
        self.episode.save()
        self.assertEqual(self.video_post().status_code, 404)
        self.assertEqual(self.post(kind="podcast", episode=str(self.episode.pk)).status_code, 404)
        self.assertEqual(DailyTaskCompletion.objects.count(), 0)

    def test_malformed_and_oversized_episode_ids_never_error(self):
        huge = "9" * 5000
        for value in (
            huge,
            "9" * 30,
            "9223372036854775808",  # one past SQLite's maximum integer
            "9999999999999999999",
            "0",
            "-1",
            "１２３",
            "²",
            " 1",
            "1\n",
            "1.5",
            "",
        ):
            with self.subTest(episode=value[:20]):
                self.assertEqual(self.post(kind="podcast", episode=value).status_code, 404)
        self.assertEqual(DailyTaskCompletion.objects.count(), 0)

    def test_episode_id_at_the_sqlite_maximum_is_looked_up_without_error(self):
        top = 2**63 - 1
        self.assertEqual(self.post(kind="podcast", episode=str(top)).status_code, 404)
        PodcastEpisode.objects.create(
            pk=top,
            source_url="https://example.invalid/top",
            youtube_id="topepisode1",
            title="Top",
            transcript=[{"start": 0, "duration": 1, "text": "Hallo"}],
            synced_at=timezone.now(),
        )
        with mock.patch("video_study.views.fetch_transcript") as fetch:
            response = self.post(kind="podcast", episode=str(top))
        fetch.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(task_is_complete(self.user, "media", "DE-RU"))

    def test_oversized_video_fields_are_rejected_cleanly(self):
        self.assertEqual(self.video_post(video="a" * 5000).status_code, 404)
        self.assertEqual(self.video_post(lang="d" * 5000).status_code, 400)

    def test_unknown_kind_is_rejected(self):
        self.assertEqual(self.post(kind="radio").status_code, 400)
        self.assertEqual(self.post().status_code, 400)

    def test_dutch_mode_accepts_dutch_captions_and_has_no_podcasts(self):
        _set_mode(self.client, "NL-EN")
        self.assertEqual(self.video_post().status_code, 400)  # German captions in Dutch mode
        self.assertEqual(self.post(kind="podcast", episode=str(self.episode.pk)).status_code, 400)
        TranscriptVideo.objects.create(
            youtube_id="nlvideo0001",
            source_url="https://www.youtube.com/watch?v=nlvideo0001",
            source_language="nl",
            transcript=[{"start": 0, "duration": 1, "text": "Hallo"}],
        )
        response = self.video_post(video="nlvideo0001", lang="nl")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(task_is_complete(self.user, "media", "NL-EN"))
        self.assertFalse(task_is_complete(self.user, "media", "DE-RU"))

    def test_next_parameter_is_ignored(self):
        response = self.video_post(next="https://evil.example/")
        self.assertTrue(response["Location"].startswith("/videos/"))

    def test_login_csrf_and_method_are_enforced(self):
        anonymous = self.client_class()
        response = anonymous.post(reverse("core:complete_media"), {"kind": "video"})
        self.assertIn(reverse("login"), response["Location"])

        strict = self.client_class(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post(
            reverse("core:complete_media"),
            {"kind": "video", "video": "abcdefghijk", "lang": "de"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("core:complete_media")).status_code, 405)
        self.assertEqual(DailyTaskCompletion.objects.count(), 0)

    def test_workspaces_show_the_control_and_its_completed_state(self):
        video_url = f"{reverse('video_study:workspace')}?video=abcdefghijk&lang=de"
        podcast_url = f"{reverse('podcasts:workspace')}?episode={self.episode.pk}"
        with mock.patch("video_study.views.fetch_transcript") as fetch:
            for url in (video_url, podcast_url):
                self.assertContains(self.client.get(url), "Mark media done for today")
            self.video_post()
            for url in (video_url, podcast_url):
                response = self.client.get(url)
                self.assertContains(response, "Media done today")
                self.assertNotContains(response, "Mark media done for today")
        fetch.assert_not_called()

    def test_workspaces_are_private_and_not_stored(self):
        for url in (
            f"{reverse('video_study:workspace')}?video=abcdefghijk&lang=de",
            f"{reverse('podcasts:workspace')}?episode={self.episode.pk}",
        ):
            directives = {part.strip() for part in self.client.get(url)["Cache-Control"].split(",")}
            self.assertLessEqual({"private", "no-store"}, directives)

    def test_workspace_without_a_loaded_video_has_no_completion_control(self):
        response = self.client.get(reverse("video_study:workspace"))
        self.assertNotContains(response, "data-media-complete")
