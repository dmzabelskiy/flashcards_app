from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.articles import parse_leading_article
from cards.models import LEECH_THRESHOLD, Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck


class ExportFilenameTests(AuthedTestCase):
    def test_export_filename_is_sanitized(self):
        deck = Deck.objects.create(
            name='Bad "name"; drop', language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        Card.objects.create(deck=deck, front="a", back="b")

        response = self.client.get(reverse("cards:export_deck_csv", kwargs={"deck_id": deck.pk}))

        content_disposition = response["Content-Disposition"]
        self.assertNotIn('"name"', content_disposition)
        self.assertNotIn(";", content_disposition.split("filename=", 1)[1])
        self.assertIn("bad-name-drop", content_disposition)


class CardModelTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def test_card_creation_defaults_to_due_immediately(self):
        deck = Deck.objects.create(name="German", owner=self.owner)

        card = Card.objects.create(
            deck=deck,
            front="der Tisch",
            back="table; masculine noun; plural: die Tische",
            card_type=Card.CardType.VOCABULARY,
        )

        self.assertEqual(card.deck, deck)
        self.assertEqual(card.interval_days, 0)
        self.assertEqual(card.ease_factor, 2.5)
        self.assertEqual(card.review_count, 0)
        self.assertEqual(card.lapse_count, 0)
        self.assertFalse(card.is_suspended)
        self.assertLessEqual(card.due_at, timezone.now())

    def test_due_queryset_excludes_future_and_suspended_cards(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        due = Card.objects.create(deck=deck, front="seit", back="for/since")
        Card.objects.create(
            deck=deck,
            front="morgen",
            back="tomorrow",
            due_at=timezone.now() + timezone.timedelta(days=2),
        )
        Card.objects.create(deck=deck, front="pause", back="pause", is_suspended=True)

        self.assertEqual(list(Card.objects.due()), [due])


class CardBrowseViewTests(AuthedTestCase):
    def test_card_list_filters_by_session_language_mode(self):
        ru_deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        es_deck = Deck.objects.create(
            name="ES", language_mode=Deck.LanguageMode.DE_ES, owner=self.user
        )
        Card.objects.create(deck=ru_deck, front="Haus", back="dom")
        Card.objects.create(deck=es_deck, front="Haus", back="casa")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get("/cards/")

        self.assertContains(response, "casa")
        self.assertNotContains(response, "dom")


class TtsButtonTests(AuthedTestCase):
    def test_card_detail_renders_speak_button_with_german_text(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="das Haus", back="dom")

        response = self.client.get(reverse("cards:detail", kwargs={"pk": card.pk}))
        content = response.content.decode()

        self.assertIn('class="tts-button"', content)
        self.assertIn('data-tts-text="das Haus"', content)
        self.assertIn('aria-label="Speak"', content)

    def test_card_detail_shows_the_article_on_the_face(self):
        deck = Deck.objects.create(
            name="Global", language_mode=Deck.LanguageMode.DE_RU, owner=self.user, is_global=True
        )
        card = Card.objects.create(deck=deck, front="Haus", article="das", back="dom")

        response = self.client.get(reverse("cards:detail", kwargs={"pk": card.pk}))
        content = response.content.decode()

        self.assertIn('data-tts-text="das Haus"', content)
        self.assertIn("das Haus", content)

    def test_display_front_does_not_repeat_an_article_already_in_the_front(self):
        deck = Deck.objects.create(
            name="Global", language_mode=Deck.LanguageMode.DE_RU, owner=self.user, is_global=True
        )
        card = Card.objects.create(deck=deck, front="das Unternehmen", article="das", back="firma")

        self.assertEqual(card.display_front, "das Unternehmen")

        response = self.client.get(reverse("cards:detail", kwargs={"pk": card.pk}))

        self.assertNotContains(response, "das das Unternehmen")
        self.assertContains(response, 'data-tts-text="das Unternehmen"')

    def test_card_detail_speaks_a_german_card_with_the_german_locale(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="das Haus", back="dom")

        response = self.client.get(reverse("cards:detail", kwargs={"pk": card.pk}))

        self.assertContains(response, 'data-tts-lang="de-DE"')

    def test_card_detail_speaks_a_dutch_card_with_the_dutch_locale(self):
        deck = Deck.objects.create(
            name="NL", language_mode=Deck.LanguageMode.NL_EN, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="de kat", back="cat")
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

        response = self.client.get(reverse("cards:detail", kwargs={"pk": card.pk}))

        self.assertContains(response, 'data-tts-lang="nl-NL"')


class ArticleParsingTests(TestCase):
    def test_parse_leading_article_extracts_article_and_stem(self):
        self.assertEqual(parse_leading_article("der Tisch"), ("der", "Tisch"))
        self.assertEqual(parse_leading_article("die Katze"), ("die", "Katze"))

    def test_parse_leading_article_without_article_returns_empty(self):
        self.assertEqual(parse_leading_article("gehen"), ("", "gehen"))


class DutchArticleParsingTests(TestCase):
    def test_dutch_articles_parse_only_with_dutch_articles(self):
        from cards.articles import parse_leading_article

        self.assertEqual(parse_leading_article("het huis", ("de", "het")), ("het", "huis"))
        self.assertEqual(parse_leading_article("het huis"), ("", "het huis"))
        self.assertEqual(parse_leading_article("die Katze", ("de", "het")), ("", "die Katze"))


class NounFieldTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def test_card_stores_article_and_plural(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.owner
        )
        card = Card.objects.create(
            deck=deck, front="der Tisch", back="table", article="der", plural="die Tische"
        )
        card.refresh_from_db()
        self.assertEqual(card.article, "der")
        self.assertEqual(card.plural, "die Tische")

    def test_noun_stem_strips_leading_article(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.owner
        )
        card = Card.objects.create(deck=deck, front="das Haus", back="dom")
        self.assertEqual(card.noun_stem, "Haus")

    def test_nouns_queryset_returns_only_cards_with_article(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.owner
        )
        noun = Card.objects.create(deck=deck, front="der Tisch", back="table", article="der")
        Card.objects.create(deck=deck, front="gehen", back="to go")
        self.assertEqual(list(Card.objects.nouns()), [noun])


class LeechTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        self.leech = Card.objects.create(
            deck=self.deck, front="a", back="b", lapse_count=LEECH_THRESHOLD
        )
        self.ok = Card.objects.create(deck=self.deck, front="c", back="d", lapse_count=1)

    def test_leeches_queryset_returns_cards_at_or_above_threshold(self):
        self.assertEqual(list(Card.objects.leeches()), [self.leech])

    def test_browse_leech_filter_limits_results(self):
        response = self.client.get(reverse("cards:list"), {"leech": "1"})
        cards = list(response.context["cards"])
        self.assertEqual(cards, [self.leech])
