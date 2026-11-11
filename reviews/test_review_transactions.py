from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import get_global_deck
from reviews.models import DailyStudyProgress, ReviewLog, TrainerAttempt
from reviews.ratings import Rating
from reviews.scheduler import schedule_card
from reviews.services import (
    CardUnavailable,
    DuplicateSubmission,
    StaleSubmission,
    parse_elapsed_seconds,
    record_review,
)
from reviews.testutils import (
    check_typed_answer,
    post_quick_review,
    rendered_submission,
    submit_typed_result,
)

Trainer = TrainerAttempt.Trainer
User = get_user_model()


class ServiceFixtureMixin:
    def make_card(self, owner=None, **fields):
        owner = owner or self.owner
        deck = fields.pop("deck", None) or get_global_deck(owner, Deck.LanguageMode.DE_RU)
        return Card.objects.create(
            deck=deck, front=fields.pop("front", "Haus"), back=fields.pop("back", "dom"), **fields
        )

    def review(self, card, **overrides):
        kwargs = {
            "user": self.owner,
            "card_id": card.pk,
            "language_mode": Deck.LanguageMode.DE_RU,
            "rating": Rating.GOOD,
            "trainer": Trainer.TYPED_REVIEW,
            "record_daily": True,
        }
        kwargs.update(overrides)
        return record_review(**kwargs)

    def assertNothingWritten(self, card, review_count=0):
        card.refresh_from_db()
        self.assertEqual(card.review_count, review_count)
        self.assertEqual(ReviewLog.objects.count(), review_count)
        self.assertEqual(TrainerAttempt.objects.count(), review_count)
        self.assertEqual(DailyStudyProgress.objects.count(), 0)


class RecordReviewTransactionTests(ServiceFixtureMixin, TestCase):
    def setUp(self):
        self.owner = create_user("reviewer")

    def test_a_review_writes_schedule_log_trainer_and_daily_progress_together(self):
        card = self.make_card()

        outcome = self.review(card, elapsed_seconds=7, trainer=Trainer.SPACED_REVIEW)

        card.refresh_from_db()
        self.assertEqual((card.review_count, card.interval_days), (1, 3))
        log = ReviewLog.objects.get()
        self.assertEqual((log.card, log.rating, log.elapsed_seconds), (card, Rating.GOOD, 7))
        attempt = TrainerAttempt.objects.get()
        self.assertEqual(
            (attempt.owner, attempt.trainer, attempt.is_correct, attempt.response_ms),
            (self.owner, Trainer.SPACED_REVIEW, True, 7000),
        )
        self.assertEqual(DailyStudyProgress.objects.get(deck=card.deck).correct_count, 1)
        self.assertTrue(outcome.correct)
        self.assertFalse(outcome.retired)

    def test_a_failure_writing_the_review_log_rolls_back_the_schedule(self):
        card = self.make_card()

        with (
            patch.object(ReviewLog.objects, "create", side_effect=RuntimeError("log")),
            self.assertRaises(RuntimeError),
        ):
            self.review(card)

        self.assertNothingWritten(card)

    def test_a_failure_recording_the_trainer_attempt_rolls_back_schedule_and_log(self):
        card = self.make_card()

        with (
            patch("reviews.services.record_trainer_attempt", side_effect=RuntimeError("trainer")),
            self.assertRaises(RuntimeError),
        ):
            self.review(card)

        self.assertNothingWritten(card)

    def test_a_failure_recording_daily_progress_rolls_back_everything_before_it(self):
        card = self.make_card()

        with (
            patch("reviews.services.record_daily_study_result", side_effect=RuntimeError("daily")),
            self.assertRaises(RuntimeError),
        ):
            self.review(card)

        self.assertNothingWritten(card)

    def test_a_failure_retiring_the_card_rolls_back_everything_before_it(self):
        card = self.make_card(correct_streak=2)

        with patch.object(Card, "retire", side_effect=RuntimeError("retire")):
            with self.assertRaises(RuntimeError):
                self.review(card, retire=True)

        self.assertNothingWritten(card)
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 2)
        self.assertFalse(card.is_retired)

    def test_retirement_commits_with_the_review_when_the_streak_allows_it(self):
        card = self.make_card(correct_streak=2)

        outcome = self.review(card, retire=True)

        card.refresh_from_db()
        self.assertTrue(outcome.retired)
        self.assertTrue(card.is_retired)
        self.assertEqual(card.correct_streak, 3)

    def test_retirement_request_is_ignored_below_the_streak(self):
        card = self.make_card(correct_streak=0)

        outcome = self.review(card, retire=True)

        card.refresh_from_db()
        self.assertFalse(outcome.retired)
        self.assertFalse(card.is_retired)

    def test_again_does_not_count_toward_daily_progress(self):
        card = self.make_card()

        outcome = self.review(card, rating=Rating.AGAIN)

        self.assertFalse(outcome.correct)
        self.assertFalse(DailyStudyProgress.objects.exists())
        self.assertFalse(TrainerAttempt.objects.get().is_correct)

    def test_daily_progress_is_not_recorded_unless_asked(self):
        card = self.make_card()

        self.review(card, record_daily=False)

        self.assertFalse(DailyStudyProgress.objects.exists())


