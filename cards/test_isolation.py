from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck


class CardIsolationTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.my_deck = Deck.objects.create(name="Mine", owner=self.user)
        self.other = create_user("other")
        self.other_deck = Deck.objects.create(name="Other", owner=self.other)
        self.other_card = Card.objects.create(deck=self.other_deck, front="geheim", back="secret")

    def test_browse_excludes_other_users_cards(self):
        Card.objects.create(deck=self.my_deck, front="hallo", back="hi")
        response = self.client.get(reverse("cards:list"))
        self.assertContains(response, "hallo")
        self.assertNotContains(response, "geheim")

    def test_detail_of_other_users_card_is_404(self):
        response = self.client.get(reverse("cards:detail", kwargs={"pk": self.other_card.pk}))
        self.assertEqual(response.status_code, 404)

    def test_export_of_other_users_deck_is_404(self):
        response = self.client.get(
            reverse("cards:export_deck_csv", kwargs={"deck_id": self.other_deck.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_card_form_deck_choices_exclude_other_users_decks(self):
        response = self.client.get(reverse("cards:create"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, f'value="{self.other_deck.pk}"')
