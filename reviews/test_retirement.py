from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from decks.services import get_global_deck
from reviews.testutils import check_typed_answer, post_quick_review, rendered_submission


class SpacedReviewRetirementTests(AuthedTestCase):
    def _review_url(self, card):
        return reverse("reviews:review_card", kwargs={"card_id": card.pk})

    def test_retire_button_shown_for_global_deck_card_near_threshold(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head", correct_streak=2)

        response = self.client.get(f"{self._review_url(card)}?answer=1")

        self.assertContains(response, "Retire word")

    def test_retire_button_not_shown_for_non_global_deck_card(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="Kopf", back="head", correct_streak=2)

        response = self.client.get(f"{self._review_url(card)}?answer=1")

        self.assertNotContains(response, "Retire word")

    def test_retire_button_not_shown_for_already_retired_card(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(
            deck=deck,
            front="Kopf",
            back="head",
            correct_streak=2,
            is_retired=True,
        )

        response = self.client.get(f"{self._review_url(card)}?answer=1")

        # A retired card is not put in front of the learner for review at all.
        self.assertRedirects(response, card.get_absolute_url())

    def test_retire_button_not_shown_below_threshold(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head", correct_streak=1)

        response = self.client.get(f"{self._review_url(card)}?answer=1")

        self.assertNotContains(response, "Retire word")

    def test_posting_retire_schedules_good_and_retires_card(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head", correct_streak=2)

        response = post_quick_review(self.client, card, "good", retire="1")

        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 3)
        self.assertTrue(card.is_retired)
        self.assertIsNotNone(card.retired_at)
        self.assertNotIn(card, Card.objects.active())

    def test_posting_without_retire_leaves_card_active(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="Kopf", back="head", correct_streak=2)

        post_quick_review(self.client, card, "good")

        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 3)
        self.assertFalse(card.is_retired)


class TypedStudyWrongCheckRetirementTests(AuthedTestCase):
    def test_wrong_check_still_offers_retire_and_retiring_counts_as_correct(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(deck=deck, front="bekommen", back="получать", correct_streak=2)
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "wrong answer")

        self.assertContains(response, "Incorrect")
        self.assertContains(response, "Retire word")

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "retire",
                "answer": "wrong answer",
                "auto_correct": "0",
            },
        )

        self.assertRedirects(response, reverse("reviews:study_complete"))
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 3)
        self.assertTrue(card.is_retired)

        session = self.client.session
        self.assertEqual(session["study_session"]["correct"], 1)
        self.assertEqual(session["study_session"]["incorrect"], 0)
