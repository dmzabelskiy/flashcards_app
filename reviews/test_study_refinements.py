from django.test import TestCase
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.models import ReviewLog
from reviews.ratings import Rating
from reviews.study import check_answer
from reviews.testutils import check_typed_answer, rendered_submission


class TypoTolerantAnswerTests(TestCase):
    def test_exact_answer_is_correct_without_typo_flag(self):
        result = check_answer("table", "table; masculine noun")

        self.assertTrue(result.is_correct)
        self.assertFalse(result.had_typo)
        self.assertEqual(result.matched_answer, "table")

    def test_one_letter_replacement_counts_as_typo_correct(self):
        result = check_answer("tabke", "table; masculine noun")

        self.assertTrue(result.is_correct)
        self.assertTrue(result.had_typo)
        self.assertEqual(result.matched_answer, "table")

    def test_adjacent_two_letter_swap_counts_as_typo_correct(self):
        result = check_answer("taible", "tiable; option")

        self.assertTrue(result.is_correct)
        self.assertTrue(result.had_typo)
        self.assertEqual(result.matched_answer, "tiable")

    def test_insertion_is_not_accepted_as_typo(self):
        result = check_answer("tables", "table; masculine noun")

        self.assertFalse(result.is_correct)
        self.assertFalse(result.had_typo)
        self.assertEqual(result.matched_answer, "")


class StudyRefinementFlowTests(AuthedTestCase):
    def test_continue_uses_automatic_correct_result(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="der Tisch", back="table; masculine noun")
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "tabke")

        self.assertContains(response, "Correct with typo")
        self.assertContains(response, "table")

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "auto",
                "answer": "tabke",
                "auto_correct": "1",
            },
        )

        self.assertRedirects(response, reverse("reviews:study_complete"))
        session = self.client.session["study_session"]
        self.assertEqual(session["correct"], 1)
        self.assertEqual(session["incorrect"], 0)
        card.refresh_from_db()
        self.assertEqual(card.interval_days, 3)
        self.assertEqual(ReviewLog.objects.get(card=card).rating, Rating.GOOD)

    def test_i_was_right_overrides_incorrect_result(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="abgeben", back="сдавать; отдавать")
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "acceptable")

        self.assertContains(response, "Incorrect")
        self.assertContains(response, "I was right")

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "correct",
                "answer": "acceptable",
                "auto_correct": "0",
            },
        )

        self.assertRedirects(response, reverse("reviews:study_complete"))
        session = self.client.session["study_session"]
        self.assertEqual(session["correct"], 1)
        self.assertEqual(session["incorrect"], 0)
        self.assertEqual(ReviewLog.objects.get(card=card).rating, Rating.GOOD)
