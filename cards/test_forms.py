from django.test import TestCase

from cards.forms import CardForm
from core.testutils import create_user
from decks.models import Deck


class CardFormArticleTests(TestCase):
    def setUp(self):
        self.owner = create_user()
        self.de_deck = Deck.objects.create(
            owner=self.owner, name="DE", language_mode=Deck.LanguageMode.DE_RU
        )
        self.nl_deck = Deck.objects.create(
            owner=self.owner, name="NL", language_mode=Deck.LanguageMode.NL_EN
        )

    def _data(self, deck, article):
        return {
            "deck": deck.pk,
            "front": "front",
            "back": "back",
            "example_sentence": "",
            "notes": "",
            "tags": "",
            "article": article,
            "plural": "",
            "card_type": "vocabulary",
            "due_at": "2026-01-01T00:00",
            "is_suspended": False,
        }

    def test_de_ru_form_rejects_het(self):
        form = CardForm(
            data=self._data(self.de_deck, "het"),
            language_mode=Deck.LanguageMode.DE_RU,
            user=self.owner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("article", form.errors)

    def test_nl_en_form_rejects_das(self):
        form = CardForm(
            data=self._data(self.nl_deck, "das"),
            language_mode=Deck.LanguageMode.NL_EN,
            user=self.owner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("article", form.errors)

    def test_nl_en_form_accepts_het(self):
        form = CardForm(
            data=self._data(self.nl_deck, "het"),
            language_mode=Deck.LanguageMode.NL_EN,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_german_form_still_accepts_der_die_das(self):
        for article in ("der", "die", "das"):
            form = CardForm(
                data=self._data(self.de_deck, article),
                language_mode=Deck.LanguageMode.DE_RU,
                user=self.owner,
            )
            self.assertTrue(form.is_valid(), form.errors)

    def test_de_ru_field_choices_exclude_dutch_articles(self):
        form = CardForm(language_mode=Deck.LanguageMode.DE_RU, user=self.owner)
        values = [value for value, _label in form.fields["article"].choices]
        self.assertEqual(values, ["", "der", "die", "das"])

    def test_nl_en_field_choices_exclude_german_articles(self):
        form = CardForm(language_mode=Deck.LanguageMode.NL_EN, user=self.owner)
        values = [value for value, _label in form.fields["article"].choices]
        self.assertEqual(values, ["", "de", "het"])