class SchedulerAtomicityTests(ServiceFixtureMixin, TestCase):
    def setUp(self):
        self.owner = create_user("scheduler")

    def test_direct_schedule_card_calls_roll_back_when_the_log_cannot_be_written(self):
        card = self.make_card()

        with (
            patch.object(ReviewLog.objects, "create", side_effect=RuntimeError("log")),
            self.assertRaises(RuntimeError),
        ):
            schedule_card(card, Rating.GOOD)

        stored = Card.objects.get(pk=card.pk)
        self.assertEqual((stored.review_count, stored.interval_days), (0, 0))
        self.assertFalse(ReviewLog.objects.exists())

    def test_a_stale_card_object_is_scheduled_from_the_current_database_values(self):
        card = self.make_card()
        stale = Card.objects.select_related("deck").get(pk=card.pk)

        schedule_card(card, Rating.GOOD)  # 0 -> 3 days
        result = schedule_card(stale, Rating.GOOD)  # must continue from 3 days, not 0

        self.assertEqual(result.previous_interval_days, 3)
        self.assertEqual(result.new_interval_days, 8)
        stored = Card.objects.get(pk=card.pk)
        self.assertEqual((stored.review_count, stored.interval_days), (2, 8))
        self.assertEqual(stored.correct_streak, 2)
        self.assertEqual(stale.review_count, 2, "the passed object is updated in place")
        self.assertEqual(ReviewLog.objects.count(), 2)

    def test_scheduling_does_not_overwrite_unrelated_concurrent_edits(self):
        card = self.make_card()
        stale = Card.objects.select_related("deck").get(pk=card.pk)
        Card.objects.filter(pk=card.pk).update(notes="edited elsewhere")

        schedule_card(stale, Rating.GOOD)

        self.assertEqual(Card.objects.get(pk=card.pk).notes, "edited elsewhere")


class SubmissionGuardTests(ServiceFixtureMixin, TestCase):
    def setUp(self):
        self.owner = create_user("guard")

    def test_the_same_submission_id_is_accepted_once(self):
        card = self.make_card()
        self.review(card, submission_id="token-1")

        with self.assertRaises(DuplicateSubmission):
            self.review(card, submission_id="token-1")

        self.assertEqual(ReviewLog.objects.count(), 1)
        self.assertEqual(TrainerAttempt.objects.count(), 1)
        self.assertEqual(DailyStudyProgress.objects.get().correct_count, 1)
        card.refresh_from_db()
        self.assertEqual(card.review_count, 1)

    def test_the_database_itself_refuses_two_logs_with_one_submission_id(self):
        card = self.make_card()
        self.review(card, submission_id="token-2")
        log = ReviewLog.objects.get()

        with self.assertRaises(IntegrityError), transaction.atomic():
            ReviewLog.objects.create(
                card=card,
                rating=Rating.GOOD,
                reviewed_at=log.reviewed_at,
                previous_due_at=log.previous_due_at,
                new_due_at=log.new_due_at,
                previous_interval_days=0,
                new_interval_days=3,
                submission_id="token-2",
            )

    def test_reviews_without_a_submission_id_are_never_treated_as_duplicates(self):
        card = self.make_card()

        self.review(card)
        self.review(card)

        self.assertEqual(ReviewLog.objects.count(), 2)

    def test_a_form_rendered_before_the_card_was_reviewed_is_stale(self):
        card = self.make_card()
        self.review(card)

        with self.assertRaises(StaleSubmission):
            self.review(card, expected_review_count=0, submission_id="spaced:old")

        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_the_review_count_the_form_was_rendered_for_is_accepted(self):
        card = self.make_card()
        self.review(card)

        self.review(card, expected_review_count=1, submission_id="spaced:ok")

        self.assertEqual(ReviewLog.objects.count(), 2)


