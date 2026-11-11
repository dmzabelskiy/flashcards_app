import csv
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import STARTER_SETS, get_global_deck
from imports_app.services import import_cards_from_csv

CARD_SETS = Path(settings.BASE_DIR, "card_sets", "ES-RU")
STARTER = CARD_SETS / "spanish_a2_starter.csv"
FULL = CARD_SETS / "spanish_a1_a2_vocabulary.csv"
ES = Deck.LanguageMode.ES_RU


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class StarterSetFileTests(AuthedTestCase):
    def test_the_starter_file_has_100_unique_fronts_all_marked_a2(self):
        rows = read_rows(STARTER)

        self.assertEqual(len(rows), 100)
        self.assertEqual(len({row["front"] for row in rows}), 100)
        self.assertTrue(all(row["notes"].startswith("A2") for row in rows))

    def test_every_starter_row_is_in_the_full_list_unchanged(self):
        full = {row["front"]: row for row in read_rows(FULL)}

        for row in read_rows(STARTER):
            self.assertEqual(row, full.get(row["front"]), row["front"])

    def test_the_mapping_names_the_starter_file_for_es_ru_only(self):
        self.assertEqual(STARTER_SETS, {ES: STARTER})


class StarterSetSeedingTests(AuthedTestCase):
    def open_dashboard(self, mode=ES):
        session = self.client.session
        session["language_mode"] = mode
        session.save()
        return self.client.get(reverse("core:dashboard"))

    def global_deck(self, mode=ES):
        return Deck.objects.get(owner=self.user, language_mode=mode, is_global=True)

    def test_the_dashboard_seeds_an_empty_es_ru_global_deck_with_100_words(self):
        response = self.open_dashboard()

        self.assertEqual(response.status_code, 200)
        deck = self.global_deck()
        self.assertEqual(deck.cards.count(), 100)
        study = response.context["daily_plan"]["study"]
        self.assertEqual(study["available"], 100)
        self.assertTrue(study["has_cards"])

    def test_nouns_get_their_article_so_the_gender_drill_has_nouns(self):
        self.open_dashboard()

        cards = self.global_deck().cards.all()
        with_article = [card for card in cards if card.front.startswith(("el ", "la "))]
        self.assertGreaterEqual(len(with_article), 30)
        for card in with_article:
            self.assertEqual(card.article, card.front.split(" ", 1)[0], card.front)
        self.assertGreaterEqual(Card.objects.for_user(self.user).active().nouns().count(), 30)

    def test_a_second_visit_adds_nothing(self):
        self.open_dashboard()
        self.open_dashboard()

        self.assertEqual(self.global_deck().cards.count(), 100)

    def test_a_deck_holding_one_card_is_not_seeded(self):
        deck = get_global_deck(self.user, ES)
        deck.cards.all().delete()
        Card.objects.create(deck=deck, front="hola", back="привет")

        self.open_dashboard()

        self.assertEqual(deck.cards.count(), 1)

    def test_a_deck_holding_only_an_archived_card_is_not_seeded(self):
        deck = get_global_deck(self.user, ES)
        deck.cards.all().delete()
        Card.objects.create(deck=deck, front="hola", back="привет", is_retired=True)

        self.open_dashboard()

        self.assertEqual(deck.cards.count(), 1)

    def test_the_other_modes_are_not_seeded(self):
        for mode in (Deck.LanguageMode.DE_RU, Deck.LanguageMode.NL_EN, Deck.LanguageMode.DE_ES):
            response = self.open_dashboard(mode)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.global_deck(mode).cards.count(), 0, mode)

    def test_another_account_gets_its_own_seed(self):
        self.open_dashboard()
        other = create_user("other")

        deck = get_global_deck(other, ES)

        self.assertEqual(deck.cards.count(), 100)
        self.assertEqual(Card.objects.filter(deck__owner=self.user).count(), 100)

    def test_a_second_import_of_the_starter_set_that_passed_the_check_adds_nothing(self):
        deck = get_global_deck(self.user, ES)
        self.assertEqual(deck.cards.count(), 100)

        # What a second request would do had it read "no cards" before the first imported.
        result = import_cards_from_csv(deck, STARTER.read_bytes().decode("utf-8-sig"))

        self.assertEqual((result.imported_count, result.skipped_count), (0, 100))
        self.assertEqual(deck.cards.count(), 100)

    def test_the_full_list_imports_on_top_with_100_skipped(self):
        self.open_dashboard()
        deck = self.global_deck()

        result = import_cards_from_csv(deck, FULL.read_bytes().decode("utf-8-sig"))

        self.assertEqual(result.errors, [])
        self.assertEqual(result.skipped_count, 100)
        self.assertEqual(deck.cards.count(), 1048)

    def test_a_missing_file_leaves_the_deck_empty_and_the_page_working(self):
        with (
            patch.dict(STARTER_SETS, {ES: CARD_SETS / "missing.csv"}),
            self.assertLogs("decks.services", level="ERROR") as logs,
        ):
            response = self.open_dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.global_deck().cards.count(), 0)
        self.assertIn("missing.csv", logs.output[0])

    def test_an_unreadable_file_leaves_the_deck_empty_and_the_page_working(self):
        bad = Path(settings.BASE_DIR, "temp", "starter-not-utf8.csv")
        bad.parent.mkdir(exist_ok=True)
        bad.write_bytes(b"front,back\n\xff\xfe,\xff\n")
        self.addCleanup(bad.unlink)

        with (
            patch.dict(STARTER_SETS, {ES: bad}),
            self.assertLogs("decks.services", level="ERROR"),
        ):
            response = self.open_dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.global_deck().cards.count(), 0)
