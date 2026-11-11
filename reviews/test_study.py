from django.test import TestCase
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from decks.services import get_global_deck
from reviews.models import ReviewLog
from reviews.ratings import Rating
from reviews.study import (
    accepted_german_forms,
    check_german_answer,
    is_correct_answer,
    normalize_german_answer,
)
from reviews.testutils import check_typed_answer, rendered_submission, submit_typed_result


class StudyAnswerTests(TestCase):
    def test_accepts_any_semicolon_separated_translation(self):
        self.assertTrue(is_correct_answer("отдавать", "сдавать; отдавать"))

    def test_accepts_answers_with_case_and_whitespace_differences(self):
        self.assertTrue(is_correct_answer("  TABLE  ", "table; masculine noun"))

    def test_rejects_typo_until_user_overrides_in_view(self):
        self.assertFalse(is_correct_answer("отдавть", "сдавать; отдавать"))


class NormalizeGermanAnswerTests(TestCase):
    def test_folds_umlauts_and_eszett(self):
        self.assertEqual(normalize_german_answer("Häuser"), normalize_german_answer("Haeuser"))
        self.assertEqual(normalize_german_answer("Häuser"), "haeuser")
        self.assertEqual(normalize_german_answer("groß"), "gross")
        self.assertEqual(normalize_german_answer("für"), "fuer")
        self.assertEqual(normalize_german_answer("können"), "koennen")


class AcceptedGermanFormsTests(TestCase):
    def test_noun_with_article_on_front(self):
        card = Card(front="das Haus", back="house")
        self.assertEqual(accepted_german_forms(card), ["das Haus", "Haus"])

    def test_noun_with_separate_article_field(self):
        card = Card(front="Haus", article="das", back="house")
        self.assertEqual(accepted_german_forms(card), ["das Haus", "Haus"])

    def test_verb_has_a_single_form(self):
        card = Card(front="verlangen", back="to demand")
        self.assertEqual(accepted_german_forms(card), ["verlangen"])

    def test_front_with_delimiters_is_not_split(self):
        card = Card(front="der Tisch, die Bank", back="table, bench")
        self.assertEqual(accepted_german_forms(card), ["der Tisch, die Bank", "Tisch, die Bank"])


class CheckGermanAnswerTests(TestCase):
    def test_accepts_stem_without_article(self):
        card = Card(front="Haus", article="das", back="house")
        self.assertTrue(check_german_answer("Haus", card).is_correct)

    def test_accepts_full_form_with_article(self):
        card = Card(front="Haus", article="das", back="house")
        self.assertTrue(check_german_answer("das Haus", card).is_correct)

    def test_accepts_case_insensitive(self):
        card = Card(front="Haus", article="das", back="house")
        self.assertTrue(check_german_answer("haus", card).is_correct)

    def test_accepts_umlaut_folded_spelling(self):
        card = Card(front="Häuser", back="houses")
        self.assertTrue(check_german_answer("Haeuser", card).is_correct)

    def test_accepts_one_typo(self):
        card = Card(front="verlangen", back="to demand")
        result = check_german_answer("verlanegn", card)
        self.assertTrue(result.is_correct)
        self.assertTrue(result.had_typo)

    def test_rejects_the_translation_typed_back(self):
        card = Card(front="verlangen", back="to demand")
        self.assertFalse(check_german_answer("to demand", card).is_correct)

    def test_rejects_a_different_german_word(self):
        card = Card(front="verlangen", back="to demand")
        self.assertFalse(check_german_answer("fordern", card).is_correct)


