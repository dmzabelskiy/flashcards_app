from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import get_global_deck
from reviews.models import ReviewLog
from reviews.testutils import submit_typed_result


class RetryMistakesTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(name="German", owner=self.user)
        self.cards = [
            Card.objects.create(deck=self.deck, front=f"Wort{i}", back=f"слово{i}")
            for i in range(5)
        ]
        self.retry = reverse("reviews:retry_mistakes")

    def study(self, wrong, *, direction=None):
        """Run one full session answering the cards in ``wrong`` incorrectly."""
        suffix = f"?direction={direction}" if direction else ""
        self.client.get(reverse("reviews:start_study", args=[self.deck.pk]) + suffix)
        session = self.client.session["study_session"]
        for card_id in list(session["queue"]):
            card = Card.objects.get(pk=card_id)
            if card.pk in wrong:
                submit_typed_result(self.client, answer="nope", auto_correct="0")
            else:
                submit_typed_result(self.client, result="correct", answer="yes")
        return session["queue"]

    def retry_queue(self):
        return self.client.session["study_session"]["queue"]

    def test_the_complete_page_offers_a_retry_of_only_the_mistakes(self):
        wrong = {self.cards[0].pk, self.cards[3].pk}
        self.study(wrong)

        response = self.client.get(reverse("reviews:study_complete"))

        self.assertContains(response, "Retry 2 mistakes")
        self.assertContains(response, f'action="{self.retry}"')

    def test_no_retry_is_offered_without_mistakes(self):
        self.study(set())

        response = self.client.get(reverse("reviews:study_complete"))

        self.assertNotContains(response, "Retry")

    def test_the_retry_round_contains_exactly_the_incorrect_cards(self):
        wrong = {self.cards[1].pk, self.cards[4].pk}
        self.study(wrong)

        response = self.client.post(self.retry)

        self.assertRedirects(response, reverse("reviews:study_current"))
        session = self.client.session["study_session"]
        self.assertCountEqual(session["queue"], wrong)
        self.assertEqual(session["kind"], "retry")
        self.assertEqual((session["index"], session["correct"], session["incorrect"]), (0, 0, 0))
        self.assertEqual(session["incorrect_ids"], [])
        self.assertEqual(session["deck_id"], self.deck.pk)

    def test_a_card_marked_right_by_the_learner_is_not_a_mistake(self):
        self.client.get(reverse("reviews:start_study", args=[self.deck.pk]))
        queue = self.client.session["study_session"]["queue"]
        submit_typed_result(self.client, result="correct", answer="nope", auto_correct="0")
        for _ in queue[1:]:
            submit_typed_result(self.client, result="correct", answer="yes")

        response = self.client.get(reverse("reviews:study_complete"))

        self.assertNotContains(response, "Retry")

    def test_the_study_direction_is_preserved(self):
        self.study({self.cards[2].pk}, direction="reverse")

        self.client.post(self.retry)

        self.assertEqual(self.client.session["study_session"]["direction"], "reverse")
        page = self.client.get(reverse("reviews:study_current"))
        self.assertContains(page, "Deutsch")

    def test_a_forward_session_retries_forward(self):
        self.study({self.cards[2].pk})

        self.client.post(self.retry)

        self.assertEqual(self.client.session["study_session"]["direction"], "forward")

    def test_a_retry_round_can_be_retried_again(self):
        self.study({self.cards[0].pk, self.cards[1].pk})
        self.client.post(self.retry)
        first, second = self.retry_queue()
        submit_typed_result(self.client, result="correct", answer="yes")
        submit_typed_result(self.client, answer="nope", auto_correct="0")

        self.client.post(self.retry)

        self.assertEqual(self.retry_queue(), [second])

    def test_mistakes_are_not_listed_twice_when_a_card_is_missed_again_in_a_round(self):
        self.study({self.cards[0].pk})
        self.client.post(self.retry)
        submit_typed_result(self.client, answer="nope", auto_correct="0")

        self.assertEqual(self.client.session["study_session"]["incorrect_ids"], [self.cards[0].pk])

    def test_cards_that_became_ineligible_are_dropped_from_the_retry(self):
        self.study({c.pk for c in self.cards})
        Card.objects.filter(pk=self.cards[0].pk).update(is_retired=True)
        Card.objects.filter(pk=self.cards[1].pk).update(is_suspended=True)
        archived = Deck.objects.create(name="Old", owner=self.user, is_archived=True)
        Card.objects.filter(pk=self.cards[2].pk).update(deck=archived)

        self.client.post(self.retry)

        self.assertCountEqual(self.retry_queue(), [self.cards[3].pk, self.cards[4].pk])

    def test_ids_forged_into_the_session_are_revalidated(self):
        stranger = create_user("stranger")
        foreign = Card.objects.create(
            deck=get_global_deck(stranger, Deck.LanguageMode.DE_RU), front="Fremd", back="чужой"
        )
        spanish = Card.objects.create(
            deck=Deck.objects.create(name="ES", owner=self.user, language_mode="DE-ES"),
            front="casa",
            back="дом",
        )
        self.study({self.cards[0].pk})
        session = self.client.session
        session["study_session"]["incorrect_ids"] += [foreign.pk, spanish.pk, 999999]
        session.save()

        self.client.post(self.retry)

        self.assertEqual(self.retry_queue(), [self.cards[0].pk])

    def test_a_retry_with_nothing_eligible_explains_and_stays_on_the_results(self):
        self.study({self.cards[0].pk})
        Card.objects.filter(pk=self.cards[0].pk).update(is_suspended=True)

        response = self.client.post(self.retry, follow=True)

        self.assertContains(response, "available to practice any more")
        self.assertEqual(response.redirect_chain[-1][0], reverse("reviews:study_complete"))
        self.assertEqual(self.client.session["study_session"]["kind"], "deck")

    def test_retry_is_only_available_once_the_session_is_finished(self):
        self.client.get(reverse("reviews:start_study", args=[self.deck.pk]))
        submit_typed_result(self.client, answer="nope", auto_correct="0")

        response = self.client.post(self.retry)

        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["study_session"]["kind"], "deck")

    def test_retry_needs_post_and_a_session(self):
        self.assertEqual(self.client.get(self.retry).status_code, 405)

        response = self.client.post(self.retry)

        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)

    def test_retry_ignores_card_ids_sent_by_the_client(self):
        self.study({self.cards[0].pk})

        self.client.post(self.retry, {"card_ids": [self.cards[3].pk], "incorrect_ids": "4"})

        self.assertEqual(self.retry_queue(), [self.cards[0].pk])

    def test_retried_answers_are_scheduled_like_any_other_review(self):
        self.study({self.cards[0].pk})
        before = ReviewLog.objects.count()
        self.client.post(self.retry)

        submit_typed_result(self.client, result="correct", answer="yes")

        self.assertEqual(ReviewLog.objects.count(), before + 1)
        self.assertEqual(Card.objects.get(pk=self.cards[0].pk).review_count, 2)
