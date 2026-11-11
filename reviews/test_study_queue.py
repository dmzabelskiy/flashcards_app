from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import get_global_deck
from reviews.daily import DAILY_TARGET
from reviews.models import DailyStudyProgress, ReviewLog, TrainerAttempt
from reviews.study_queue import STUDY_SESSION_LIMIT, select_study_queue
from reviews.testutils import rendered_submission, submit_typed_result


def make_cards(deck, count, *, prefix="w", due_in_days=None, **fields):
    """Create ``count`` cards; ``due_in_days(i)`` sets each card's due date."""
    now = timezone.now()
    cards = []
    for index in range(count):
        due_at = (
            now + timezone.timedelta(days=due_in_days(index))
            if due_in_days
            else now - timezone.timedelta(days=1)
        )
        cards.append(
            Card.objects.create(
                deck=deck, front=f"{prefix}{index}", back=f"b{index}", due_at=due_at, **fields
            )
        )
    return cards


class SelectStudyQueueTests(TestCase):
    def setUp(self):
        self.user = create_user("queue")
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)

    def select(self, limit=STUDY_SESSION_LIMIT):
        return select_study_queue(Card.objects.for_user(self.user).active(), limit)

    def test_the_limit_is_twenty(self):
        self.assertEqual(STUDY_SESSION_LIMIT, 20)

    def test_never_selects_more_than_the_limit(self):
        make_cards(self.deck, 35)

        self.assertEqual(len(self.select()), 20)
        self.assertEqual(len(self.select(5)), 5)

    def test_all_words_are_read_once_and_shuffled_once(self):
        cards = make_cards(self.deck, 45, due_in_days=lambda i: i - 10)

        with (
            self.assertNumQueries(1),
            patch("reviews.study_queue.random.shuffle") as shuffle,
        ):
            queue = self.select(None)

        self.assertCountEqual(queue, [c.pk for c in cards])
        shuffle.assert_called_once_with(queue)

    def test_due_cards_come_before_any_active_fill(self):
        due = make_cards(self.deck, 8, prefix="due")
        make_cards(self.deck, 30, prefix="later", due_in_days=lambda i: i + 1)

        queue = self.select()

        self.assertEqual(len(queue), 20)
        self.assertTrue({card.pk for card in due} <= set(queue))

    def test_a_short_queue_is_filled_with_the_cards_due_soonest(self):
        due = make_cards(self.deck, 3, prefix="due")
        later = make_cards(self.deck, 40, prefix="later", due_in_days=lambda i: i + 1)

        queue = self.select()

        self.assertEqual(set(queue), {c.pk for c in due} | {c.pk for c in later[:17]})

    def test_a_review_between_selection_reads_cannot_duplicate_a_card(self):
        due = make_cards(self.deck, 1)[0]
        future = make_cards(self.deck, 1, prefix="future", due_in_days=lambda i: 10)[0]
        reads = []

        def interleave(execute, sql, params, many, context):
            if sql.startswith("SELECT") and 'FROM "cards_card"' in sql:
                reads.append(sql)
                if len(reads) == 2:
                    # Deterministically simulate a review committing after the
                    # due selection but before a separate future-card fill.
                    Card.objects.filter(pk=due.pk).update(
                        due_at=timezone.now() + timezone.timedelta(days=1)
                    )
            return execute(sql, params, many, context)

        with connection.execute_wrapper(interleave):
            queue = self.select(2)

        self.assertCountEqual(queue, [due.pk, future.pk])
        self.assertEqual(len(queue), len(set(queue)))
        self.assertEqual(len(reads), 1, "selection must use a coherent, bounded read")

    def test_more_due_cards_than_the_limit_selects_the_longest_overdue(self):
        now = timezone.now()
        cards = [
            Card.objects.create(
                deck=self.deck,
                front=f"w{i}",
                back="b",
                due_at=now - timezone.timedelta(days=i + 1),
            )
            for i in range(30)
        ]

        queue = self.select()

        self.assertEqual(set(queue), {c.pk for c in cards[10:]})

    def test_only_the_selected_pool_is_shuffled(self):
        due = make_cards(self.deck, 5, prefix="due")
        make_cards(self.deck, 30, prefix="later", due_in_days=lambda i: i + 1)
        seen = []

        with patch("reviews.study_queue.random.shuffle", side_effect=seen.append):
            self.select(10)

        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 10)
        self.assertEqual({c.pk for c in due}, set(seen[0][:5]), "due cards lead the pool")

    def test_fewer_cards_than_the_limit_gives_a_shorter_queue(self):
        cards = make_cards(self.deck, 4)

        self.assertCountEqual(self.select(), [c.pk for c in cards])

    def test_no_cards_and_a_zero_limit_give_an_empty_queue(self):
        self.assertEqual(self.select(), [])
        make_cards(self.deck, 2)
        self.assertEqual(self.select(0), [])


class StartStudyQueueTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.start = reverse("reviews:start_study", args=[self.deck.pk])

    def queue(self):
        return self.client.session["study_session"]["queue"]

    def test_ordinary_study_is_bounded_to_twenty_cards(self):
        make_cards(self.deck, 45)

        response = self.client.get(self.start)

        self.assertRedirects(response, reverse("reviews:study_current"))
        self.assertEqual(len(self.queue()), 20)
        self.assertEqual(self.client.session["study_session"]["kind"], "deck")

    def test_ordinary_study_prefers_due_cards(self):
        due = make_cards(self.deck, 6, prefix="due")
        make_cards(self.deck, 30, prefix="later", due_in_days=lambda i: i + 1)

        self.client.get(self.start)

        self.assertTrue({c.pk for c in due} <= set(self.queue()))

    def test_daily_study_uses_the_remaining_goal(self):
        make_cards(self.deck, 40)
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=15
        )

        self.client.get(f"{self.start}?daily=1")

        self.assertEqual(len(self.queue()), 5)
        self.assertEqual(self.client.session["study_session"]["kind"], "daily")

    def test_daily_study_with_an_untouched_goal_is_capped_at_twenty(self):
        make_cards(self.deck, 40)

        self.client.get(f"{self.start}?daily=1")

        self.assertEqual(len(self.queue()), 20)

    def test_a_completed_goal_offers_optional_extra_practice(self):
        make_cards(self.deck, 40)
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=DAILY_TARGET + 3
        )

        response = self.client.get(f"{self.start}?daily=1", follow=True)

        self.assertEqual(len(self.queue()), 20)
        self.assertEqual(self.client.session["study_session"]["kind"], "extra")
        self.assertContains(response, "optional extra practice")

    def test_daily_progress_from_another_day_does_not_shrink_the_session(self):
        make_cards(self.deck, 40)
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        DailyStudyProgress.objects.create(deck=self.deck, date=yesterday, correct_count=18)

        self.client.get(f"{self.start}?daily=1")

        self.assertEqual(len(self.queue()), 20)

    def test_a_short_daily_session_says_so_and_is_truthful(self):
        make_cards(self.deck, 3)

        response = self.client.get(f"{self.start}?daily=1", follow=True)

        self.assertEqual(len(self.queue()), 3)
        self.assertContains(response, "Only 3 active words")
        self.assertContains(response, "1 / 3")

    def test_a_deck_without_active_cards_explains_and_returns_to_the_deck(self):
        make_cards(self.deck, 2, is_retired=True)
        make_cards(self.deck, 2, prefix="s", is_suspended=True)

        response = self.client.get(self.start, follow=True)

        self.assertRedirects(response, self.deck.get_absolute_url())
        self.assertContains(response, "no active cards")
        self.assertNotIn("study_session", self.client.session)

    def test_inactive_and_archived_cards_are_never_queued(self):
        active = make_cards(self.deck, 2, prefix="a")
        make_cards(self.deck, 2, prefix="r", is_retired=True)
        make_cards(self.deck, 2, prefix="s", is_suspended=True)
        archived = Deck.objects.create(name="Old", owner=self.user, is_archived=True)
        make_cards(archived, 2, prefix="x")

        self.client.get(self.start)
        self.client.get(reverse("reviews:start_study", args=[archived.pk]))

        self.assertCountEqual(self.queue(), [c.pk for c in active])

    def test_only_the_decks_own_cards_are_queued(self):
        other_deck = Deck.objects.create(name="Other", owner=self.user)
        mine = make_cards(self.deck, 3)
        make_cards(other_deck, 3, prefix="o")
        stranger = create_user("stranger")
        make_cards(get_global_deck(stranger, Deck.LanguageMode.DE_RU), 3, prefix="s")

        self.client.get(self.start)

        self.assertCountEqual(self.queue(), [c.pk for c in mine])

    def test_another_users_deck_and_another_mode_are_404(self):
        stranger_deck = get_global_deck(create_user("stranger"), Deck.LanguageMode.DE_RU)
        spanish = Deck.objects.create(name="ES", owner=self.user, language_mode="DE-ES")

        for deck in (stranger_deck, spanish):
            response = self.client.get(reverse("reviews:start_study", args=[deck.pk]))
            self.assertEqual(response.status_code, 404)

    def test_the_study_direction_is_kept(self):
        make_cards(self.deck, 2)

        self.client.get(f"{self.start}?daily=1&direction=reverse")

        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")


