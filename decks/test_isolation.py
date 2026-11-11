from django.urls import reverse

from core.testutils import AuthedTestCase, create_user
from decks.models import Deck


class DeckIsolationTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.other = create_user("other")
        self.other_deck = Deck.objects.create(name="Other's deck", owner=self.other)

    def test_list_excludes_other_users_decks(self):
        Deck.objects.create(name="Mine", owner=self.user)
        response = self.client.get(reverse("decks:list"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Other's deck")

    def test_detail_of_other_users_deck_is_404(self):
        response = self.client.get(reverse("decks:detail", kwargs={"pk": self.other_deck.pk}))
        self.assertEqual(response.status_code, 404)

    def test_created_deck_is_owned_by_current_user(self):
        self.client.post(
            reverse("decks:create"), {"name": "New", "description": "", "is_archived": ""}
        )
        deck = Deck.objects.get(name="New")
        self.assertEqual(deck.owner, self.user)
