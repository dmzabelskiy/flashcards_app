from unittest.mock import patch

from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.models import DailyStudyProgress, ReviewLog, TrainerAttempt
from reviews.testutils import rendered_submission


class StudyAnswerCheckTokenTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(name="Identical answers", owner=self.user)
        self.cards = [
            Card.objects.create(deck=self.deck, front=front, back="same")
            for front in ("First", "Second")
        ]
        self.url = reverse("reviews:study_current")
        self.start()

    def start(self):
        with patch(
            "reviews.study_views.select_study_queue", return_value=[c.pk for c in self.cards]
        ):
            self.client.get(reverse("reviews:start_study", args=[self.deck.pk]))

    def front_form(self):
        return {
            "action": "check",
            "answer": "same",
            "submission": rendered_submission(self.client.get(self.url)),
        }

    def continue_form(self, checked):
        return {
            "action": "submit_result",
            "result": "auto",
            "auto_correct": "1",
            "submission": rendered_submission(checked),
        }

    def assert_rejected_check(self, form, *, index=0, logs=0):
        with patch("reviews.study_views.check_answer") as checker:
            response = self.client.post(self.url, form)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        checker.assert_not_called()
        self.assertEqual(self.client.session["study_session"]["index"], index)
        self.assertEqual(ReviewLog.objects.count(), logs)
        self.assertEqual(TrainerAttempt.objects.count(), logs)
        self.assertEqual(
            sum(DailyStudyProgress.objects.values_list("correct_count", flat=True)), logs
        )

    def test_fresh_front_and_continue_use_the_same_position_token(self):
        form = self.front_form()
        checked = self.client.post(self.url, form)

        self.assertTrue(checked.context["checked"])
        self.assertTrue(checked.context["is_correct"])
        self.assertEqual(rendered_submission(checked), form["submission"])
        self.assertFalse(ReviewLog.objects.exists(), "checking alone must not write a review")
        self.client.post(self.url, self.continue_form(checked))
        self.assertEqual(self.client.session["study_session"]["index"], 1)
        self.assertEqual(ReviewLog.objects.get().card_id, self.cards[0].pk)

    def test_stale_check_cannot_accept_the_identical_answer_on_the_next_card(self):
        stale = self.front_form()
        checked = self.client.post(self.url, stale)
        self.client.post(self.url, self.continue_form(checked))

        self.assert_rejected_check(stale, index=1, logs=1)
        current = self.client.get(self.url)
        self.assertEqual(current.context["card"].pk, self.cards[1].pk)
        self.assertFalse(current.context["checked"])
        self.assertNotContains(current, 'id="continue-form"')
        self.assertEqual(current.context["user_answer"], "")
        self.cards[1].refresh_from_db()
        self.assertEqual(self.cards[1].review_count, 0)

        fresh = self.front_form()
        self.assertNotEqual(fresh["submission"], stale["submission"])
        checked = self.client.post(self.url, fresh)
        self.assertTrue(checked.context["is_correct"])
        self.client.post(self.url, self.continue_form(checked))
        self.assertEqual(self.client.session["study_session"]["index"], 2)
        self.assertCountEqual(
            ReviewLog.objects.values_list("card_id", flat=True), [c.pk for c in self.cards]
        )

    def test_duplicate_check_is_rejected_and_refresh_recovers_the_first_result(self):
        form = self.front_form()
        checked = self.client.post(self.url, form)
        replay = {**form, "answer": "different"}

        self.assert_rejected_check(replay)
        recovered = self.client.get(self.url)
        self.assertTrue(recovered.context["checked"])
        self.assertEqual(recovered.context["user_answer"], "same")
        self.assertEqual(rendered_submission(recovered), rendered_submission(checked))
        self.client.post(self.url, self.continue_form(recovered))
        self.assertEqual(ReviewLog.objects.count(), 1)
        self.assertEqual(self.client.session["study_session"]["index"], 1)

    def test_missing_and_mismatched_check_tokens_are_rejected(self):
        form = self.front_form()
        missing = {key: value for key, value in form.items() if key != "submission"}
        mismatch = {**form, "submission": form["submission"].rsplit(":", 1)[0] + ":999999"}
        for invalid in (missing, mismatch):
            with self.subTest(form=invalid):
                self.assert_rejected_check(invalid)
                current = self.client.get(self.url)
                self.assertFalse(current.context["checked"])
                self.assertEqual(current.context["user_answer"], "")

    def test_a_prior_sessions_check_is_rejected_even_for_the_same_card(self):
        old = self.front_form()
        self.start()

        self.assert_rejected_check(old)
        fresh = self.front_form()
        self.assertNotEqual(old["submission"], fresh["submission"])
        checked = self.client.post(self.url, fresh)
        self.assertTrue(checked.context["checked"])
        self.assertEqual(checked.context["card"].pk, self.cards[0].pk)

    def test_legacy_session_gets_an_identity_before_its_front_form_is_used(self):
        session = self.client.session
        del session["study_session"]["sid"]
        session.save()

        form = self.front_form()
        self.assertIn("sid", self.client.session["study_session"])
        checked = self.client.post(self.url, form)
        self.assertTrue(checked.context["is_correct"])
        self.client.post(self.url, self.continue_form(checked))
        self.assertEqual(self.client.session["study_session"]["index"], 1)
