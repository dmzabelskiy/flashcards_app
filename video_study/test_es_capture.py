import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from decks.services import get_global_deck
from video_study.es.capture import resolve_capture_es
from video_study.es.lemma import lemmatize_spanish
from video_study.metadata import LemmaCacheUnavailable
from video_study.models import WordTranslation
from video_study.normalization import CaptureResolution
from video_study.testutils_lemma import LemmaCacheTestMixin


class LemmatizeSpanishTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_inflected_forms_return_their_lemma(self):
        self.assertEqual(lemmatize_spanish("tengo"), ("tener", True))
        self.assertEqual(lemmatize_spanish("fuimos"), ("ir", True))
        self.assertEqual(lemmatize_spanish("niñas"), ("niño", True))

    def test_the_token_is_lower_cased(self):
        self.assertEqual(lemmatize_spanish("Tengo"), ("tener", True))

    def test_an_unavailable_cache_returns_the_token_unchanged(self):
        with patch(
            "video_study.es.lemma.get_lemmatizer", side_effect=LemmaCacheUnavailable("missing")
        ):
            self.assertEqual(lemmatize_spanish("Tengo"), ("tengo", False))


class ResolveCaptureEsTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_a_verb_form_resolves_to_its_infinitive(self):
        r = resolve_capture_es("tengo", "Tengo un coche.")
        self.assertEqual(r.lemma, "tener")
        self.assertEqual((r.article, r.plural, r.part_of_speech), ("", "", ""))
        self.assertEqual((r.translation, r.dictionary_status), ("", "skipped"))
        self.assertTrue(r.trustworthy)

    def test_a_plural_noun_resolves_to_its_singular(self):
        self.assertEqual(resolve_capture_es("niñas", "Las niñas juegan.").lemma, "niño")

    def test_accented_words_and_enye_stay_whole(self):
        self.assertEqual(resolve_capture_es("año", "Es un año.").lemma, "año")
        self.assertEqual(resolve_capture_es("Él", "Él come.").lemma, "él")

    def test_several_words_are_a_trustworthy_unlemmatised_phrase(self):
        r = resolve_capture_es("por favor", "Dos cafés, por favor.")
        self.assertEqual(
            (r.lemma, r.part_of_speech, r.article, r.dictionary_status, r.trustworthy),
            ("por favor", "phrase", "", "skipped", True),
        )

    def test_an_unavailable_cache_gives_an_untrustworthy_token(self):
        with patch(
            "video_study.es.lemma.get_lemmatizer", side_effect=LemmaCacheUnavailable("missing")
        ):
            r = resolve_capture_es("Tengo", "Tengo un coche.")
        self.assertEqual((r.lemma, r.trustworthy), ("tengo", False))


