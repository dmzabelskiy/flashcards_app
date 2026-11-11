from django.test import TestCase

from core.testutils import AuthedTestCase, create_user
from decks.models import Deck


class DeckModelTests(TestCase):
    def test_deck_creation_defaults_to_active(self):
        owner = create_user()
        deck = Deck.objects.create(name="German A1", description="Starter vocabulary", owner=owner)

        self.assertEqual(deck.name, "German A1")
        self.assertFalse(deck.is_archived)
        self.assertEqual(deck.language_mode, Deck.LanguageMode.DE_RU)
        self.assertIsNotNone(deck.created_at)
        self.assertIsNotNone(deck.updated_at)


class DeckListViewTests(AuthedTestCase):
    def test_deck_list_filters_by_session_language_mode(self):
        Deck.objects.create(name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user)
        Deck.objects.create(name="ES", language_mode=Deck.LanguageMode.DE_ES, owner=self.user)
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get("/decks/")

        self.assertEqual([deck.name for deck in response.context["decks"]], ["ES"])