class StudyFlowTests(AuthedTestCase):
    def test_study_flow_counts_override_and_schedules_card(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        card = Card.objects.create(deck=deck, front="abgeben", back="сдавать; отдавать")

        response = self.client.get(reverse("reviews:start_study", args=[deck.pk]))
        self.assertRedirects(response, reverse("reviews:study_current"))

        response = check_typed_answer(self.client, "отдавть")
        self.assertContains(response, "Incorrect")
        self.assertContains(response, "отдавть")

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "correct",
                "answer": "отдавть",
            },
        )
        self.assertRedirects(response, reverse("reviews:study_complete"))

        session = self.client.session
        self.assertEqual(session["study_session"]["correct"], 1)
        self.assertEqual(session["study_session"]["incorrect"], 0)

        card.refresh_from_db()
        self.assertEqual(card.review_count, 1)
        self.assertEqual(card.interval_days, 3)
        self.assertEqual(ReviewLog.objects.get(card=card).rating, Rating.GOOD)

    def test_global_deck_noun_card_shows_the_article_on_the_face(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        Card.objects.create(deck=deck, front="Haus", article="das", back="dom")

        self.client.get(reverse("reviews:start_study", args=[deck.pk]))
        response = self.client.get(reverse("reviews:study_current"))

        self.assertContains(response, "das Haus")

    def test_start_study_can_shuffle_deck_queue(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        cards = [
            Card.objects.create(deck=deck, front=f"front {index}", back=f"back {index}")
            for index in range(5)
        ]

        response = self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?shuffle=1")

        self.assertRedirects(response, reverse("reviews:study_current"))
        queue = self.client.session["study_session"]["queue"]
        self.assertCountEqual(queue, [card.pk for card in cards])

    def test_third_global_deck_correct_answer_offers_and_applies_retirement(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(
            deck=deck,
            front="bekommen",
            back="получать",
            correct_streak=2,
        )
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "получать")

        self.assertContains(response, "Retire word")
        self.assertContains(response, 'name="result" value="retire"')

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "retire",
                "answer": "получать",
                "auto_correct": "1",
            },
        )

        self.assertRedirects(response, reverse("reviews:study_complete"))
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 3)
        self.assertTrue(card.is_retired)
        self.assertIsNotNone(card.retired_at)
        self.assertNotIn(card, Card.objects.active())

    def test_retirement_is_not_offered_or_applied_before_third_correct_answer(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(
            deck=deck,
            front="bekommen",
            back="получать",
            correct_streak=1,
        )
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "получать")

        self.assertNotContains(response, "Retire word")

        self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "retire",
                "answer": "получать",
                "auto_correct": "1",
            },
        )
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 2)
        self.assertFalse(card.is_retired)


class ReverseStudyDirectionTests(AuthedTestCase):
    def test_start_study_stores_reverse_direction_from_query_param(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        Card.objects.create(deck=deck, front="Haus", article="das", back="house")

        self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse")

        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")

    def test_start_study_defaults_to_forward_direction(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        Card.objects.create(deck=deck, front="Haus", article="das", back="house")

        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        self.assertEqual(self.client.session["study_session"]["direction"], "forward")

    def test_reverse_session_shows_back_and_hides_front_before_checking(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        Card.objects.create(deck=deck, front="Haus", article="das", back="house")

        self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse")
        response = self.client.get(reverse("reviews:study_current"))

        front_face = response.content.split(b"flip-card-back")[0]
        self.assertIn(b"house", front_face)
        self.assertNotIn(b"das Haus", front_face)
        # The card has not been flipped to the answer side yet.
        self.assertNotContains(response, "is-flipped")

    def test_reverse_session_accepts_the_german_front_and_updates_progress(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        card = Card.objects.create(deck=deck, front="Haus", article="das", back="house")

        self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse")
        response = check_typed_answer(self.client, "das Haus")

        self.assertContains(response, "Correct")
        self.assertContains(response, "das Haus")

        from django.utils import timezone

        from reviews.models import DailyStudyProgress

        self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "correct",
                "answer": "das Haus",
            },
        )

        progress = DailyStudyProgress.objects.get(deck=card.deck, date=timezone.localdate())
        self.assertEqual(progress.correct_count, 1)

    def test_reverse_session_excludes_retired_cards(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        active_card = Card.objects.create(deck=deck, front="Haus", back="house")
        Card.objects.create(deck=deck, front="Auto", back="car", is_retired=True)

        response = self.client.get(
            f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse"
        )

        self.assertRedirects(response, reverse("reviews:study_current"))
        self.assertEqual(self.client.session["study_session"]["queue"], [active_card.pk])

    def test_reverse_retirement_offer_and_apply(self):
        deck = get_global_deck(self.user, Deck.LanguageMode.DE_RU)
        card = Card.objects.create(
            deck=deck,
            front="bekommen",
            back="получать",
            correct_streak=2,
        )
        self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse")

        response = check_typed_answer(self.client, "bekommen")

        self.assertContains(response, "Retire word")
        self.assertContains(response, 'name="result" value="retire"')

        response = self.client.post(
            reverse("reviews:study_current"),
            {
                "action": "submit_result",
                "submission": rendered_submission(response),
                "result": "retire",
                "answer": "bekommen",
                "auto_correct": "1",
            },
        )

        self.assertRedirects(response, reverse("reviews:study_complete"))
        card.refresh_from_db()
        self.assertEqual(card.correct_streak, 3)
        self.assertTrue(card.is_retired)
        self.assertNotIn(card, Card.objects.active())

    def test_study_complete_keeps_reverse_direction_in_study_again_link(self):
        deck = Deck.objects.create(name="German Test", owner=self.user)
        Card.objects.create(deck=deck, front="Haus", back="house")

        self.client.get(f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse")
        submit_typed_result(self.client, result="correct", answer="Haus")
        response = self.client.get(reverse("reviews:study_complete"))

        self.assertContains(
            response, f"{reverse('reviews:start_study', args=[deck.pk])}?direction=reverse"
        )
