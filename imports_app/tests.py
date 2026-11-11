from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from imports_app.forms import CsvImportForm
from imports_app.services import import_cards_from_csv


class CsvImportTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def test_imports_valid_rows_and_reports_row_errors(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        csv_text = (
            "front,back,example_sentence,notes,tags,card_type\n"
            "der Tisch,table,Der Tisch steht im Zimmer.,noun,furniture,vocabulary\n"
            ",missing front,,,,\n"
            "seit,for/since,Ich lebe seit zwei Jahren in Deutschland.,time,grammar,grammar\n"
        )

        result = import_cards_from_csv(deck, csv_text)

        self.assertEqual(result.imported_count, 2)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].row_number, 3)
        self.assertEqual(Card.objects.count(), 2)
        self.assertTrue(Card.objects.filter(front="der Tisch", tags="furniture").exists())

    def test_import_skips_empty_rows(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        csv_text = "front,back\n\n  ,  \nHallo,hello\n"

        result = import_cards_from_csv(deck, csv_text)

        self.assertEqual(result.imported_count, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(Card.objects.get().front, "Hallo")


class ImportedArticleTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def import_rows(self, rows, mode=Deck.LanguageMode.DE_RU):
        deck = Deck.objects.create(name="Words", language_mode=mode, owner=self.owner)
        csv_text = "front,back,card_type\n" + "".join(
            f'"{front}",gloss,{card_type}\n' for front, card_type in rows
        )
        result = import_cards_from_csv(deck, csv_text)
        self.assertEqual(result.errors, [])
        return {card.front: card for card in Card.objects.filter(deck=deck)}

    def test_a_vocabulary_front_with_a_pack_article_sets_it_and_stays_unchanged(self):
        cards = self.import_rows(
            [("der Alltag", "vocabulary"), ("die Art und Weise", "vocabulary")]
        )

        self.assertEqual(cards["der Alltag"].article, "der")
        self.assertEqual(cards["die Art und Weise"].article, "die")

    def test_a_sentence_front_gets_no_article(self):
        cards = self.import_rows([("Die Katze schläft.", "vocabulary")])

        self.assertEqual(cards["Die Katze schläft."].article, "")

    def test_a_sentence_front_ending_in_a_question_or_exclamation_gets_none(self):
        cards = self.import_rows(
            [("die Katze schläft!", "vocabulary"), ("das Haus brennt?", "vocabulary")]
        )

        self.assertEqual({card.article for card in cards.values()}, {""})

    def test_a_capitalised_article_gets_none(self):
        cards = self.import_rows([("Der Alltag", "vocabulary")])

        self.assertEqual(cards["Der Alltag"].article, "")

    def test_a_front_that_is_only_an_article_gets_none(self):
        cards = self.import_rows([("der", "vocabulary")])

        self.assertEqual(cards["der"].article, "")

    def test_another_card_type_gets_none(self):
        cards = self.import_rows([("der Alltag", "grammar")])

        self.assertEqual(cards["der Alltag"].article, "")

    def test_an_article_of_another_pack_gets_none(self):
        cards = self.import_rows([("het huis", "vocabulary")])

        self.assertEqual(cards["het huis"].article, "")

    def test_a_dutch_deck_takes_the_dutch_articles(self):
        cards = self.import_rows([("het huis", "vocabulary")], mode=Deck.LanguageMode.NL_EN)

        self.assertEqual(cards["het huis"].article, "het")


class B2VocabularyCardSetTests(TestCase):
    def test_b2_vocabulary_card_set_loads_through_the_importer(self):
        path = Path(settings.BASE_DIR, "card_sets", "DE-RU", "german_b2_vocabulary.csv")
        csv_text = path.read_bytes().decode("utf-8-sig")  # as the upload view decodes it
        deck = Deck.objects.create(
            name="B2 vocabulary", language_mode=Deck.LanguageMode.DE_RU, owner=create_user()
        )

        result = import_cards_from_csv(deck, csv_text)

        self.assertEqual(result.errors, [])
        self.assertGreater(result.imported_count, 150)
        fronts = list(Card.objects.filter(deck=deck).values_list("front", flat=True))
        self.assertEqual(len(fronts), len(set(fronts)))


class SpanishVocabularyCardSetTests(TestCase):
    def test_spanish_a1_a2_card_set_loads_into_an_es_ru_global_deck(self):
        path = Path(settings.BASE_DIR, "card_sets", "ES-RU", "spanish_a1_a2_vocabulary.csv")
        csv_text = path.read_bytes().decode("utf-8-sig")  # as the upload view decodes it
        deck = Deck.objects.create(
            name="Spanish",
            language_mode=Deck.LanguageMode.ES_RU,
            owner=create_user(),
            is_global=True,
        )

        result = import_cards_from_csv(deck, csv_text)

        self.assertEqual(result.errors, [])
        cards = list(Card.objects.filter(deck=deck))
        self.assertGreaterEqual(result.imported_count, 900)
        self.assertEqual(len(cards), result.imported_count)
        fronts = [card.front.casefold() for card in cards]
        self.assertEqual(len(fronts), len(set(fronts)))
        for card in cards:
            with self.subTest(front=card.front):
                self.assertTrue(card.back)
                self.assertTrue(card.example_sentence)
                self.assertFalse(card.front.startswith(("los ", "las ", "un ", "una ")))
                for article in ("el", "la"):
                    if card.front.startswith(f"{article} "):
                        self.assertEqual(card.article, article)


class CsvImportFormTests(TestCase):
    def test_import_form_limits_decks_to_selected_language_mode(self):
        owner = create_user()
        Deck.objects.create(name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=owner)
        es_deck = Deck.objects.create(name="ES", language_mode=Deck.LanguageMode.DE_ES, owner=owner)

        form = CsvImportForm(language_mode=Deck.LanguageMode.DE_ES)

        self.assertEqual(list(form.fields["deck"].queryset), [es_deck])


class ImportDeckScopingTests(AuthedTestCase):
    def test_import_form_excludes_other_users_decks(self):
        Deck.objects.create(name="Mine", owner=self.user)
        other = create_user("other")
        Deck.objects.create(name="Other", owner=other)
        response = self.client.get(reverse("imports_app:import_cards"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Other")
