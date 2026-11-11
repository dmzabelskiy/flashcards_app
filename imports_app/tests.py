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
