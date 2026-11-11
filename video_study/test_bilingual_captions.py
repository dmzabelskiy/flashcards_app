from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse

from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study.models import TranscriptVideo
from video_study.services import TranscriptUnavailable, fetch_english_captions

VIDEO_ID = "dQw4w9WgXcQ"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
DUTCH = [{"text": "Hallo wereld.", "start": 0, "duration": 2}]
ENGLISH = [
    {"text": "Hello", "start": 0, "duration": 1},
    {"text": "world.", "start": 1, "duration": 1},
]
ENGLISH_DATA = {
    "transcript": ENGLISH,
    "caption_origin": "manual",
    "caption_language": "en-US",
}


def track(code, generated=False, text="Hello", translatable=False, translations=()):
    result = Mock(
        language_code=code,
        is_generated=generated,
        is_translatable=translatable,
        translation_languages=[{"language_code": language} for language in translations],
    )
    result.fetch.return_value.to_raw_data.return_value = [{"text": text, "start": 0, "duration": 2}]
    return result


class EnglishCaptionServiceTests(SimpleTestCase):
    def setUp(self):
        # Any unexpected request is a test failure, never a live YouTube/provider call.
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network"))
        network.start()
        self.addCleanup(network.stop)
        api = patch("youtube_transcript_api.YouTubeTranscriptApi")
        self.api = api.start()
        self.addCleanup(api.stop)
        provider = patch("video_study.services.request_translation")
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def fetch(self, tracks):
        self.api.return_value.list.return_value = tracks
        result = fetch_english_captions(VIDEO_ID)
        self.provider.assert_not_called()
        return result

    def test_native_manual_preferred_over_generated_and_translation(self):
        manual = track("en", text="Manual")
        dutch = track("nl", translatable=True, translations=("en",))
        result = self.fetch([track("en", generated=True), dutch, manual])
        self.assertEqual(result["transcript"][0]["text"], "Manual")
        self.assertEqual(result["caption_origin"], "manual")
        dutch.translate.assert_not_called()

    def test_regional_manual_preferred_over_exact_generated(self):
        result = self.fetch([track("en", generated=True), track("en-GB", text="Regional")])
        self.assertEqual(result["caption_language"], "en-GB")
        self.assertEqual(result["caption_origin"], "manual")
        self.assertEqual(result["transcript"][0]["text"], "Regional")

    def test_generated_track_has_accurate_provenance(self):
        result = self.fetch([track("en-US", generated=True)])
        self.assertEqual(result["caption_origin"], "generated")

    def test_translates_selected_dutch_track_with_youtube(self):
        generated = track("nl", generated=True, translatable=True, translations=("en",))
        dutch = track("nl-NL", translatable=True, translations=("en",))
        dutch.translate.return_value = track("en", generated=True, text="YouTube translated")
        result = self.fetch([generated, dutch])
        dutch.translate.assert_called_once_with("en")
        generated.translate.assert_not_called()
        self.assertEqual(result["caption_origin"], "translated")
        self.assertEqual(result["transcript"][0]["text"], "YouTube translated")

    def test_regional_translation_supported(self):
        dutch = track("nl", translatable=True, translations=("fr", "en-US"))
        dutch.translate.return_value = track("en-US", generated=True)
        result = self.fetch([dutch])
        dutch.translate.assert_called_once_with("en-US")
        self.assertEqual(result["caption_language"], "en-US")

    def test_no_english_or_translation_reports_unavailable(self):
        for dutch in [track("nl"), track("nl", translatable=True, translations=("fr",))]:
            with self.subTest(dutch=dutch):
                self.api.return_value.list.return_value = [dutch, track("eng")]
                with self.assertRaises(TranscriptUnavailable):
                    fetch_english_captions(VIDEO_ID)
                dutch.translate.assert_not_called()
        self.provider.assert_not_called()

    def test_list_fetch_and_translation_errors_are_safe(self):
        self.api.return_value.list.side_effect = RuntimeError("List failed")
        with self.assertRaises(TranscriptUnavailable):
            fetch_english_captions(VIDEO_ID)
        self.api.return_value.list.side_effect = None
        native = track("en")
        native.fetch.side_effect = RuntimeError("Fetch failed")
        self.api.return_value.list.return_value = [native]
        with self.assertRaises(TranscriptUnavailable):
            fetch_english_captions(VIDEO_ID)
        dutch = track("nl", translatable=True, translations=("en",))
        dutch.translate.side_effect = RuntimeError("Translate failed")
        self.api.return_value.list.return_value = [dutch]
        with self.assertRaises(TranscriptUnavailable):
            fetch_english_captions(VIDEO_ID)

    def test_empty_secondary_track_is_unavailable(self):
        native = track("en", text=" ")
        self.api.return_value.list.return_value = [native]
        with self.assertRaisesMessage(TranscriptUnavailable, "empty transcript"):
            fetch_english_captions(VIDEO_ID)

    def test_malformed_secondary_timestamps_become_a_safe_fetch_error(self):
        native = track("en")
        self.api.return_value.list.return_value = [native]
        for start in ["invalid", float("nan"), float("inf")]:
            native.fetch.return_value.to_raw_data.return_value = [
                {"text": "Hello", "start": start, "duration": 2}
            ]
            with self.subTest(start=start), self.assertRaises(TranscriptUnavailable):
                fetch_english_captions(VIDEO_ID)


class BilingualWorkspaceTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()
        self.url = reverse("video_study:workspace")
        self.dutch = TranscriptVideo.objects.create(
            youtube_id=VIDEO_ID, source_url=SOURCE_URL, source_language="nl", transcript=DUTCH
        )
        for target, value in [
            ("video_study.views.fetch_english_captions", ENGLISH_DATA),
            ("video_study.views.fetch_transcript", DUTCH),
        ]:
            mock_patch = patch(target, return_value=value)
            mocked = mock_patch.start()
            self.addCleanup(mock_patch.stop)
            if target.endswith("fetch_transcript"):
                self.fetch_dutch = mocked
            else:
                self.fetch_english = mocked
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network"))
        network.start()
        self.addCleanup(network.stop)

    def post(self, **extra):
        return self.client.post(self.url, {"url": SOURCE_URL, "source_language": "nl", **extra})

    def cache_english(self, **extra):
        return TranscriptVideo.objects.create(
            youtube_id=VIDEO_ID,
            source_url=SOURCE_URL,
            source_language="en",
            **{**ENGLISH_DATA, **extra},
        )

    def test_default_get_and_load_do_not_fetch_english_even_when_cached(self):
        self.cache_english()
        page = self.client.get(self.url, {"video": VIDEO_ID})
        self.assertFalse(page.context["bilingual"])
        self.assertNotContains(page, "video-english-transcript-data")
        self.assertEqual(self.post().status_code, 302)
        self.fetch_english.assert_not_called()
        self.fetch_dutch.assert_not_called()

    def test_opt_in_fetches_separate_cache_and_preserves_on_cached_navigation(self):
        page = self.post(bilingual="on")
        self.assertEqual(page.status_code, 302)
        self.assertIn("captions=bilingual", page["Location"])
        self.fetch_english.assert_called_once_with(VIDEO_ID)
        english = TranscriptVideo.objects.get(youtube_id=VIDEO_ID, source_language="en")
        self.assertEqual(english.caption_language, "en-US")
        self.assertEqual(english.caption_origin, "manual")
        self.dutch.refresh_from_db()
        self.assertEqual(self.dutch.transcript, DUTCH)
        for query in [{"video": VIDEO_ID}, {"video": VIDEO_ID, "captions": "bilingual"}]:
            page = self.client.get(self.url, query)
            self.assertTrue(page.context["bilingual"])
            self.assertContains(page, "video-english-transcript-data")
            self.assertContains(page, 'id="current-dutch-caption"')
            self.assertTrue(page.context["form"]["bilingual"].value())
        self.post(bilingual="on")
        self.fetch_english.assert_called_once()

    def test_choice_survives_navigation_to_another_cached_video(self):
        self.post(bilingual="on")
        TranscriptVideo.objects.create(
            youtube_id="aaaaaaaaaaa",
            source_url="https://youtu.be/aaaaaaaaaaa",
            source_language="nl",
            transcript=DUTCH,
        )
        page = self.client.get(self.url, {"video": "aaaaaaaaaaa"})
        self.assertTrue(page.context["bilingual"])
        self.fetch_english.assert_called_with("aaaaaaaaaaa")
        self.fetch_dutch.assert_not_called()

    def test_opt_in_load_fetches_uncached_dutch_and_english_once(self):
        self.dutch.delete()
        page = self.post(bilingual="on")
        self.assertEqual(page.status_code, 302)
        page = self.client.get(page["Location"])
        self.assertEqual(page.context["transcript"], DUTCH)
        self.assertEqual(page.context["english_transcript"], ENGLISH)
        self.fetch_dutch.assert_called_once_with(VIDEO_ID, languages=("nl",))
        self.fetch_english.assert_called_once_with(VIDEO_ID)

    def test_refresh_updates_both_tracks_only_if_opted_in(self):
        self.cache_english()
        self.post(bilingual="on", refresh="1")
        self.fetch_dutch.assert_called_once_with(VIDEO_ID, languages=("nl",))
        self.fetch_english.assert_called_once_with(VIDEO_ID)
        self.fetch_dutch.reset_mock()
        self.fetch_english.reset_mock()
        self.post(refresh="1")
        self.fetch_dutch.assert_called_once()
        self.fetch_english.assert_not_called()

    def test_explicit_get_choice_and_disable_override_session(self):
        self.cache_english()
        page = self.client.get(self.url, {"video": VIDEO_ID, "captions": "bilingual"})
        self.assertTrue(page.context["bilingual"])
        page = self.client.get(self.url, {"video": VIDEO_ID, "captions": "dutch"})
        self.assertFalse(page.context["bilingual"])
        page = self.client.get(self.url, {"video": VIDEO_ID})
        self.assertFalse(page.context["bilingual"])
        self.fetch_english.assert_not_called()

    def test_secondary_error_leaves_dutch_usable_and_retry_only_fetches_english(self):
        self.fetch_english.side_effect = TranscriptUnavailable("No English captions")
        page = self.post(bilingual="on")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Retry English captions")
        self.assertContains(page, "Dutch captions remain available")
        self.assertEqual(page.context["transcript"], DUTCH)
        self.assertFalse(page.context["form"].errors)
        self.fetch_english.side_effect = None
        self.assertEqual(self.post(bilingual="on", retry_english="1").status_code, 302)
        self.assertEqual(self.fetch_english.call_count, 2)
        self.fetch_dutch.assert_not_called()

    def test_failed_english_refresh_retains_both_caches(self):
        self.cache_english(caption_origin="translated")
        self.fetch_english.side_effect = TranscriptUnavailable(
            "YouTube temporarily blocked requests"
        )
        page = self.post(bilingual="on", refresh="1")
        self.assertEqual(page.context["transcript"], DUTCH)
        self.assertEqual(page.context["english_transcript"], ENGLISH)
        self.assertContains(page, "Showing cached English captions")
        self.assertContains(page, "auto-translated from Dutch by YouTube")
        self.assertEqual(TranscriptVideo.objects.get(source_language="en").transcript, ENGLISH)

    def test_autofetch_default_remains_dutch_only(self):
        self.dutch.delete()
        page = self.client.get(self.url, {"video": VIDEO_ID, "autofetch": "1"})
        self.assertEqual(page.status_code, 200)
        self.fetch_dutch.assert_called_once_with(VIDEO_ID, languages=("nl",))
        self.fetch_english.assert_not_called()

    def test_mode_gate_ignores_forged_bilingual_requests(self):
        for mode in [Deck.LanguageMode.DE_RU, Deck.LanguageMode.DE_ES]:
            with self.subTest(mode=mode):
                session = self.client.session
                session["language_mode"] = mode
                session.save()
                page = self.client.get(self.url, {"video": VIDEO_ID, "captions": "bilingual"})
                self.assertFalse(page.context["bilingual"])
                self.assertNotContains(page, "Apply captions")
                self.assertNotIn("bilingual", page.context["form"].fields)
                self.client.post(
                    self.url, {"url": SOURCE_URL, "source_language": "de", "bilingual": "on"}
                )
        self.fetch_english.assert_not_called()

    def test_account_preference_is_private_but_public_cache_can_be_reused(self):
        self.post(bilingual="on")
        other = get_user_model().objects.create_user(username="other", password="test")
        self.client.force_login(other)
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()
        page = self.client.get(self.url, {"video": VIDEO_ID})
        self.assertFalse(page.context["bilingual"])
        self.post(bilingual="on")
        self.fetch_english.assert_called_once()

    def test_anonymous_cannot_trigger_english_fetch(self):
        self.client.logout()
        self.assertEqual(self.post(bilingual="on").status_code, 302)
        self.fetch_english.assert_not_called()

    def test_primary_english_refresh_clears_stale_shared_provenance(self):
        english = self.cache_english(caption_origin="translated", caption_language="en-GB")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_RU
        session.save()
        self.fetch_dutch.return_value = ENGLISH
        page = self.client.get(self.url, {"video": VIDEO_ID, "lang": "en"})
        self.assertContains(page, "auto-translated from Dutch by YouTube")
        page = self.client.post(
            self.url, {"url": SOURCE_URL, "source_language": "en", "refresh": "1"}
        )
        self.assertEqual(page.status_code, 302)
        english.refresh_from_db()
        self.assertEqual(english.caption_origin, "unknown")
        self.assertEqual(english.caption_language, "en")
        self.assertEqual(english.transcript, ENGLISH)
        self.fetch_english.assert_not_called()

    def test_json_script_escapes_caption_markup(self):
        self.cache_english(
            transcript=[
                {"text": "</script><img src=x onerror=alert(1)>", "start": 0, "duration": 2}
            ]
        )
        page = self.client.get(self.url, {"video": VIDEO_ID, "captions": "bilingual"})
        self.assertNotContains(page, "<img src=x")
        self.assertContains(page, r"\u003C/script\u003E")
