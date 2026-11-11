import json
from unittest.mock import ANY, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study.dictionary import DictionaryResult
from video_study.models import TranscriptVideo, WordTranslation
from video_study.services import (
    InvalidYouTubeUrl,
    TranslationUnavailable,
    extract_youtube_id,
    normalize_word,
)
from video_study.testutils import DictionaryStubMixin, OfflineTranslationMixin
from video_study.testutils_lemma import LemmaCacheTestMixin

SAMPLE_TRANSCRIPT = [
    {"text": "Hello, curious human.", "start": 0.0, "duration": 2.4},
    {"text": "Let us begin.", "start": 2.4, "duration": 1.8},
]


class YouTubeUrlTests(SimpleTestCase):
    def test_extracts_ids_from_supported_url_shapes(self):
        video_id = "dQw4w9WgXcQ"
        urls = [
            video_id,
            f"https://www.youtube.com/watch?v={video_id}&t=20",
            f"https://youtu.be/{video_id}?si=example",
            f"https://www.youtube.com/shorts/{video_id}",
            f"https://www.youtube.com/embed/{video_id}",
            f"https://www.youtube.com/live/{video_id}",
        ]

        self.assertEqual(
            [extract_youtube_id(url) for url in urls],
            [video_id] * len(urls),
        )

    def test_rejects_non_youtube_urls(self):
        with self.assertRaises(InvalidYouTubeUrl):
            extract_youtube_id("https://example.com/watch?v=dQw4w9WgXcQ")

    def test_normalizes_single_words_for_cache_keys(self):
        self.assertEqual(normalize_word("Curious"), "curious")
        self.assertEqual(normalize_word("don't"), "don't")

    def test_rejects_phrases_as_word_lookups(self):
        with self.assertRaises(TranslationUnavailable):
            normalize_word("two words")


class LoginGateTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("video_study:workspace"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class VideoWorkspaceTests(AuthedTestCase):
    @patch("video_study.views.fetch_transcript", return_value=SAMPLE_TRANSCRIPT)
    def test_post_loads_and_caches_a_transcript(self, fetch_transcript):
        response = self.client.post(
            reverse("video_study:workspace"),
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

        self.assertRedirects(
            response,
            f"{reverse('video_study:workspace')}?video=dQw4w9WgXcQ&lang=de",
        )
        video = TranscriptVideo.objects.get(youtube_id="dQw4w9WgXcQ")
        self.assertEqual(video.transcript, SAMPLE_TRANSCRIPT)
        fetch_transcript.assert_called_once_with(
            "dQw4w9WgXcQ",
            languages=("de",),
        )

    @patch("video_study.views.fetch_transcript")
    def test_cached_video_does_not_fetch_again(self, fetch_transcript):
        TranscriptVideo.objects.create(
            youtube_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_language="de",
            transcript=SAMPLE_TRANSCRIPT,
        )

        response = self.client.post(
            reverse("video_study:workspace"),
            {"url": "https://youtu.be/dQw4w9WgXcQ"},
        )

        self.assertEqual(response.status_code, 302)
        fetch_transcript.assert_not_called()

    def test_workspace_renders_cached_transcript_and_player_data(self):
        TranscriptVideo.objects.create(
            youtube_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_language="de",
            transcript=SAMPLE_TRANSCRIPT,
        )

        response = self.client.get(
            reverse("video_study:workspace"),
            {"video": "dQw4w9WgXcQ", "lang": "de"},
        )

        self.assertContains(response, 'data-video-id="dQw4w9WgXcQ"')
        self.assertContains(response, "video-transcript-data")
        self.assertContains(response, "Hello, curious human.")
        self.assertContains(response, "German · 2 segments")

    @patch("video_study.views.fetch_transcript", return_value=SAMPLE_TRANSCRIPT)
    def test_autofetches_a_dashboard_recommendation_into_learning_mode(self, fetch_transcript):
        response = self.client.get(
            reverse("video_study:workspace"),
            {"video": "dQw4w9WgXcQ", "lang": "de", "autofetch": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "video-transcript-data")
        fetch_transcript.assert_called_once_with(
            "dQw4w9WgXcQ",
            languages=("de",),
        )
        self.assertTrue(
            TranscriptVideo.objects.filter(youtube_id="dQw4w9WgXcQ", source_language="de").exists()
        )

    def test_spanish_mode_labels_video_translation_target(self):
        TranscriptVideo.objects.create(
            youtube_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_language="de",
            transcript=SAMPLE_TRANSCRIPT,
        )
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get(
            reverse("video_study:workspace"),
            {"video": "dQw4w9WgXcQ", "lang": "de"},
        )

        self.assertContains(response, "for a Spanish translation")
        self.assertContains(response, "German → Spanish")
        self.assertContains(response, 'data-target-language="es"')
        self.assertContains(
            response,
            'data-target-language-label="Spanish"',
        )

    @patch("video_study.views.fetch_transcript")
    def test_invalid_url_is_shown_as_a_form_error(self, fetch_transcript):
        response = self.client.post(
            reverse("video_study:workspace"),
            {"url": "https://example.com/not-youtube"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid youtube.com or youtu.be video URL.")
        fetch_transcript.assert_not_called()

    def test_german_mode_offers_only_non_dutch_source_languages(self):
        response = self.client.get(reverse("video_study:workspace"))
        choices = dict(response.context["form"].fields["source_language"].choices)
        self.assertEqual(set(choices), {"de", "en"})

    def test_dutch_lang_param_is_rejected_in_german_mode(self):
        response = self.client.get(
            reverse("video_study:workspace"),
            {"video": "dQw4w9WgXcQ", "lang": "nl"},
        )
        self.assertEqual(response.context["source_language"], "de")


class NlVideoWorkspaceTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

    def test_nl_mode_offers_only_dutch(self):
        response = self.client.get(reverse("video_study:workspace"))
        choices = dict(response.context["form"].fields["source_language"].choices)
        self.assertEqual(set(choices), {"nl"})

    @patch("video_study.views.fetch_transcript")
    def test_german_source_language_is_rejected_in_nl_mode(self, fetch_transcript):
        response = self.client.post(
            reverse("video_study:workspace"),
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "source_language": "de"},
        )

        self.assertEqual(response.status_code, 200)
        fetch_transcript.assert_not_called()

    @patch("video_study.views.fetch_transcript", return_value=SAMPLE_TRANSCRIPT)
    def test_omitted_source_language_defaults_to_dutch(self, fetch_transcript):
        response = self.client.post(
            reverse("video_study:workspace"),
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

        self.assertRedirects(
            response,
            f"{reverse('video_study:workspace')}?video=dQw4w9WgXcQ&lang=nl",
        )
        fetch_transcript.assert_called_once_with("dQw4w9WgXcQ", languages=("nl",))
        self.assertTrue(
            TranscriptVideo.objects.filter(youtube_id="dQw4w9WgXcQ", source_language="nl").exists()
        )


class TranslationEndpointTests(DictionaryStubMixin, OfflineTranslationMixin, AuthedTestCase):
    @patch("video_study.views.request_translation", return_value="любопытный")
    def test_translates_and_then_uses_the_local_cache(self, request_translation):
        url = reverse("video_study:translate_word")
        first = self.client.post(
            url,
            data=json.dumps({"word": "curious"}),
            content_type="application/json",
        )
        # A later, differently-cased selection of the same word (e.g.
        # sentence-initial "Curious") still hits the row the first request
        # wrote, through the legacy casefolded-key fallback.
        second = self.client.post(
            url,
            data=json.dumps({"word": "Curious"}),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["translation"], "любопытный")
        self.assertFalse(first.json()["cached"])
        self.assertTrue(second.json()["cached"])
        self.assertEqual(WordTranslation.objects.count(), 1)
        request_translation.assert_called_once_with(
            "curious",
            source_language="de",
            target_language="ru",
            source_form="curious",
            budget_start=ANY,
        )

    @patch("video_study.views.request_translation", return_value="curioso")
    def test_spanish_mode_uses_a_separate_spanish_translation_cache(self, request_translation):
        WordTranslation.objects.create(
            source_text="Curious",
            source_language="de",
            target_language="ru",
            translated_text="любопытный",
        )
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"word": "Curious"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["translation"], "curioso")
        self.assertFalse(response.json()["cached"])
        self.assertTrue(
            WordTranslation.objects.filter(
                source_text="Curious",
                source_language="de",
                target_language="es",
                translated_text="curioso",
            ).exists()
        )
        request_translation.assert_called_once_with(
            "Curious",
            source_language="de",
            target_language="es",
            source_form="Curious",
            budget_start=ANY,
        )

    def test_rejects_invalid_json_and_non_words(self):
        url = reverse("video_study:translate_word")

        invalid_json = self.client.post(
            url,
            data="{",
            content_type="application/json",
        )
        phrase = self.client.post(
            url,
            data=json.dumps({"text": "1234"}),
            content_type="application/json",
        )

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(phrase.status_code, 400)

    def test_dutch_source_language_is_rejected_outside_nl_mode(self):
        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": "huis", "source_language": "nl"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)


@patch("video_study.views.request_translation", return_value="дом")
class TranslateWordResolutionTests(DictionaryStubMixin, LemmaCacheTestMixin, AuthedTestCase):
    dictionary_result = DictionaryResult(
        status="found",
        lemma="Haus",
        article="das",
        plural="Häuser",
        part_of_speech="noun",
        example_sentence="Das Haus steht seit 1898.",
    )

    def post(self, text, context):
        return self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": text, "context": context, "source_language": "de"}),
            content_type="application/json",
        )

    def test_suggestion_carries_the_finished_card_fields(self, request_translation):
        result = self.post("Häusern", "In den Häusern wohnen viele Leute.").json()
        self.assertEqual(result["word"], "das Haus")
        suggestion = result["suggestion"]
        self.assertEqual(suggestion["normalized_word"], "Haus")
        self.assertEqual(suggestion["gender"], "neuter")
        self.assertEqual(suggestion["plural"], "Häuser")
        self.assertEqual(suggestion["example_sentence"], "Das Haus steht seit 1898.")
        self.assertEqual(suggestion["dictionary_status"], "found")
        self.assertTrue(suggestion["trustworthy"])
        self.assertEqual(suggestion["source"], "dictionary")
        self.assertEqual(request_translation.call_args.args[0], "Haus")

    def test_the_original_case_is_resolved_not_the_cache_key(self, request_translation):
        self.post("Häusern", "In den Häusern wohnen viele Leute.")
        self.assertEqual(self.lookup_dictionary.call_args.args[1], "Häusern")

    def test_eszett_is_kept(self, request_translation):
        self.dictionary_result = DictionaryResult(status="missing")
        result = self.post("größeren", "in größeren Städten").json()
        self.assertEqual(result["suggestion"]["normalized_word"], "groß")

    def test_unavailable_dictionary_is_reported(self, request_translation):
        self.dictionary_result = DictionaryResult(status="unavailable")
        result = self.post("gegangen", "Ich bin gegangen.").json()
        self.assertEqual(result["suggestion"]["dictionary_status"], "unavailable")
        self.assertEqual(result["suggestion"]["normalized_word"], "gehen")
