from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import get_global_deck
from reviews.analytics import build_personal_analytics, weak_practice_cards
from reviews.models import ReviewLog
from reviews.ratings import Rating
from reviews.testutils import submit_typed_result


def log_reviews(card, ratings, *, days_ago=0):
    when = timezone.now() - timezone.timedelta(days=days_ago)
    for rating in ratings:
        ReviewLog.objects.create(
            card=card,
            rating=rating,
            reviewed_at=when,
            previous_due_at=when,
            new_due_at=when,
            previous_interval_days=0,
            new_interval_days=1,
        )


def make_weak(deck, front="Wort", **fields):
    """A card with two recent mistakes: 0% accuracy over two attempts."""
    card = Card.objects.create(deck=deck, front=front, back="слово", **fields)
    log_reviews(card, [Rating.AGAIN, Rating.AGAIN])
    return card


class StatsReviewLinkTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)

    def stats(self):
        return self.client.get(reverse("core:stats"))

    def test_weak_word_review_links_to_a_review_face_not_card_details(self):
        card = make_weak(self.deck)

        response = self.stats()

        self.assertContains(response, f'href="{reverse("reviews:review_card", args=[card.pk])}"')
        self.assertNotContains(response, f'href="{card.get_absolute_url()}"')

    def test_strong_word_review_links_to_a_review_face(self):
        card = Card.objects.create(deck=self.deck, front="Haus", back="дом")
        log_reviews(card, [Rating.GOOD, Rating.GOOD, Rating.EASY])

        response = self.stats()

        self.assertContains(response, f'href="{reverse("reviews:review_card", args=[card.pk])}"')

    def test_the_link_opens_the_front_of_the_card_for_review(self):
        card = make_weak(self.deck, front="Haus")

        response = self.client.get(reverse("reviews:review_card", args=[card.pk]))

        self.assertContains(response, "Haus")
        self.assertContains(response, "Show answer")

    def test_an_inactive_card_links_to_its_details_instead_of_a_review(self):
        card = make_weak(self.deck, is_retired=True)

        response = self.stats()

        self.assertContains(response, f'href="{card.get_absolute_url()}"')
        self.assertNotContains(response, reverse("reviews:review_card", args=[card.pk]))


class WeakWordPracticeTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.start = reverse("reviews:start_weak_practice")

    def queue(self):
        return self.client.session["study_session"]["queue"]

    def test_stats_offers_practice_with_a_count_of_active_weak_words(self):
        make_weak(self.deck, "Eins")
        make_weak(self.deck, "Zwei")
        make_weak(self.deck, "Drei", is_retired=True)

        response = self.client.get(reverse("core:stats"))

        self.assertEqual(response.context["analytics"]["weak_practice_count"], 2)
        self.assertContains(response, "Practice 2 weak words")
        self.assertContains(response, f'action="{self.start}"')

    def test_stats_hides_the_practice_button_without_weak_words(self):
        response = self.client.get(reverse("core:stats"))

        self.assertNotContains(response, reverse("reviews:start_weak_practice"))
        self.assertEqual(response.context["analytics"]["weak_practice_count"], 0)

    def test_practice_starts_a_session_with_the_active_weak_cards(self):
        weak = [make_weak(self.deck, f"Wort{i}") for i in range(3)]
        strong = Card.objects.create(deck=self.deck, front="Stark", back="сильный")
        log_reviews(strong, [Rating.GOOD, Rating.GOOD, Rating.GOOD])
        untested = Card.objects.create(deck=self.deck, front="Neu", back="новый")

        response = self.client.post(self.start, {"period": "7"})

        self.assertRedirects(response, reverse("reviews:study_current"))
        self.assertCountEqual(self.queue(), [c.pk for c in weak])
        self.assertNotIn(strong.pk, self.queue())
        self.assertNotIn(untested.pk, self.queue())
        session = self.client.session["study_session"]
        self.assertEqual(session["kind"], "weak")
        self.assertIsNone(session["deck_id"])
        self.assertEqual(session["direction"], "forward")

    def test_practice_never_includes_inactive_cards(self):
        active = make_weak(self.deck, "Aktiv")
        make_weak(self.deck, "Retired", is_retired=True)
        make_weak(self.deck, "Suspended", is_suspended=True)
        archived = Deck.objects.create(name="Old", owner=self.user, is_archived=True)
        make_weak(archived, "Archived")

        self.client.post(self.start)

        self.assertEqual(self.queue(), [active.pk])

    def test_practice_covers_only_the_signed_in_user_and_active_mode(self):
        mine = make_weak(self.deck, "Meins")
        stranger = create_user("stranger")
        make_weak(get_global_deck(stranger, Deck.LanguageMode.DE_RU), "Fremd")
        spanish = Deck.objects.create(name="ES", owner=self.user, language_mode="DE-ES")
        make_weak(spanish, "casa")

        self.client.post(self.start)

        self.assertEqual(self.queue(), [mine.pk])

    def test_practice_is_recomputed_and_ignores_ids_sent_by_the_client(self):
        mine = make_weak(self.deck, "Meins")
        stranger = create_user("stranger")
        foreign = make_weak(get_global_deck(stranger, Deck.LanguageMode.DE_RU), "Fremd")
        fine = Card.objects.create(deck=self.deck, front="Fein", back="хорошо")

        self.client.post(
            self.start, {"card_ids": [foreign.pk, fine.pk], "cards": f"{foreign.pk},{fine.pk}"}
        )

        self.assertEqual(self.queue(), [mine.pk])

    def test_practice_is_bounded_to_twenty_cards_worst_first(self):
        worst = Card.objects.create(deck=self.deck, front="Schlimm", back="плохо")
        log_reviews(worst, [Rating.AGAIN] * 5)
        for index in range(24):
            card = Card.objects.create(deck=self.deck, front=f"w{index:02d}", back="b")
            log_reviews(card, [Rating.GOOD, Rating.AGAIN, Rating.AGAIN])

        self.client.post(self.start)

        self.assertEqual(len(self.queue()), 20)
        self.assertIn(worst.pk, self.queue())
        self.assertEqual(build_personal_analytics(self.user, "DE-RU")["weak_practice_count"], 20)

    def test_the_period_limits_the_history_that_counts(self):
        old = Card.objects.create(deck=self.deck, front="Alt", back="старый")
        log_reviews(old, [Rating.AGAIN, Rating.AGAIN], days_ago=20)

        self.client.post(self.start, {"period": "7"})
        self.assertNotIn("study_session", self.client.session)

        self.client.post(self.start, {"period": "30"})
        self.assertEqual(self.queue(), [old.pk])

    def test_a_bad_period_falls_back_to_the_default_window(self):
        card = make_weak(self.deck)

        self.client.post(self.start, {"period": "banana"})

        self.assertEqual(self.queue(), [card.pk])

    def test_without_weak_words_it_explains_and_returns_to_stats(self):
        response = self.client.post(self.start, follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse("core:stats"))
        self.assertContains(response, "No weak words to practice")
        self.assertNotIn("study_session", self.client.session)

    def test_practice_needs_post(self):
        self.assertEqual(self.client.get(self.start).status_code, 405)

    def test_direction_is_honoured(self):
        make_weak(self.deck)

        self.client.post(self.start, {"direction": "reverse"})

        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")

    def test_weak_practice_cards_helper_is_scoped_and_active_only(self):
        mine = make_weak(self.deck, "Meins")
        make_weak(self.deck, "Retired", is_retired=True)
        make_weak(get_global_deck(create_user("stranger"), "DE-RU"), "Fremd")

        self.assertEqual(weak_practice_cards(self.user, "DE-RU"), [mine])


class MixedDeckSessionTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.global_deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        self.other_deck = Deck.objects.create(name="Reisen", owner=self.user)
        self.cards = [
            make_weak(self.global_deck, "Eins"),
            make_weak(self.other_deck, "Zwei"),
            make_weak(self.other_deck, "Drei"),
        ]

    def finish(self, wrong=()):
        self.client.post(reverse("reviews:start_weak_practice"))
        queue = list(self.client.session["study_session"]["queue"])
        for card_id in queue:
            if card_id in wrong:
                submit_typed_result(self.client, answer="nope", auto_correct="0")
            else:
                submit_typed_result(self.client, result="correct", answer="yes")
        return queue

    def test_studying_cards_from_several_decks_completes_cleanly(self):
        queue = self.finish()

        response = self.client.get(reverse("reviews:study_complete"))

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(queue, [c.pk for c in self.cards])
        self.assertContains(response, "Back to Stats")
        self.assertContains(response, reverse("core:stats"))
        self.assertContains(response, reverse("core:dashboard"))

    def test_a_mixed_session_does_not_pretend_to_belong_to_one_deck(self):
        self.finish()

        response = self.client.get(reverse("reviews:study_complete"))

        self.assertNotContains(response, "Back to deck")
        self.assertNotContains(response, "Study again")

    def test_each_card_shows_its_own_deck_while_studying(self):
        self.client.post(reverse("reviews:start_weak_practice"))
        seen = set()
        for _ in range(3):
            page = self.client.get(reverse("reviews:study_current"))
            seen.add(page.context["card"].deck.name)
            submit_typed_result(self.client, result="correct", answer="yes")

        self.assertEqual(seen, {"Reisen", "Global deck · Russian"})

    def test_mistakes_in_a_mixed_session_can_be_retried(self):
        queue = self.finish(wrong={self.cards[0].pk, self.cards[2].pk})

        page = self.client.get(reverse("reviews:study_complete"))
        self.assertContains(page, "Retry 2 mistakes")
        self.client.post(reverse("reviews:retry_mistakes"))

        self.assertCountEqual(
            self.client.session["study_session"]["queue"], [self.cards[0].pk, self.cards[2].pk]
        )
        self.assertIsNone(self.client.session["study_session"]["deck_id"])
        self.assertEqual(len(queue), 3)

    def test_daily_progress_is_recorded_on_each_cards_own_deck(self):
        from reviews.models import DailyStudyProgress

        self.finish()

        counts = {
            progress.deck.name: progress.correct_count
            for progress in DailyStudyProgress.objects.select_related("deck")
        }
        self.assertEqual(counts, {"Global deck · Russian": 1, "Reisen": 2})
