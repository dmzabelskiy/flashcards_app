import json
from unittest.mock import patch

from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from decks.services import get_global_deck
from reviews.ratings import Rating
from reviews.scheduler import schedule_card
from video_study.dictionary import DictionaryResult
from video_study.metadata import LemmaCacheUnavailable
from video_study.normalization import CaptureResolution
from video_study.testutils import DictionaryStubMixin
from video_study.testutils_lemma import LemmaCacheTestMixin


class GlobalDeckTests(DictionaryStubMixin, LemmaCacheTestMixin, AuthedTestCase):
    def test_global_deck_is_reused_for_each_language_mode(self):
        first = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        again = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        spanish = get_global_deck(self.user, Deck.LanguageMode.DE_ES)

        self.assertEqual(first.pk, again.pk)
        self.assertNotEqual(first.pk, spanish.pk)
        self.assertTrue(first.is_global)

    def test_transcript_translation_is_cleaned_before_saving(self):
        noisy_translation = (
            "исполнять (поручение); исправлять (обязанности); совершать (обряд) "
            "{{пример|Sie erst einmal ihre Arbeit, dann können wir über Urlaub reden."
            "|перевод=Сначала свою работу, а потом поговорим об отпуске.}}"
        )

        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "verrichten",
                    "translation": noisy_translation,
                    "context": "Sie müssen ihre Arbeit verrichten.",
                    "source_url": "https://www.youtube.com/watch?v=abc123",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual(
            card.back,
            "исполнять (поручение); исправлять (обязанности); совершать (обряд)",
        )

    def test_added_conjugated_verb_is_normalized_before_saving(self):
        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "geht",
                    "source_form": "geht",
                    "translation": "goes",
                    "context": "Er geht nach Hause.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual(card.front, "gehen")
        self.assertEqual(card.article, "")

    def test_added_noun_uses_the_context_article(self):
        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "Kopf",
                    "source_form": "Kopf",
                    "translation": "head",
                    "context": "Das ist der Kopf.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual(card.front, "Kopf")
        self.assertEqual(card.article, "der")

    def test_normalized_word_duplicate_is_detected_on_the_lemma(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        existing = Card.objects.create(deck=deck, front="gehen", back="to go")

        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "geht",
                    "source_form": "geht",
                    "normalized_word": "gehen",
                    "translation": "to go",
                    "context": "Er geht nach Hause.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["created"])
        self.assertEqual(result["card_id"], existing.pk)
        self.assertEqual(Card.objects.count(), 1)

    def add(self, **payload):
        return self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_saved_card_is_dictionary_content_only(self):
        self.dictionary_result = DictionaryResult(
            status="found",
            lemma="Haus",
            article="das",
            plural="Häuser",
            part_of_speech="noun",
            example_sentence="Das Haus steht seit 1898.",
        )
        response = self.add(
            word="Haus",
            source_form="Häusern",
            translation="дом",
            context="In den Häusern wohnen viele Leute.",
            normalized_word="Häuserchen",
            article="der",
            plural="x",
            part_of_speech="verb",
            source_url="https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual((card.front, card.article, card.plural), ("Haus", "das", "Häuser"))
        self.assertEqual(card.back, "дом")
        self.assertEqual(card.example_sentence, "Das Haus steht seit 1898.")
        self.assertEqual((card.notes, card.tags, card.source_url), ("", "", ""))

    def test_card_without_a_dictionary_example_has_an_empty_example(self):
        response = self.add(source_form="gegangen", translation="идти", context="Ich bin gegangen.")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Card.objects.get().example_sentence, "")

    def test_save_is_refused_when_no_source_gives_a_dictionary_form(self):
        self.dictionary_result = DictionaryResult(status="unavailable")
        with patch(
            "video_study.metadata.get_lemmatizer",
            side_effect=LemmaCacheUnavailable("missing"),
        ):
            response = self.add(source_form="Häusern", translation="дом", context="")
        self.assertEqual(response.status_code, 422)
        self.assertIn("dictionary form", response.json()["error"])
        self.assertFalse(Card.objects.exists())

    def test_missing_word_or_translation_is_rejected(self):
        self.assertEqual(self.add(source_form="Haus", translation="").status_code, 400)
        self.assertEqual(self.add(source_form="", translation="дом").status_code, 400)

    def test_global_card_can_only_be_retired_after_three_correct_answers(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head")

        for _ in range(2):
            schedule_card(card, Rating.GOOD)
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 2)
        self.assertFalse(card.is_retired)

        blocked = self.client.post(reverse("cards:retire_global_card", kwargs={"pk": card.pk}))
        self.assertEqual(blocked.status_code, 302)
        card.refresh_from_db()
        self.assertFalse(card.is_retired)

        schedule_card(card, Rating.GOOD)
        retired = self.client.post(reverse("cards:retire_global_card", kwargs={"pk": card.pk}))
        self.assertEqual(retired.status_code, 302)
        card.refresh_from_db()
        self.assertTrue(card.is_retired)
        self.assertNotIn(card, Card.objects.active())

    def test_again_resets_global_correct_streak(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head")
        schedule_card(card, Rating.GOOD)
        schedule_card(card, Rating.AGAIN)

        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 0)

    def test_global_deck_page_renders_retirement_controls(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        Card.objects.create(deck=deck, front="Kopf", back="head")

        response = self.client.get(deck.get_absolute_url())

        self.assertContains(response, "Global deck")
        self.assertContains(response, "Retire")
        self.assertContains(response, "Requires 3 correct answers in a row")


class NlGlobalDeckTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

    @patch("video_study.resolvers.resolve_capture_nl")
    @patch("video_study.resolvers.resolve_capture")
    def test_dutch_capture_uses_the_dutch_resolver(self, resolve_de, resolve_nl):
        resolve_nl.return_value = CaptureResolution(
            lemma="huis",
            article="het",
            plural="huizen",
            part_of_speech="noun",
            example_sentence="",
            dictionary_status="found",
            trustworthy=True,
            translation="house",
        )

        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "huizen",
                    "source_form": "huizen",
                    "translation": "house",
                    "context": "De huizen.",
                    "source_language": "nl",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        card = Card.objects.get()
        self.assertEqual((card.front, card.article, card.plural), ("huis", "het", "huizen"))
        self.assertEqual(card.deck.language_mode, Deck.LanguageMode.NL_EN)
        resolve_de.assert_not_called()

    def test_german_source_language_is_rejected_in_nl_mode(self):
        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "Haus",
                    "source_form": "Haus",
                    "translation": "house",
                    "context": "Das Haus.",
                    "source_language": "de",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Card.objects.count(), 0)


class NlSourceLanguageMismatchTests(AuthedTestCase):
    """Default session mode is DE-RU; a Dutch source_language must be rejected."""

    def test_dutch_source_language_is_rejected_outside_nl_mode(self):
        response = self.client.post(
            reverse("cards:add_to_global"),
            data=json.dumps(
                {
                    "word": "huis",
                    "source_form": "huis",
                    "translation": "house",
                    "context": "Het huis.",
                    "source_language": "nl",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Card.objects.count(), 0)
