from unittest.mock import patch

from django.test import SimpleTestCase

from video_study.dictionary import DictionaryResult
from video_study.metadata import LemmaCacheUnavailable
from video_study.normalization import resolve_capture
from video_study.testutils_lemma import LemmaCacheTestMixin


class ResolveCaptureTests(LemmaCacheTestMixin, SimpleTestCase):
    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(
            status="found",
            lemma="Haus",
            article="das",
            plural="Häuser",
            part_of_speech="noun",
            example_sentence="Das Haus steht seit 1898.",
        ),
    )
    def test_dictionary_entry_wins(self, lookup):
        result = resolve_capture("Häusern", "In den Häusern wohnen viele Leute.")
        self.assertEqual(
            (result.lemma, result.article, result.plural, result.part_of_speech),
            ("Haus", "das", "Häuser", "noun"),
        )
        self.assertEqual(result.example_sentence, "Das Haus steht seit 1898.")
        self.assertEqual(result.dictionary_status, "found")
        self.assertTrue(result.trustworthy)
        lookup.assert_called_once_with("Haus", "Häusern", budget_start=None)

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="found", lemma="gehen", part_of_speech="verb"),
    )
    def test_non_nouns_carry_no_article_or_plural(self, lookup):
        result = resolve_capture("Gehen", "Gehen wir?")
        self.assertEqual((result.article, result.plural), ("", ""))

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(
            status="found", lemma="Band", part_of_speech="noun", plural="Bänder", article=""
        ),
    )
    def test_found_noun_with_no_dictionary_article_stays_empty(self, lookup):
        # The heuristic would guess "das" from context here; the dictionary
        # confirmed the entry but gave no genus, and that empty article wins.
        result = resolve_capture("Band", "Das Band ist lang.")
        self.assertEqual(result.article, "")
        self.assertEqual(result.plural, "Bänder")

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="found", lemma="X", part_of_speech="other"),
    )
    def test_found_other_part_of_speech_is_not_reclassified(self, lookup):
        result = resolve_capture("X", "context")
        self.assertEqual(result.part_of_speech, "other")

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="unavailable"),
    )
    def test_unavailable_dictionary_falls_back_to_the_lemmatiser(self, lookup):
        result = resolve_capture("gegangen", "Ich bin nach Hause gegangen.")
        self.assertEqual(result.lemma, "gehen")
        self.assertEqual(result.example_sentence, "")
        self.assertEqual(result.dictionary_status, "unavailable")
        self.assertTrue(result.trustworthy)

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="unavailable"),
    )
    def test_no_source_at_all_is_not_trustworthy(self, lookup):
        with patch(
            "video_study.metadata.get_lemmatizer",
            side_effect=LemmaCacheUnavailable("missing"),
        ):
            result = resolve_capture("Häusern", "")
        self.assertFalse(result.trustworthy)

    @patch("video_study.normalization.lookup_dictionary")
    def test_phrases_skip_the_dictionary(self, lookup):
        result = resolve_capture("zum Beispiel", "Das ist zum Beispiel gut.")
        self.assertEqual((result.lemma, result.part_of_speech), ("zum Beispiel", "phrase"))
        self.assertEqual(result.dictionary_status, "skipped")
        lookup.assert_not_called()

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="missing"),
    )
    def test_eszett_survives_because_the_original_case_is_used(self, lookup):
        result = resolve_capture("größeren", "in größeren Städten")
        self.assertEqual(result.lemma, "groß")

    @patch(
        "video_study.normalization.lookup_dictionary",
        return_value=DictionaryResult(status="missing"),
    )
    def test_article_selection_looks_up_the_bare_noun(self, lookup):
        resolve_capture("den Kopf", "Er schüttelt den Kopf.")
        self.assertEqual(lookup.call_args.args[1], "Kopf")