class AllWordStudyTests(AuthedTestCase):
    """All-word sessions share the existing bounded study lifecycle."""

    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.start = reverse("reviews:start_study", args=[self.deck.pk])

    def queue(self):
        return self.client.session["study_session"]["queue"]

    def start_all(self, **params):
        return self.client.get(self.start, {"all": "1", **params})

    def test_all_includes_more_than_twenty_and_already_seen_words(self):
        cards = make_cards(self.deck, 25)
        self.client.get(self.start)
        submit_typed_result(self.client, result="correct")
        seen_id = ReviewLog.objects.get().card_id

        self.start_all(daily="1")

        self.assertCountEqual(self.queue(), [c.pk for c in cards])
        self.assertIn(seen_id, self.queue())
        self.assertEqual(self.client.session["study_session"]["kind"], "all")

    def test_all_is_optional_before_and_after_the_goal(self):
        cards = make_cards(self.deck, 30)
        for correct in (0, DAILY_TARGET, DAILY_TARGET + 5):
            with self.subTest(correct=correct):
                DailyStudyProgress.objects.update_or_create(
                    deck=self.deck,
                    date=timezone.localdate(),
                    defaults={"correct_count": correct},
                )
                self.start_all()
                self.assertCountEqual(self.queue(), [c.pk for c in cards])

    def test_all_stays_with_the_selected_active_deck_owner_and_mode(self):
        cards = make_cards(self.deck, 25)
        make_cards(self.deck, 2, prefix="retired", is_retired=True)
        make_cards(self.deck, 2, prefix="suspended", is_suspended=True)
        other = Deck.objects.create(name="Other", owner=self.user)
        archived = Deck.objects.create(name="Archived", owner=self.user, is_archived=True)
        spanish = get_global_deck(self.user, "DE-ES")
        foreign = get_global_deck(create_user("foreign-all"), "DE-RU")
        for deck in (other, archived, spanish, foreign):
            make_cards(deck, 2)

        self.start_all()
        self.assertCountEqual(self.queue(), [c.pk for c in cards])
        for deck in (spanish, foreign):
            self.assertEqual(
                self.client.get(
                    reverse("reviews:start_study", args=[deck.pk]), {"all": "1"}
                ).status_code,
                404,
            )
        self.client.get(reverse("reviews:start_study", args=[archived.pk]), {"all": "1"})
        self.assertCountEqual(self.queue(), [c.pk for c in cards])

    def test_short_and_empty_all_sessions(self):
        response = self.start_all()
        self.assertRedirects(response, self.deck.get_absolute_url())
        self.assertNotIn("study_session", self.client.session)
        cards = make_cards(self.deck, 3)
        self.start_all()
        self.assertCountEqual(self.queue(), [c.pk for c in cards])

    def test_ordinary_completion_offers_all_without_changing_study_again(self):
        make_cards(self.deck, 1)
        self.client.get(self.start)
        submit_typed_result(self.client, result="correct")

        page = self.client.get(reverse("reviews:study_complete"))

        self.assertContains(page, f'href="{self.start}?all=1"')
        self.assertContains(page, f'href="{self.start}">Study again</a>')

    def test_all_finishes_every_word_and_repeats_with_reverse_preserved(self):
        cards = make_cards(self.deck, 25)
        self.start_all(direction="reverse")
        for _ in cards:
            response = submit_typed_result(self.client, result="correct")

        self.assertRedirects(response, reverse("reviews:study_complete"))
        self.assertEqual(self.client.session["study_session"]["correct"], 25)
        self.assertEqual(ReviewLog.objects.count(), 25)
        self.assertEqual(TrainerAttempt.objects.count(), 25)
        self.assertEqual(DailyStudyProgress.objects.get(deck=self.deck).correct_count, 25)
        page = self.client.get(reverse("reviews:study_complete"))
        self.assertContains(page, f'href="{self.start}?all=1&amp;direction=reverse"')
        self.assertContains(page, "Practice all words")
        self.start_all(direction="reverse")
        self.assertCountEqual(self.queue(), [c.pk for c in cards])
        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")

    def test_all_skips_unavailable_words_without_counting_them(self):
        make_cards(self.deck, 25)
        self.start_all()
        queue = list(self.queue())
        Card.objects.filter(pk=queue[0]).update(is_suspended=True)
        Card.objects.filter(pk=queue[1]).update(is_retired=True)
        Card.objects.filter(pk=queue[2]).delete()

        page = self.client.get(reverse("reviews:study_current"))

        self.assertEqual(page.context["card"].pk, queue[3])
        self.assertEqual(self.client.session["study_session"]["index"], 3)
        self.assertEqual(ReviewLog.objects.count(), 0)
        self.assertFalse(DailyStudyProgress.objects.exists())

    def test_all_result_replay_and_session_recovery_count_only_once(self):
        make_cards(self.deck, 25)
        self.start_all()
        stale = dict(self.client.session["study_session"])
        page = self.client.get(reverse("reviews:study_current"))
        data = {
            "action": "submit_result",
            "result": "correct",
            "submission": rendered_submission(page),
        }
        url = reverse("reviews:study_current")
        self.client.post(url, data)
        self.client.post(url, data)
        self.assertEqual(self.client.session["study_session"]["index"], 1)
        # Simulate the review committing before a failed session save.
        session = self.client.session
        session["study_session"] = stale
        session.save()
        self.client.get(url)
        self.assertEqual(self.client.session["study_session"]["index"], 1)
        self.assertEqual(self.client.session["study_session"]["correct"], 1)
        self.assertEqual(ReviewLog.objects.count(), 1)
        self.assertEqual(TrainerAttempt.objects.count(), 1)
        self.assertEqual(DailyStudyProgress.objects.get(deck=self.deck).correct_count, 1)

    def test_all_mistake_retry_keeps_reverse_and_filters_unavailable_words(self):
        make_cards(self.deck, 3)
        self.start_all(direction="reverse")
        queue = list(self.queue())
        for _ in queue:
            submit_typed_result(self.client, result="incorrect")
        Card.objects.filter(pk=queue[0]).update(is_suspended=True)

        self.client.post(reverse("reviews:retry_mistakes"))

        self.assertCountEqual(self.queue(), queue[1:])
        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")
        self.assertEqual(self.client.session["study_session"]["kind"], "retry")


class DashboardStudyItemTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.start = reverse("reviews:start_study", args=[self.deck.pk])

    def dashboard(self):
        return self.client.get(reverse("core:dashboard"))

    def test_today_starts_a_bounded_goal_session(self):
        make_cards(self.deck, 45)

        response = self.dashboard()
        study = response.context["daily_plan"]["study"]

        self.assertEqual(study["study_url"], f"{self.start}?daily=1")
        self.assertEqual(study["reverse_url"], f"{self.start}?daily=1&direction=reverse")
        self.assertEqual(study["session_size"], 20)
        self.assertNotContains(response, "Practice all words")
        self.assertContains(response, "Study 20 words")
        self.assertContains(response, f'href="{self.start}?daily=1"')
        self.assertContains(response, "direction=reverse")

    def test_the_session_size_follows_the_remaining_goal(self):
        make_cards(self.deck, 45)
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=14
        )

        response = self.dashboard()

        self.assertEqual(response.context["daily_plan"]["study"]["session_size"], 6)
        self.assertContains(response, "Study 6 words")

    def test_few_words_are_reported_truthfully(self):
        make_cards(self.deck, 3)

        response = self.dashboard()

        study = response.context["daily_plan"]["study"]
        self.assertEqual(study["session_size"], 3)
        self.assertTrue(study["is_short"])
        self.assertContains(response, "Study 3 words")
        self.assertContains(response, "only 3 active words")

    def test_one_word_is_singular(self):
        make_cards(self.deck, 1)

        self.assertContains(self.dashboard(), "Study 1 word<")

    def test_a_completed_goal_offers_optional_practice_without_the_goal_flag(self):
        make_cards(self.deck, 30)
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=DAILY_TARGET
        )

        response = self.dashboard()
        study = response.context["daily_plan"]["study"]

        self.assertTrue(study["is_done"])
        self.assertEqual(study["study_url"], f"{self.start}?all=1")
        self.assertEqual(study["reverse_url"], f"{self.start}?all=1&direction=reverse")
        self.assertEqual(study["session_size"], 30)
        self.assertContains(response, "Daily goal complete")
        self.assertContains(response, "Practice all words")
        self.assertNotContains(response, "daily=1")
        self.client.get(study["study_url"])
        self.assertEqual(len(self.client.session["study_session"]["queue"]), 30)

    def test_an_empty_deck_offers_adding_words_instead_of_a_dead_session(self):
        make_cards(self.deck, 2, is_retired=True)

        response = self.dashboard()

        self.assertFalse(response.context["daily_plan"]["study"]["has_cards"])
        self.assertContains(response, "No words to study yet")
        self.assertContains(response, reverse("cards:create"))
        self.assertNotContains(response, "?daily=1")

    def test_completed_goal_with_no_active_words_offers_adding_words(self):
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=DAILY_TARGET
        )

        response = self.dashboard()

        self.assertContains(response, "Add words")
        self.assertNotContains(response, "Practice all words")
        self.assertNotContains(response, '?all=1"')

    def test_completed_goal_uses_the_active_modes_global_deck(self):
        dutch = get_global_deck(self.user, "NL-EN")
        cards = make_cards(dutch, 25)
        make_cards(self.deck, 30)
        DailyStudyProgress.objects.create(
            deck=dutch, date=timezone.localdate(), correct_count=DAILY_TARGET
        )
        session = self.client.session
        session["language_mode"] = "NL-EN"
        session.save()

        response = self.dashboard()
        study = response.context["daily_plan"]["study"]

        self.assertEqual(study["deck"], dutch)
        self.client.get(study["reverse_url"])
        self.assertCountEqual(self.client.session["study_session"]["queue"], [c.pk for c in cards])
        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")
