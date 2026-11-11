from django.test import TestCase
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.study import accepted_translations, check_answer, check_german_answer
from reviews.testutils import check_typed_answer


class ParentheticalNoteSplittingTests(TestCase):
    def test_comma_inside_parentheses_does_not_split_the_option(self):
        result = check_answer("совершать", "совершать (обряд, ритуал)")

        self.assertTrue(result.is_correct)
        self.assertFalse(result.had_typo)
        self.assertFalse(result.different_form)

    def test_exact_match_against_a_paren_stripped_option_is_plain_correct(self):
        back = "исполнять (поручение); исправлять (обязанности); совершать (обряд)"

        result = check_answer("исполнять", back)

        self.assertTrue(result.is_correct)
        self.assertFalse(result.had_typo)
        self.assertFalse(result.different_form)
        self.assertEqual(result.matched_answer, "исполнять")

    def test_accepted_translations_adds_stripped_variant_without_duplicates(self):
        self.assertEqual(accepted_translations("стол (мебель)"), ["стол (мебель)", "стол"])


class DifferentFormAnswerTests(TestCase):
    def test_accepts_different_verb_form_matched_through_paren_stripped_option(self):
        back = (
            "изготовлять, производить, приготовлять, вырабатывать; "
            "устанавливать (нормы ); составлять, разрабатывать (планы )"
        )

        result = check_answer("установить", back)

        self.assertTrue(result.is_correct)
        self.assertFalse(result.had_typo)
        self.assertTrue(result.different_form)
        self.assertEqual(result.matched_answer, "устанавливать")

    def test_accepts_adverb_for_adjective(self):
        result = check_answer("временно", "временный")

        self.assertTrue(result.is_correct)
        self.assertTrue(result.different_form)
        self.assertEqual(result.matched_answer, "временный")

    def test_rejects_when_stems_are_too_short(self):
        # Both are verbs (stems "п"/"м", length 1) and not typos of each other
        # (two non-adjacent differing letters), so only the short-stem rule
        # can decide this case.
        result = check_answer("пить", "мыть")

        self.assertFalse(result.is_correct)

    def test_rejects_verb_against_adjective(self):
        result = check_answer("готовый", "готовить")

        self.assertFalse(result.is_correct)

    def test_rejects_a_totally_different_word(self):
        result = check_answer("собака", "устанавливать")

        self.assertFalse(result.is_correct)

    def test_rejects_multi_word_answers(self):
        result = check_answer("устанавливать нормы", "устанавливать")

        self.assertFalse(result.is_correct)

    def test_rejects_latin_text(self):
        result = check_answer("ustanovit", "устанавливать")

        self.assertFalse(result.is_correct)

    def test_reverse_check_is_not_affected_by_different_form_matching(self):
        card = Card(front="kommen", back="to come")

        result = check_german_answer("gekommen", card)

        self.assertFalse(result.is_correct)
        self.assertFalse(result.different_form)


class DifferentFormViewTests(AuthedTestCase):
    def test_view_shows_different_form_result_label_and_note(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        Card.objects.create(deck=deck, front="vorübergehend", back="временный")
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = check_typed_answer(self.client, "временно")

        self.assertContains(response, "Correct — different form")
        self.assertContains(response, "Accepted in a different form")
        self.assertContains(response, "временный")


class ExampleSentenceCleanupViewTests(AuthedTestCase):
    def test_bracketed_transcript_tags_are_stripped_from_the_displayed_example(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        card = Card.objects.create(
            deck=deck,
            front="Musik",
            back="музыка",
            example_sentence="Das ist toll [musik] wirklich.",
        )
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        response = self.client.get(reverse("reviews:study_current"))

        self.assertNotContains(response, "[musik]")
        self.assertContains(response, "Das ist toll")
        card.refresh_from_db()
        self.assertEqual(card.example_sentence, "Das ist toll [musik] wirklich.")
