"""The ñ button: shown beside typed Spanish answers, nowhere else."""

from django.urls import reverse

from cards.models import Card
from core.language_packs import DUTCH, GERMAN, PACKS
from core.testutils import AuthedTestCase
from decks.models import Deck

BUTTON = 'data-insert-letter="ñ"'
SCRIPT = "js/letter_buttons.js"


class StudyCardLetterButtonTests(AuthedTestCase):
    def open_card(self, mode, front, back, direction):
        deck = Deck.objects.create(name="Deck", owner=self.user, language_mode=mode)
        Card.objects.create(deck=deck, front=front, back=back)
        session = self.client.session
        session["language_mode"] = mode
        session.save()
        self.client.get(reverse("reviews:start_study", args=[deck.pk]), {"direction": direction})
        return self.client.get(reverse("reviews:study_current"))

    def test_shown_where_a_spanish_word_is_typed(self):
        page = self.open_card(Deck.LanguageMode.ES_RU, "año", "год", "reverse")

        self.assertContains(page, BUTTON)
        self.assertContains(page, 'aria-label="Insert ñ"')
        self.assertContains(page, 'type="button"')
        self.assertContains(page, SCRIPT)

    def test_not_shown_where_the_translation_is_typed(self):
        page = self.open_card(Deck.LanguageMode.ES_RU, "año", "год", "forward")

        self.assertNotContains(page, BUTTON)
        self.assertNotContains(page, SCRIPT)

    def test_not_shown_in_german_or_dutch_modes(self):
        for mode, front, back in (
            (Deck.LanguageMode.DE_RU, "Haus", "дом"),
            (Deck.LanguageMode.NL_EN, "huis", "house"),
        ):
            with self.subTest(mode=mode):
                page = self.open_card(mode, front, back, "reverse")

                self.assertContains(page, 'name="answer"')
                self.assertNotContains(page, "data-insert-letter")
                self.assertNotContains(page, SCRIPT)


class DrillLetterButtonTests(AuthedTestCase):
    def open_drill(self, mode, slug):
        session = self.client.session
        session["language_mode"] = mode
        session.save()
        self.client.get(reverse("reviews:drill_start", args=[slug]))
        return self.client.get(reverse("reviews:drill", args=[slug]))

    def test_shown_on_the_text_input_of_a_spanish_trainer(self):
        for slug in ("verb-forms", "verbs-in-context", "small-words"):
            with self.subTest(drill=slug):
                page = self.open_drill(Deck.LanguageMode.ES_RU, slug)

                self.assertContains(page, 'type="text"')
                self.assertContains(page, BUTTON)
                self.assertContains(page, 'aria-label="Insert ñ"')
                self.assertContains(page, SCRIPT)

    def test_not_shown_in_a_german_drill(self):
        page = self.open_drill(Deck.LanguageMode.DE_RU, "irregular-verbs")

        self.assertContains(page, 'type="text"')
        self.assertNotContains(page, "data-insert-letter")
        self.assertNotContains(page, SCRIPT)


class PackExtraLettersTests(AuthedTestCase):
    def test_only_spanish_declares_extra_letters(self):
        self.assertEqual(PACKS["es"].extra_letters, ("ñ",))
        self.assertEqual(GERMAN.extra_letters, ())
        self.assertEqual(DUTCH.extra_letters, ())
