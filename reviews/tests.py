from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from reviews.models import ReviewLog, TrainerAttempt
from reviews.scheduler import Rating, schedule_card
from reviews.testutils import check_typed_answer, post_quick_review


class ReviewCardInputTests(AuthedTestCase):
    def _review_url(self, card):
        return reverse("reviews:review_card", kwargs={"card_id": card.pk})

    def test_invalid_rating_returns_400_and_does_not_schedule(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="a", back="b")

        response = self.client.post(self._review_url(card), {"rating": "bogus"})

        self.assertEqual(response.status_code, 400)
        card.refresh_from_db()
        self.assertEqual(card.review_count, 0)
        self.assertFalse(ReviewLog.objects.exists())

    def test_missing_rating_returns_400(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="a", back="b")

        response = self.client.post(self._review_url(card), {})

        self.assertEqual(response.status_code, 400)
        card.refresh_from_db()
        self.assertEqual(card.review_count, 0)

    def test_malformed_or_negative_elapsed_seconds_is_rejected_and_nothing_is_written(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="a", back="b")

        for bad in ("not-a-number", "-5", "1.5", "1e3", "99999999999999999999"):
            with self.subTest(elapsed=bad):
                response = post_quick_review(self.client, card, "good", elapsed_seconds=bad)

                self.assertEqual(response.status_code, 400)
        card.refresh_from_db()
        self.assertEqual(card.review_count, 0)
        self.assertFalse(ReviewLog.objects.exists())
        self.assertFalse(TrainerAttempt.objects.exists())

    def test_blank_elapsed_seconds_is_stored_as_unknown(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="a", back="b")

        response = post_quick_review(self.client, card, "good", elapsed_seconds="")

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(ReviewLog.objects.get(card=card).elapsed_seconds)


