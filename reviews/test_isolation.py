from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck


class ReviewIsolationTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.other = create_user("other")
        self.other_deck = Deck.objects.create(name="Other", owner=self.other)
        self.other_card = Card.objects.create(
            deck=self.other_deck, front="x", back="y", due_at=timezone.now()
        )

    def test_review_card_of_other_user_is_404(self):
        response = self.client.get(
            reverse("reviews:review_card", kwargs={"card_id": self.other_card.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_start_review_ignores_other_users_due_cards(self):
        response = self.client.get(reverse("reviews:start"))
        # No due cards owned by self.user -> redirect to dashboard, never to other's card.
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)

    def test_start_study_of_other_users_deck_is_404(self):
        response = self.client.get(
            reverse("reviews:start_study", kwargs={"deck_id": self.other_deck.pk})
        )
        self.assertEqual(response.status_code, 404)