class CardEligibilityTests(ServiceFixtureMixin, TestCase):
    def setUp(self):
        self.owner = create_user("eligible")
        self.other = create_user("someone-else")

    def assertUnavailable(self, card, **overrides):
        with self.assertRaises(CardUnavailable):
            self.review(card, **overrides)
        self.assertEqual(ReviewLog.objects.count(), 0)
        self.assertEqual(TrainerAttempt.objects.count(), 0)
        card.refresh_from_db()
        self.assertEqual(card.review_count, 0)

    def test_another_users_card_is_unavailable(self):
        self.assertUnavailable(self.make_card(owner=self.other))

    def test_inactive_cards_are_unavailable(self):
        for fields in ({"is_suspended": True}, {"is_retired": True}):
            with self.subTest(fields=fields):
                self.assertUnavailable(self.make_card(**fields))

    def test_a_card_in_an_archived_deck_is_unavailable(self):
        deck = Deck.objects.create(name="Old", owner=self.owner, is_archived=True)
        self.assertUnavailable(self.make_card(deck=deck))

    def test_a_card_from_another_language_mode_is_unavailable(self):
        deck = Deck.objects.create(name="ES", owner=self.owner, language_mode="DE-ES")
        self.assertUnavailable(self.make_card(deck=deck))

    def test_a_missing_card_is_unavailable(self):
        card = self.make_card()
        pk = card.pk
        card.delete()
        with self.assertRaises(CardUnavailable):
            record_review(
                user=self.owner,
                card_id=pk,
                language_mode=Deck.LanguageMode.DE_RU,
                rating=Rating.GOOD,
                trainer=Trainer.TYPED_REVIEW,
            )


class ParseElapsedSecondsTests(TestCase):
    def test_valid_values(self):
        self.assertEqual(parse_elapsed_seconds("0"), 0)
        self.assertEqual(parse_elapsed_seconds(" 42 "), 42)
        self.assertEqual(parse_elapsed_seconds("86400"), 86400)

    def test_absent_values_mean_unknown(self):
        for raw in (None, "", "   "):
            self.assertIsNone(parse_elapsed_seconds(raw))

    def test_malformed_negative_and_absurd_values_are_rejected(self):
        for raw in ("abc", "-1", "-0", "1.5", "1e3", "1_000", "NaN", "inf", "86401", "10" * 20):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_elapsed_seconds(raw)


class QuickReviewReplayTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.card = Card.objects.create(
            deck=get_global_deck(self.user, Deck.LanguageMode.DE_RU), front="Haus", back="dom"
        )
        self.other_due = Card.objects.create(deck=self.card.deck, front="Auto", back="mashina")

    def url(self, card):
        return reverse("reviews:review_card", kwargs={"card_id": card.pk})

    def test_resubmitting_the_rendered_form_does_not_review_the_card_twice(self):
        page = self.client.get(self.url(self.card), {"answer": 1})
        data = {"rating": "good", "submission": rendered_submission(page)}

        first = self.client.post(self.url(self.card), data)
        second = self.client.post(self.url(self.card), data, follow=True)

        self.assertEqual(first.status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.review_count, 1)
        self.assertEqual(ReviewLog.objects.filter(card=self.card).count(), 1)
        self.assertEqual(TrainerAttempt.objects.filter(card=self.card).count(), 1)
        self.assertContains(second, "already reviewed")

    def test_two_tabs_rendered_for_the_same_card_state_review_it_once(self):
        tab_a = rendered_submission(self.client.get(self.url(self.card), {"answer": 1}))
        tab_b = rendered_submission(self.client.get(self.url(self.card), {"answer": 1}))

        self.client.post(self.url(self.card), {"rating": "good", "submission": tab_a})
        response = self.client.post(self.url(self.card), {"rating": "again", "submission": tab_b})

        self.assertEqual(response.status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.review_count, 1)
        self.assertEqual(self.card.lapse_count, 0)

    def test_a_post_without_the_rendered_token_is_ignored(self):
        huge = f"spaced:{self.card.pk}:" + "9" * 5000
        for data in (
            {"rating": "good"},
            {"rating": "good", "submission": "spaced:1:x"},
            {"rating": "good", "submission": huge},
        ):
            with self.subTest(data=data):
                response = self.client.post(self.url(self.card), data)
                self.assertEqual(response.status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.review_count, 0)
        self.assertFalse(ReviewLog.objects.exists())

    def test_a_token_rendered_for_another_card_is_ignored(self):
        token = rendered_submission(self.client.get(self.url(self.other_due), {"answer": 1}))

        self.client.post(self.url(self.card), {"rating": "good", "submission": token})

        self.card.refresh_from_db()
        self.assertEqual(self.card.review_count, 0)

    def test_the_next_review_after_a_valid_one_still_works(self):
        post_quick_review(self.client, self.card, "good")

        response = post_quick_review(self.client, self.card, "good")

        self.assertEqual(response.status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.review_count, 2)

    def test_elapsed_time_is_stored_on_the_log_and_the_trainer_attempt(self):
        post_quick_review(self.client, self.card, "good", elapsed_seconds="12")

        self.assertEqual(ReviewLog.objects.get().elapsed_seconds, 12)
        self.assertEqual(TrainerAttempt.objects.get().response_ms, 12000)

    def test_a_rejected_elapsed_value_leaves_the_form_usable(self):
        page = self.client.get(self.url(self.card), {"answer": 1})
        token = rendered_submission(page)

        bad = self.client.post(
            self.url(self.card),
            {"rating": "good", "submission": token, "elapsed_seconds": "-3"},
        )
        retry = self.client.post(
            self.url(self.card),
            {"rating": "good", "submission": token, "elapsed_seconds": "3"},
        )

        self.assertEqual(bad.status_code, 400)
        self.assertEqual(retry.status_code, 302)
        self.assertEqual(ReviewLog.objects.get().elapsed_seconds, 3)

    def test_inactive_cards_are_not_shown_for_review(self):
        for fields in ({"is_suspended": True}, {"is_retired": True}):
            with self.subTest(fields=fields):
                Card.objects.filter(pk=self.card.pk).update(**fields)

                response = self.client.get(self.url(self.card), {"answer": 1})

                self.assertRedirects(response, self.card.get_absolute_url())
                Card.objects.filter(pk=self.card.pk).update(is_suspended=False, is_retired=False)

    def test_a_card_that_became_inactive_after_rendering_is_not_reviewed(self):
        token = rendered_submission(self.client.get(self.url(self.card), {"answer": 1}))
        Card.objects.filter(pk=self.card.pk).update(is_suspended=True)

        response = self.client.post(self.url(self.card), {"rating": "good", "submission": token})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ReviewLog.objects.exists())


class TypedStudyReplayTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.cards = [
            Card.objects.create(deck=self.deck, front=f"wort{i}", back=f"слово{i}")
            for i in range(3)
        ]
        self.client.get(reverse("reviews:start_study", args=[self.deck.pk]))
        self.url = reverse("reviews:study_current")

    def current_card(self):
        session = self.client.session["study_session"]
        return Card.objects.get(pk=session["queue"][session["index"]])

    def checked_form(self, answer="x"):
        page = check_typed_answer(self.client, answer)
        return {
            "action": "submit_result",
            "result": "auto",
            "answer": answer,
            "auto_correct": "0",
            "submission": rendered_submission(page),
        }

    def test_the_rendered_continue_form_carries_a_one_time_token(self):
        page = check_typed_answer(self.client, "x")

        self.assertContains(page, 'name="submission"')

    def test_a_duplicate_submission_does_not_advance_the_queue_or_the_card_twice(self):
        card = self.current_card()
        data = self.checked_form()

        self.client.post(self.url, data)
        second = self.client.post(self.url, data)

        self.assertRedirects(second, self.url)
        session = self.client.session["study_session"]
        self.assertEqual(session["index"], 1)
        self.assertEqual((session["correct"], session["incorrect"]), (0, 1))
        self.assertEqual(session["incorrect_ids"], [card.pk])
        card.refresh_from_db()
        self.assertEqual(card.review_count, 1)
        self.assertEqual(ReviewLog.objects.count(), 1)
        self.assertEqual(TrainerAttempt.objects.count(), 1)

    def test_replaying_an_old_form_after_the_queue_moved_on_is_ignored(self):
        old = self.checked_form()
        self.client.post(self.url, old)
        self.client.post(self.url, self.checked_form())

        self.client.post(self.url, old)

        self.assertEqual(self.client.session["study_session"]["index"], 2)
        self.assertEqual(ReviewLog.objects.count(), 2)

    def test_a_submission_without_a_token_is_ignored(self):
        data = self.checked_form()
        del data["submission"]

        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url)
        self.assertEqual(self.client.session["study_session"]["index"], 0)
        self.assertFalse(ReviewLog.objects.exists())

    def test_a_token_from_another_session_is_ignored(self):
        data = self.checked_form()
        self.client.get(reverse("reviews:start_study", args=[self.deck.pk]))  # new session

        self.client.post(self.url, data)

        self.assertEqual(self.client.session["study_session"]["index"], 0)
        self.assertFalse(ReviewLog.objects.exists())

    def test_a_token_for_another_card_is_ignored(self):
        data = self.checked_form()
        token = data["submission"]
        data["submission"] = token.rsplit(":", 1)[0] + ":999999"

        self.client.post(self.url, data)

        self.assertEqual(self.client.session["study_session"]["index"], 0)
        self.assertFalse(ReviewLog.objects.exists())

    def test_another_users_token_is_ignored(self):
        data = self.checked_form()
        stranger = User.objects.create_user("stranger", password="pw-testing-123")
        self.client.force_login(stranger)

        response = self.client.post(self.url, data)

        self.assertRedirects(response, reverse("decks:list"))
        self.assertFalse(ReviewLog.objects.exists())

    def test_a_session_whose_answer_committed_but_was_not_saved_does_not_get_stuck(self):
        card = self.current_card()
        token = self.checked_form()["submission"]
        record_review(
            user=self.user,
            card_id=card.pk,
            language_mode=Deck.LanguageMode.DE_RU,
            rating=Rating.AGAIN,
            trainer=Trainer.TYPED_REVIEW,
            submission_id=token,
        )

        response = self.client.get(self.url)

        session = self.client.session["study_session"]
        self.assertEqual(session["index"], 1)
        self.assertEqual((session["correct"], session["incorrect"]), (0, 1))
        self.assertEqual(session["incorrect_ids"], [card.pk])
        self.assertEqual(response.context["card"].pk, session["queue"][1])
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_self_assessment_still_overrides_an_incorrect_check(self):
        card = self.current_card()
        data = self.checked_form()
        data["result"] = "correct"

        self.client.post(self.url, data)

        session = self.client.session["study_session"]
        self.assertEqual((session["correct"], session["incorrect"]), (1, 0))
        self.assertEqual(session["incorrect_ids"], [])
        self.assertEqual(ReviewLog.objects.get(card=card).rating, Rating.GOOD)
        self.assertEqual(DailyStudyProgress.objects.get(deck=self.deck).correct_count, 1)

    def test_a_card_that_stopped_being_eligible_is_skipped_not_studied(self):
        queue = self.client.session["study_session"]["queue"]
        Card.objects.filter(pk=queue[0]).update(is_retired=True)

        response = self.client.get(self.url)

        self.assertEqual(response.context["card"].pk, queue[1])
        self.assertEqual(self.client.session["study_session"]["index"], 1)

    def test_a_card_suspended_after_the_check_is_not_recorded_on_submit(self):
        card = self.current_card()
        data = self.checked_form()
        Card.objects.filter(pk=card.pk).update(is_suspended=True)

        self.client.post(self.url, data)

        self.assertFalse(ReviewLog.objects.exists())
        self.assertEqual(self.client.session["study_session"]["index"], 1)

    def test_a_card_that_vanishes_during_submit_is_skipped_with_a_note(self):
        data = self.checked_form()

        with patch("reviews.study_views.record_review", side_effect=CardUnavailable(1)):
            response = self.client.post(self.url, data, follow=True)

        self.assertContains(response, "no longer available")
        self.assertEqual(self.client.session["study_session"]["index"], 1)

    def test_cards_from_another_language_mode_are_never_studied(self):
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get(self.url, follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse("core:dashboard"))
        self.assertFalse(ReviewLog.objects.exists())

    def test_the_whole_session_commits_once_per_card_when_finished(self):
        for _ in range(3):
            response = submit_typed_result(self.client, result="correct", answer="x")

        self.assertRedirects(response, reverse("reviews:study_complete"))
        self.assertEqual(ReviewLog.objects.count(), 3)
        self.assertEqual(DailyStudyProgress.objects.get(deck=self.deck).correct_count, 3)


class ActiveTimeTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.card = Card.objects.create(
            deck=get_global_deck(self.user, Deck.LanguageMode.DE_RU), front="Haus", back="dom"
        )

    def active_minutes(self):
        from reviews.analytics import build_personal_analytics

        return build_personal_analytics(self.user, Deck.LanguageMode.DE_RU, 7)["active_minutes"]

    def test_a_timed_quick_review_counts_its_seconds_once(self):
        post_quick_review(self.client, self.card, "good", elapsed_seconds="120")

        self.assertEqual(ReviewLog.objects.get().elapsed_seconds, 120)
        self.assertEqual(TrainerAttempt.objects.get().response_ms, 120000)
        self.assertEqual(self.active_minutes(), 2)

    def test_typed_review_attempts_do_not_add_time_on_top_of_the_log(self):
        record_review(
            user=self.user,
            card_id=self.card.pk,
            language_mode=Deck.LanguageMode.DE_RU,
            rating=Rating.GOOD,
            trainer=Trainer.TYPED_REVIEW,
            elapsed_seconds=60,
        )

        self.assertEqual(self.active_minutes(), 1)

    def test_other_trainers_are_still_timed_through_their_own_attempts(self):
        from reviews.analytics import record_trainer_attempt

        record_trainer_attempt(
            user=self.user,
            trainer=Trainer.ARTICLE,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=True,
            response_ms=180_000,
        )

        self.assertEqual(self.active_minutes(), 3)

    def test_review_and_drill_time_add_up_without_double_counting(self):
        from reviews.analytics import record_trainer_attempt

        post_quick_review(self.client, self.card, "good", elapsed_seconds="60")
        record_trainer_attempt(
            user=self.user,
            trainer=Trainer.ARTICLE,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=True,
            response_ms=60_000,
        )

        self.assertEqual(self.active_minutes(), 2)

    def test_old_reviews_outside_the_window_are_not_counted(self):
        post_quick_review(self.client, self.card, "good", elapsed_seconds="600")
        past = timezone.now() - timezone.timedelta(days=30)
        ReviewLog.objects.update(reviewed_at=past)
        TrainerAttempt.objects.update(occurred_at=past)

        self.assertEqual(self.active_minutes(), 0)

    def assert_deleted_review_keeps_time(self, trainer):
        record_review(
            user=self.user,
            card_id=self.card.pk,
            language_mode=Deck.LanguageMode.DE_RU,
            rating=Rating.GOOD,
            trainer=trainer,
            elapsed_seconds=120,
        )
        self.assertEqual(self.active_minutes(), 2, "the mirrored rows count only once")

        self.card.delete()

        self.assertFalse(ReviewLog.objects.exists())
        retained = TrainerAttempt.objects.get()
        self.assertIsNone(retained.card_id)
        self.assertEqual(retained.response_ms, 120_000)
        self.assertEqual(self.active_minutes(), 2, "deletion must retain the review's time")

    def test_deleted_typed_review_keeps_its_retained_attempt_time(self):
        self.assert_deleted_review_keeps_time(Trainer.TYPED_REVIEW)

    def test_deleted_spaced_review_keeps_its_retained_attempt_time(self):
        self.assert_deleted_review_keeps_time(Trainer.SPACED_REVIEW)

    def test_orphan_time_mixes_with_live_reviews_legacy_logs_and_other_trainers(self):
        from reviews.analytics import record_trainer_attempt

        for trainer in (Trainer.TYPED_REVIEW, Trainer.SPACED_REVIEW):
            card = Card.objects.create(deck=self.card.deck, front=trainer, back="word")
            record_review(
                user=self.user,
                card_id=card.pk,
                language_mode=Deck.LanguageMode.DE_RU,
                rating=Rating.GOOD,
                trainer=trainer,
                elapsed_seconds=120,
            )
            card.delete()
        post_quick_review(self.client, self.card, "good", elapsed_seconds="60")
        # Direct/legacy scheduler calls have no mirrored event.
        schedule_card(self.card, Rating.GOOD, elapsed_seconds=60)
        record_trainer_attempt(
            user=self.user,
            trainer=Trainer.ARTICLE,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=True,
            response_ms=180_000,
        )
        record_trainer_attempt(
            user=self.user,
            trainer=Trainer.TYPED_REVIEW,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=True,
        )

        self.assertEqual(ReviewLog.objects.count(), 2)
        self.assertEqual(self.active_minutes(), 9)

    def test_orphan_review_time_is_scoped_by_owner_mode_and_period(self):
        from reviews.analytics import build_personal_analytics

        stranger = create_user("timing-stranger")
        now = timezone.now()
        fixtures = (
            (self.user, Deck.LanguageMode.DE_RU, now, 120),
            (self.user, Deck.LanguageMode.DE_RU, now - timezone.timedelta(days=8), 180),
            (self.user, Deck.LanguageMode.DE_RU, now - timezone.timedelta(days=40), 600),
            (self.user, Deck.LanguageMode.DE_RU, now + timezone.timedelta(days=1), 600),
            (self.user, Deck.LanguageMode.DE_ES, now, 240),
            (stranger, Deck.LanguageMode.DE_RU, now, 300),
        )
        for trainer in (Trainer.TYPED_REVIEW, Trainer.SPACED_REVIEW):
            for index, (owner, mode, occurred_at, seconds) in enumerate(fixtures):
                card = Card.objects.create(
                    deck=get_global_deck(owner, mode), front=f"{trainer}-{index}", back="word"
                )
                record_review(
                    user=owner,
                    card_id=card.pk,
                    language_mode=mode,
                    rating=Rating.GOOD,
                    trainer=trainer,
                    elapsed_seconds=seconds,
                )
                TrainerAttempt.objects.filter(card=card).update(occurred_at=occurred_at)
                ReviewLog.objects.filter(card=card).update(reviewed_at=occurred_at)
                card.delete()

        self.assertFalse(ReviewLog.objects.exists())
        self.assertEqual(self.active_minutes(), 4)
        self.assertEqual(
            build_personal_analytics(self.user, Deck.LanguageMode.DE_RU, 30)["active_minutes"], 10
        )
        self.assertEqual(
            build_personal_analytics(self.user, Deck.LanguageMode.DE_ES, 7)["active_minutes"], 8
        )
        self.assertEqual(
            build_personal_analytics(stranger, Deck.LanguageMode.DE_RU, 7)["active_minutes"], 10
        )