class EsCaptureViewsTests(LemmaCacheTestMixin, AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.ES_RU
        session.save()

    def translate(self, text, context="", source_language="es"):
        return self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": text, "context": context, "source_language": source_language}),
            content_type="application/json",
        )

    def add(self, source_form, translation="иметь", source_language="es"):
        return self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": source_form,
                    "source_form": source_form,
                    "translation": translation,
                    "context": "",
                    "source_language": source_language,
                }
            ),
            content_type="application/json",
        )

    @patch("video_study.views.request_translation", return_value="иметь")
    def test_translate_asks_the_provider_chain_for_the_lemma_only(self, provider):
        response = self.translate("tengo", "Tengo un coche azul.")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["word"], body["translation"]), ("tener", "иметь"))
        self.assertEqual(body["suggestion"]["article"], "")
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args, ("tener",))
        self.assertEqual(provider.call_args.kwargs["source_language"], "es")
        self.assertEqual(provider.call_args.kwargs["target_language"], "ru")
        self.assertEqual(provider.call_args.kwargs["source_form"], "tener")
        row = WordTranslation.objects.get(source_text="tener")
        self.assertEqual((row.source_language, row.target_language), ("es", "ru"))

    @patch("video_study.views.request_translation", return_value="пожалуйста")
    def test_translate_sends_a_selected_phrase_as_selected(self, provider):
        response = self.translate("Por favor", "Dos cafés, por favor.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(provider.call_args.args, ("por favor",))

    @patch("video_study.views.request_translation", return_value="иметь")
    def test_the_source_language_defaults_to_the_mode(self, provider):
        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": "tengo", "context": ""}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

    @patch("video_study.views.request_translation")
    def test_german_source_language_is_rejected_in_es_ru(self, provider):
        self.assertEqual(self.translate("Haus", source_language="de").status_code, 400)
        self.assertEqual(self.translate("house", source_language="en").status_code, 400)
        provider.assert_not_called()

    @patch("video_study.views.request_translation")
    def test_spanish_source_language_is_rejected_in_german_and_dutch_modes(self, provider):
        for mode in (Deck.LanguageMode.DE_RU, Deck.LanguageMode.NL_EN):
            session = self.client.session
            session["language_mode"] = mode
            session.save()
            self.assertEqual(self.translate("tengo").status_code, 400, mode)
            self.assertEqual(self.add("tengo").status_code, 400, mode)
        provider.assert_not_called()
        self.assertFalse(Card.objects.exists())

    def test_saving_creates_a_card_with_the_lemma_and_no_article(self):
        response = self.add("comer", translation="есть")

        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual((card.front, card.article, card.plural), ("comer", "", ""))
        self.assertEqual(card.deck.language_mode, Deck.LanguageMode.ES_RU)
        self.assertTrue(card.deck.is_global)

    def test_saving_an_inflected_form_stores_the_lemma(self):
        self.assertEqual(self.add("tengo").status_code, 201)
        self.assertEqual(Card.objects.get().front, "tener")

    def test_saving_a_noun_the_list_already_holds_returns_that_card(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.ES_RU)
        card = Card.objects.create(deck=deck, front="el coche", article="el", back="машина")

        response = self.add("coche", translation="автомобиль")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            (body["card_id"], body["created"], body["word"]), (card.pk, False, "el coche")
        )
        self.assertEqual(Card.objects.count(), 1)

    def test_the_list_card_match_ignores_case(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.ES_RU)
        Card.objects.create(deck=deck, front="La Casa", article="la", back="дом")

        response = self.add("casa", translation="дом")

        self.assertFalse(response.json()["created"])

    def test_a_card_in_another_deck_or_mode_is_not_a_match(self):
        other = Deck.objects.create(
            owner=self.user, name="Mine", language_mode=Deck.LanguageMode.ES_RU
        )
        Card.objects.create(deck=other, front="el coche", article="el", back="машина")

        response = self.add("coche", translation="автомобиль")

        self.assertTrue(response.json()["created"])

    def test_saving_is_refused_when_the_lemma_cache_is_unavailable(self):
        with patch(
            "video_study.es.lemma.get_lemmatizer", side_effect=LemmaCacheUnavailable("missing")
        ):
            response = self.add("tengo")

        self.assertEqual(response.status_code, 422)
        self.assertIn("dictionary form", response.json()["error"])
        self.assertFalse(Card.objects.exists())

    @patch("video_study.views.request_translation", return_value="иметь")
    def test_translating_with_an_unavailable_cache_is_not_a_server_error(self, _provider):
        with patch(
            "video_study.es.lemma.get_lemmatizer", side_effect=LemmaCacheUnavailable("missing")
        ):
            response = self.translate("tengo")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["suggestion"]["trustworthy"])


class GermanSavingIsUnchangedTests(LemmaCacheTestMixin, AuthedTestCase):
    @patch("video_study.resolvers.resolve_capture")
    def test_a_verb_is_saved_even_when_its_noun_twin_is_in_the_deck(self, resolve):
        resolve.return_value = CaptureResolution(
            lemma="essen",
            article="",
            plural="",
            part_of_speech="verb",
            example_sentence="",
            dictionary_status="found",
            trustworthy=True,
        )
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        noun = Card.objects.create(deck=deck, front="das Essen", article="das", back="еда")

        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps({"word": "essen", "source_form": "essen", "translation": "есть"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["created"])
        self.assertEqual(Card.objects.filter(deck=deck).count(), 2)
        self.assertNotEqual(response.json()["card_id"], noun.pk)