class ReviewCardTemplateTests(AuthedTestCase):
    def test_uses_external_script_and_has_no_inline_script(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(deck=deck, front="a", back="b")

        response = self.client.get(reverse("reviews:review_card", kwargs={"card_id": card.pk}))

        self.assertContains(response, "js/review_card.js")
        self.assertNotContains(response, "const started")

    def test_global_deck_noun_card_shows_the_article_on_the_face(self):
        deck = Deck.objects.create(
            name="Global", language_mode=Deck.LanguageMode.DE_RU, owner=self.user, is_global=True
        )
        card = Card.objects.create(deck=deck, front="Haus", article="das", back="dom")

        response = self.client.get(reverse("reviews:review_card", kwargs={"card_id": card.pk}))

        self.assertContains(response, "das Haus")

    def test_german_deck_card_speaks_with_the_german_locale(self):
        deck = Deck.objects.create(
            name="German", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="a", back="b")

        response = self.client.get(reverse("reviews:review_card", kwargs={"card_id": card.pk}))

        self.assertContains(response, 'data-tts-lang="de-DE"')

    def test_dutch_deck_card_speaks_with_the_dutch_locale(self):
        deck = Deck.objects.create(
            name="Dutch", language_mode=Deck.LanguageMode.NL_EN, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="de kat", back="cat")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

        response = self.client.get(reverse("reviews:review_card", kwargs={"card_id": card.pk}))

        self.assertContains(response, 'data-tts-lang="nl-NL"')


class SchedulerTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def test_good_rating_on_new_card_schedules_in_three_days(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        card = Card.objects.create(deck=deck, front="seit", back="for/since")

        result = schedule_card(card, Rating.GOOD)

        self.assertEqual(result.previous_interval_days, 0)
        self.assertEqual(result.new_interval_days, 3)
        self.assertEqual(card.interval_days, 3)
        self.assertEqual(card.review_count, 1)
        self.assertEqual(card.lapse_count, 0)
        self.assertAlmostEqual(card.ease_factor, 2.5)
        self.assertGreater(card.due_at, timezone.now() + timezone.timedelta(days=2))

    def test_again_rating_on_reviewed_card_records_lapse_and_due_tomorrow(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        card = Card.objects.create(
            deck=deck,
            front="sich interessieren fuer",
            back="to be interested in",
            interval_days=10,
            ease_factor=2.4,
            review_count=3,
        )

        result = schedule_card(card, Rating.AGAIN)

        self.assertEqual(result.previous_interval_days, 10)
        self.assertEqual(result.new_interval_days, 1)
        self.assertEqual(card.interval_days, 1)
        self.assertEqual(card.review_count, 4)
        self.assertEqual(card.lapse_count, 1)
        self.assertLess(card.ease_factor, 2.4)


class ReviewLogTests(TestCase):
    def test_review_log_is_created_when_card_is_reviewed(self):
        owner = create_user()
        deck = Deck.objects.create(name="German", owner=owner)
        card = Card.objects.create(deck=deck, front="der Tisch", back="table")
        previous_due_at = card.due_at

        result = schedule_card(card, Rating.EASY, elapsed_seconds=8)

        log = ReviewLog.objects.get(card=card)
        self.assertEqual(log.rating, Rating.EASY)
        self.assertEqual(log.previous_due_at, previous_due_at)
        self.assertEqual(log.new_due_at, result.new_due_at)
        self.assertEqual(log.previous_interval_days, 0)
        self.assertEqual(log.new_interval_days, 5)
        self.assertEqual(log.elapsed_seconds, 8)


class StudyCardTemplateTests(AuthedTestCase):
    def test_checked_response_uses_external_script_and_has_no_inline_script(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        Card.objects.create(deck=deck, front="a", back="b")

        self.client.get(reverse("reviews:start_study", kwargs={"deck_id": deck.pk}))
        response = check_typed_answer(self.client, "b")

        self.assertContains(response, "js/study_card.js")
        self.assertNotContains(response, 'document.querySelector(".continue-button")')

    def test_dutch_deck_card_speaks_with_the_dutch_locale(self):
        deck = Deck.objects.create(
            name="Dutch", language_mode=Deck.LanguageMode.NL_EN, owner=self.user
        )
        Card.objects.create(deck=deck, front="de kat", back="cat")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

        response = self.client.get(
            reverse("reviews:start_study", kwargs={"deck_id": deck.pk}), follow=True
        )

        self.assertContains(response, 'data-tts-lang="nl-NL"')


class SpacedReviewOrderTests(AuthedTestCase):
    def test_start_review_does_not_always_pick_the_same_due_card(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        for i in range(10):
            Card.objects.create(deck=deck, front=f"word{i}", back=f"back{i}")

        started = set()
        for _ in range(30):
            response = self.client.get("/reviews/start/")
            started.add(response.url)

        self.assertGreater(len(started), 1, "spaced review always started with the same card")


class ReviewQueueLanguageModeTests(AuthedTestCase):
    def test_start_review_uses_selected_language_mode(self):
        ru_deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        es_deck = Deck.objects.create(
            name="ES", language_mode=Deck.LanguageMode.DE_ES, owner=self.user
        )
        Card.objects.create(deck=ru_deck, front="Haus", back="dom")
        es_card = Card.objects.create(deck=es_deck, front="Haus", back="casa")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get("/reviews/start/")

        self.assertRedirects(response, f"/reviews/{es_card.pk}/")


class ArticleDrillTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        self.noun = Card.objects.create(
            deck=self.deck, front="der Tisch", back="table", article="der"
        )
        Card.objects.create(deck=self.deck, front="gehen", back="to go")  # not a noun

    def test_start_builds_queue_of_noun_cards_only(self):
        response = self.client.get(reverse("reviews:drill_start", args=["articles"]))
        self.assertRedirects(response, reverse("reviews:drill", args=["articles"]))
        self.assertEqual(self.client.session["article_drill_session"]["queue"], [self.noun.pk])

    def test_drill_page_shows_noun_stem_and_article_buttons(self):
        self.client.get(reverse("reviews:drill_start", args=["articles"]))
        response = self.client.get(reverse("reviews:drill", args=["articles"]))
        content = response.content.decode()
        self.assertIn("Tisch", content)
        self.assertIn('value="der"', content)
        self.assertIn('value="die"', content)
        self.assertIn('value="das"', content)

    def test_correct_answer_increments_correct_and_does_not_reschedule(self):
        self.client.get(reverse("reviews:drill_start", args=["articles"]))
        due_before = self.noun.due_at

        self.client.post(reverse("reviews:drill", args=["articles"]), {"answer": "der"})

        session = self.client.session["article_drill_session"]
        self.assertEqual(session["seen"], 1)
        self.assertEqual(session["correct"], 1)
        self.noun.refresh_from_db()
        self.assertEqual(self.noun.due_at, due_before)
        self.assertEqual(self.noun.review_count, 0)

    def test_wrong_answer_does_not_increment_correct(self):
        self.client.get(reverse("reviews:drill_start", args=["articles"]))
        self.client.post(reverse("reviews:drill", args=["articles"]), {"answer": "die"})
        session = self.client.session["article_drill_session"]
        self.assertEqual(session["seen"], 1)
        self.assertEqual(session["correct"], 0)

    def test_dutch_article_card_never_enters_german_drill(self):
        # A card can only get a Dutch article on a German deck by bypassing
        # CardForm (bulk import, fixture, direct ORM); article_cards_for_user
        # defensively excludes it from the German drill queue either way.
        Card.objects.create(deck=self.deck, front="huis", back="house", article="het")
        response = self.client.get(reverse("reviews:drill_start", args=["articles"]))
        self.assertRedirects(response, reverse("reviews:drill", args=["articles"]))
        self.assertEqual(self.client.session["article_drill_session"]["queue"], [self.noun.pk])


class DrillCompletionPresentationTests(AuthedTestCase):
    def test_article_completion_shows_percentage_and_review_count(self):
        session = self.client.session
        session["article_drill_session"] = {
            "queue": [1, 2, 3, 4],
            "index": 4,
            "seen": 4,
            "correct": 3,
            "last": None,
        }
        session.save()

        response = self.client.get(reverse("reviews:drill_complete", args=["articles"]))

        self.assertContains(response, "75%")
        self.assertContains(response, "03 / 04 correct")
        self.assertContains(response, "To review")
