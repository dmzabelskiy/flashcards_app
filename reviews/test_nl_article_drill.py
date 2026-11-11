from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.models import TrainerAttempt


class DutchArticleDrillTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.nl_deck = Deck.objects.create(
            owner=self.user, name="NL", language_mode=Deck.LanguageMode.NL_EN
        )
        self.card = Card.objects.create(
            deck=self.nl_deck, front="huis", back="house", article="het"
        )
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

    def test_drill_offers_de_and_het(self):
        self.client.post(reverse("reviews:drill_start", args=["articles"]))
        response = self.client.get(reverse("reviews:drill", args=["articles"]))
        self.assertContains(response, 'value="de"')
        self.assertContains(response, 'value="het"')
        self.assertNotContains(response, 'value="der"')
        self.assertContains(response, "huis")

    def test_correct_answer_is_recorded(self):
        other = Card.objects.create(deck=self.nl_deck, front="boom", back="tree", article="de")
        session = self.client.session
        session["article_drill_session"] = {
            "queue": [self.card.pk, other.pk],
            "index": 0,
            "seen": 0,
            "correct": 0,
            "last": None,
        }
        session.save()
        self.client.post(reverse("reviews:drill", args=["articles"]), {"answer": "het"})
        response = self.client.get(reverse("reviews:drill", args=["articles"]))
        self.assertContains(response, "het huis")
        attempt = TrainerAttempt.objects.get(
            owner=self.user, trainer=TrainerAttempt.Trainer.ARTICLE, card=self.card
        )
        self.assertTrue(attempt.is_correct)

    def test_incorrect_answer_is_recorded(self):
        other = Card.objects.create(deck=self.nl_deck, front="boom", back="tree", article="de")
        session = self.client.session
        session["article_drill_session"] = {
            "queue": [self.card.pk, other.pk],
            "index": 0,
            "seen": 0,
            "correct": 0,
            "last": None,
        }
        session.save()
        self.client.post(reverse("reviews:drill", args=["articles"]), {"answer": "de"})
        attempt = TrainerAttempt.objects.get(
            owner=self.user, trainer=TrainerAttempt.Trainer.ARTICLE, card=self.card
        )
        self.assertFalse(attempt.is_correct)

    def test_card_options_follow_the_deck_not_the_session(self):
        de_deck = Deck.objects.create(
            owner=self.user, name="DE", language_mode=Deck.LanguageMode.DE_RU
        )
        german = Card.objects.create(deck=de_deck, front="Haus", back="дом", article="das")
        self.assertEqual([o["value"] for o in german.article_options], ["der", "die", "das"])
        self.assertEqual([o["value"] for o in self.card.article_options], ["de", "het"])
