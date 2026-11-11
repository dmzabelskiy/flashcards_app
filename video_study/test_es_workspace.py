from unittest.mock import patch

from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study.models import TranscriptVideo
from video_study.views import _allowed_source_languages

TRANSCRIPT = [{"text": "Hola, ¿qué tal?", "start": 0.0, "duration": 1.0}]


class AllowedSourceLanguagesTests(AuthedTestCase):
    def test_each_mode_offers_its_own_languages(self):
        self.assertEqual(_allowed_source_languages("ES-RU"), {"es"})
        self.assertEqual(_allowed_source_languages("DE-RU"), {"de", "en"})
        self.assertEqual(_allowed_source_languages("DE-ES"), {"de", "en"})
        self.assertEqual(_allowed_source_languages("NL-EN"), {"nl"})


class EsVideoWorkspaceTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.ES_RU
        session.save()
        self.url = reverse("video_study:workspace")

    def test_the_workspace_offers_only_spanish_and_names_it(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        choices = dict(response.context["form"].fields["source_language"].choices)
        self.assertEqual(choices, {"es": "Spanish"})
        self.assertEqual(response.context["source_language"], "es")
        self.assertContains(response, "Load Spanish captions")
        self.assertContains(response, "for a Russian translation")

    def test_the_workspace_does_not_name_german_or_kurzgesagt(self):
        response = self.client.get(self.url)

        content = response.content.decode()
        # The mode menu in the top bar lists every mode; the workspace itself must not.
        workspace = content[content.index("<main") : content.index("</main>")]
        self.assertNotIn("Kurzgesagt", workspace)
        self.assertNotIn("German", workspace)

    def test_a_stale_german_lang_falls_back_to_spanish(self):
        TranscriptVideo.objects.create(
            youtube_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_language="es",
            transcript=TRANSCRIPT,
        )

        response = self.client.get(self.url, {"video": "dQw4w9WgXcQ", "lang": "de"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["source_language"], "es")
        self.assertContains(response, "Spanish · 1 segments")
        self.assertContains(response, 'data-source-language="es"')

    @patch("video_study.views.fetch_transcript", return_value=TRANSCRIPT)
    def test_autofetch_asks_for_spanish_captions(self, fetch_transcript):
        response = self.client.get(
            self.url, {"video": "dQw4w9WgXcQ", "lang": "de", "autofetch": "1"}
        )

        self.assertEqual(response.status_code, 200)
        fetch_transcript.assert_called_once_with("dQw4w9WgXcQ", languages=("es",))

    @patch("video_study.views.fetch_transcript")
    def test_a_posted_german_source_language_is_rejected(self, fetch_transcript):
        response = self.client.post(
            self.url,
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "source_language": "de"},
        )

        self.assertEqual(response.status_code, 200)
        fetch_transcript.assert_not_called()

    def test_the_german_mode_hint_still_names_german_and_english(self):
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_RU
        session.save()

        response = self.client.get(self.url)

        self.assertContains(response, "Load German or English captions")


class ReverseStudyLanguageNameTests(AuthedTestCase):
    def reverse_card_page(self, mode):
        deck = Deck.objects.create(name="Words", language_mode=mode, owner=self.user)
        Card.objects.create(deck=deck, front="gato", back="кот")
        session = self.client.session
        session["language_mode"] = mode
        session.save()
        self.client.get(reverse("reviews:start_study", args=[deck.pk]) + "?direction=reverse")
        return self.client.get(reverse("reviews:study_current"))

    def test_es_ru_names_spanish(self):
        page = self.reverse_card_page(Deck.LanguageMode.ES_RU)

        self.assertContains(page, "Russian &rarr; Spanish")
        self.assertContains(page, "Type the Spanish word")
        self.assertNotContains(page, "Deutsch")
        self.assertNotContains(page, "German word")

    def test_de_ru_names_german(self):
        page = self.reverse_card_page(Deck.LanguageMode.DE_RU)

        self.assertContains(page, "Russian &rarr; German")
        self.assertContains(page, "Type the German word")
        self.assertNotContains(page, "Deutsch")

    def test_nl_en_names_dutch(self):
        page = self.reverse_card_page(Deck.LanguageMode.NL_EN)

        self.assertContains(page, "English &rarr; Dutch")
        self.assertContains(page, "Type the Dutch word")
