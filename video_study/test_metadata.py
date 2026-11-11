from unittest.mock import patch

from django.test import SimpleTestCase
from simplemma.strategies.dictionaries import TrieDictionaryFactory

from video_study.metadata import (
    LemmaCacheUnavailable,
    get_lemmatizer,
    lemmatize_german,
    lemmatize_german_detailed,
    suggest_word_metadata,
)
from video_study.testutils_lemma import LemmaCacheTestMixin


class LemmatizerFactoryTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_get_lemmatizer_uses_the_trie_dictionary_factory(self):
        lemmatizer = get_lemmatizer()

        # simplemma has no public accessor for a Lemmatizer's dictionary
        # factory; reach through the private chain it does expose.
        dictionary_lookup = lemmatizer._lemmatization_strategy._dictionary_lookup
        self.assertIsInstance(dictionary_lookup._dictionary_factory, TrieDictionaryFactory)

    def test_get_lemmatizer_returns_the_same_instance(self):
        self.assertIs(get_lemmatizer(), get_lemmatizer())


class WordMetadataTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_conjugated_irregular_verb_is_saved_as_an_infinitive(self):
        metadata = suggest_word_metadata("geht", "Er geht nach Hause.", source_form="geht")

        self.assertEqual(metadata["word"], "gehen")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_regular_past_participle_is_saved_as_an_infinitive(self):
        metadata = suggest_word_metadata(
            "gemacht",
            "Er hat das gemacht.",
            source_form="gemacht",
        )

        self.assertEqual(metadata["word"], "machen")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_noun_gets_the_article_from_its_sentence(self):
        metadata = suggest_word_metadata("Kopf", "Das ist der Kopf.", source_form="Kopf")

        self.assertEqual(metadata["word"], "Kopf")
        self.assertEqual(metadata["article"], "der")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_explicit_article_is_removed_from_the_saved_noun_stem(self):
        metadata = suggest_word_metadata("der Kopf", "", source_form="der Kopf")

        self.assertEqual(metadata["word"], "Kopf")
        self.assertEqual(metadata["article"], "der")

    def test_inflected_noun_is_saved_as_its_lemma_with_article_and_original_casing(self):
        metadata = suggest_word_metadata(
            "häusern",
            "In den Häusern wohnen viele Familien.",
            source_form="Häusern",
        )

        self.assertEqual(metadata["word"], "Haus")
        self.assertEqual(metadata["normalized_word"], "Haus")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_inflected_dative_plural_noun_gets_no_article_from_the_determiner(self):
        metadata = suggest_word_metadata(
            "Häusern",
            "In den Häusern war es still.",
            source_form="Häusern",
        )

        self.assertEqual(metadata["word"], "Haus")
        self.assertEqual(metadata["article"], "")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_inflected_plural_noun_with_old_spelling_gets_no_article(self):
        metadata = suggest_word_metadata(
            "Einflüsse",
            "Die Einflüsse sind groß.",
            source_form="Einflüsse",
        )

        self.assertEqual(metadata["word"], "Einfluss")
        self.assertEqual(metadata["article"], "")

    def test_inflected_plural_noun_gets_article_from_noun_ending_not_determiner(self):
        metadata = suggest_word_metadata(
            "Beziehungen",
            "Die Beziehungen sind gut.",
            source_form="Beziehungen",
        )

        self.assertEqual(metadata["word"], "Beziehung")
        self.assertEqual(metadata["article"], "die")

    def test_capitalised_noun_is_not_lemmatised_into_a_lowercase_verb(self):
        metadata = suggest_word_metadata(
            "Unterschied", "Das ist ein Unterschied.", source_form="Unterschied"
        )

        self.assertEqual(metadata["word"], "Unterschied")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_explicit_article_noun_is_not_lemmatised_into_a_lowercase_verb(self):
        metadata = suggest_word_metadata("der Unterschied", "", source_form="der Unterschied")

        self.assertEqual(metadata["word"], "Unterschied")
        self.assertEqual(metadata["article"], "der")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_capitalised_geraet_is_not_lemmatised_into_geraten(self):
        metadata = suggest_word_metadata("Gerät", "Das ist ein Gerät.", source_form="Gerät")

        self.assertEqual(metadata["word"], "Gerät")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_explicit_article_geraet_is_not_lemmatised_into_geraten(self):
        metadata = suggest_word_metadata("das Gerät", "", source_form="das Gerät")

        self.assertEqual(metadata["word"], "Gerät")
        self.assertEqual(metadata["article"], "das")

    def test_explicit_article_with_a_lowercase_transcript_stem_still_keeps_the_noun(self):
        metadata = suggest_word_metadata("der unterschied", "", source_form="der unterschied")

        self.assertEqual(metadata["word"], "Unterschied")
        self.assertEqual(metadata["article"], "der")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_uninflected_noun_still_gets_article_from_context(self):
        metadata = suggest_word_metadata("Haus", "Das Haus ist alt.", source_form="Haus")

        self.assertEqual(metadata["word"], "Haus")
        self.assertEqual(metadata["article"], "das")

    def test_explicit_inflected_definite_article_den_resolves_to_der(self):
        metadata = suggest_word_metadata("den Antrag", "", source_form="den Antrag")

        self.assertEqual(metadata["word"], "Antrag")
        self.assertEqual(metadata["article"], "der")
        self.assertEqual(metadata["part_of_speech"], "noun")

    def test_explicit_die_before_a_plural_noun_gives_no_article(self):
        metadata = suggest_word_metadata("die Kinder", "", source_form="die Kinder")

        self.assertEqual(metadata["word"], "Kind")
        self.assertEqual(metadata["article"], "")

    def test_explicit_definite_article_das(self):
        metadata = suggest_word_metadata("das Kind", "", source_form="das Kind")

        self.assertEqual(metadata["word"], "Kind")
        self.assertEqual(metadata["article"], "das")

    def test_explicit_indefinite_article_eine_resolves_to_die(self):
        metadata = suggest_word_metadata("eine Beziehung", "", source_form="eine Beziehung")

        self.assertEqual(metadata["word"], "Beziehung")
        self.assertEqual(metadata["article"], "die")

    def test_present_participle_adjective_is_not_lemmatised_to_a_verb(self):
        metadata = suggest_word_metadata(
            "vorübergehend",
            "Das ist nur vorübergehend.",
            source_form="vorübergehend",
        )

        self.assertEqual(metadata["word"], "vorübergehend")
        self.assertEqual(metadata["part_of_speech"], "adjective")

    def test_lich_adjective_is_not_lemmatised_to_a_verb(self):
        metadata = suggest_word_metadata(
            "unerlässlich",
            "Das ist unerlässlich.",
            source_form="unerlässlich",
        )

        self.assertEqual(metadata["word"], "unerlässlich")
        self.assertEqual(metadata["part_of_speech"], "adjective")

    def test_inflected_participle_adjective_is_stripped_to_its_end_base(self):
        metadata = suggest_word_metadata(
            "umfassende",
            "Eine umfassende Antwort.",
            source_form="umfassende",
        )

        self.assertEqual(metadata["word"], "umfassend")
        self.assertEqual(metadata["part_of_speech"], "adjective")

    def test_verb_infinitive_ending_in_enden_is_not_mangled_into_a_participle(self):
        for word in ("vollenden", "beenden", "senden"):
            with self.subTest(word=word):
                metadata = suggest_word_metadata(word, "", source_form=word)

                self.assertEqual(metadata["word"], word)
                self.assertEqual(metadata["part_of_speech"], "verb")

    def test_inflected_verb_form_ending_in_ende_is_not_mangled_into_a_stub(self):
        metadata = suggest_word_metadata("wende", "", source_form="wende")

        self.assertIn(metadata["word"], ("wenden", "wende"))
        self.assertNotEqual(metadata["word"], "wend")

    def test_comparative_adjective_still_lemmatises_correctly(self):
        metadata = suggest_word_metadata(
            "schnellere",
            "Ein schnellere Auto.",
            source_form="schnellere",
        )

        self.assertEqual(metadata["word"], "schnell")

    def test_regular_verb_ending_in_t_is_still_a_verb(self):
        metadata = suggest_word_metadata(
            "verhindert",
            "Das verhindert den Fortschritt.",
            source_form="verhindert",
        )

        self.assertEqual(metadata["word"], "verhindern")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_participle_listed_as_its_own_headword_becomes_the_infinitive_after_an_object(self):
        # simplemma lists "betäubt" as a headword (participial adjective), so
        # plain lemmatisation returns it unchanged; a noun object right before
        # it marks the finite-verb use, which wants the infinitive.
        metadata = suggest_word_metadata(
            "betäubt",
            "Klar ist, dass Alkohol deine Neuronen betäubt, sie verlangsamt und ihre",
            source_form="betäubt",
        )

        self.assertEqual(metadata["word"], "betäuben")
        self.assertEqual(metadata["normalized_word"], "betäuben")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_participle_after_a_haben_auxiliary_becomes_the_infinitive(self):
        metadata = suggest_word_metadata(
            "enttäuscht",
            "Das hat mich enttäuscht.",
            source_form="enttäuscht",
        )

        self.assertEqual(metadata["word"], "enttäuschen")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_participle_ending_in_et_becomes_the_infinitive(self):
        metadata = suggest_word_metadata(
            "verheiratet",
            "Sie haben sich verheiratet.",
            source_form="verheiratet",
        )

        self.assertEqual(metadata["word"], "verheiraten")
        self.assertEqual(metadata["part_of_speech"], "verb")

    def test_predicative_participle_adjective_after_sein_keeps_its_form(self):
        # "verrücken" is a real verb, but "ist verrückt" is the adjective.
        metadata = suggest_word_metadata("verrückt", "Er ist verrückt.", source_form="verrückt")

        self.assertEqual(metadata["word"], "verrückt")

    def test_participle_without_a_verb_cue_keeps_its_form(self):
        metadata = suggest_word_metadata(
            "betäubt",
            "ein betäubt wirkender Blick",
            source_form="betäubt",
        )

        self.assertEqual(metadata["word"], "betäubt")

    def test_phrase_selection_is_left_unchanged_with_phrase_part_of_speech(self):
        metadata = suggest_word_metadata(
            "guten tag",
            "Ich sage guten Tag.",
            source_form="guten tag",
        )

        self.assertEqual(metadata["word"], "guten tag")
        self.assertEqual(metadata["normalized_word"], "guten tag")
        self.assertEqual(metadata["article"], "")
        self.assertEqual(metadata["part_of_speech"], "phrase")


class LemmatizeGermanTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_lemmatizes_offline_examples(self):
        cases = {
            "Häusern": "Haus",
            "gegangen": "gehen",
            "bräuchte": "brauchen",
            "schnellere": "schnell",
            "Anträge": "Antrag",
            "läuft": "laufen",
            "angerufen": "anrufen",
            "Kinder": "Kind",
            "gesprochen": "sprechen",
            "besser": "gut",
            "verstanden": "verstehen",
            "auffällt": "auffallen",
            "möchte": "mögen",
            "Beziehungen": "Beziehung",
        }
        for word, expected_lemma in cases.items():
            with self.subTest(word=word):
                self.assertEqual(lemmatize_german(word), expected_lemma)

    def test_capitalised_token_keeps_its_capital_over_a_lowercase_lemma(self):
        cases = {
            "Unterschied": "Unterschied",
            "Gerät": "Gerät",
        }
        for word, expected_lemma in cases.items():
            with self.subTest(word=word):
                self.assertEqual(lemmatize_german(word), expected_lemma)

    def test_lowercase_verb_form_is_unaffected(self):
        self.assertEqual(lemmatize_german("unterschied"), "unterscheiden")
        self.assertEqual(lemmatize_german("gerät"), "geraten")

    def test_old_spelling_ss_is_normalised_to_modern_ss(self):
        self.assertEqual(lemmatize_german("Einflüsse"), "Einfluss")

    def test_a_source_with_a_real_eszett_keeps_the_eszett_in_the_lemma(self):
        self.assertEqual(lemmatize_german("Füßen"), "Fuß")

    def test_curated_irregular_table_wins_over_the_statistical_lemmatiser(self):
        # simplemma alone fails to lemmatize this past participle back to its
        # infinitive; the curated IRREGULAR_FORM_TO_INFINITIVE table catches it.
        self.assertEqual(lemmatize_german("gebacken"), "backen")

    def test_a_word_that_is_already_a_lemma_keeps_its_original_casing(self):
        self.assertEqual(lemmatize_german("SCHNELL"), "SCHNELL")

    def test_multi_word_input_is_returned_unchanged(self):
        self.assertEqual(lemmatize_german("guten tag"), "guten tag")


class SeparableVerbTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_particle_at_the_end_of_the_clause_joins_the_verb(self):
        result = suggest_word_metadata("steht", "Er steht morgen früh auf.", source_form="steht")
        self.assertEqual(result["normalized_word"], "aufstehen")
        self.assertEqual(result["part_of_speech"], "verb")

    def test_particle_closes_the_first_clause_only(self):
        result = suggest_word_metadata(
            "steht", "Er steht um sieben Uhr auf, dann frühstückt er.", source_form="steht"
        )
        self.assertEqual(result["normalized_word"], "aufstehen")

    def test_particle_in_a_later_clause_does_not_attach(self):
        result = suggest_word_metadata(
            "steht", "Er steht draußen, und dann geht die Tür auf.", source_form="steht"
        )
        self.assertEqual(result["normalized_word"], "stehen")

    def test_combination_unknown_to_the_dictionary_is_not_used(self):
        result = suggest_word_metadata("steht", "Er steht einfach mit.", source_form="steht")
        self.assertEqual(result["normalized_word"], "stehen")

    def test_non_verbs_ignore_trailing_particles(self):
        result = suggest_word_metadata("Tür", "Er macht die Tür zu.", source_form="Tür")
        self.assertEqual(result["normalized_word"], "Tür")

    def test_repeated_verb_across_clauses_does_not_attach(self):
        result = suggest_word_metadata(
            "steht", "Er steht auf, und er steht noch.", source_form="steht"
        )
        self.assertEqual(result["normalized_word"], "stehen")

    def test_single_occurrence_still_attaches_the_particle(self):
        result = suggest_word_metadata("steht", "Er steht auf.", source_form="steht")
        self.assertEqual(result["normalized_word"], "aufstehen")


class LemmatizerAvailabilityTests(LemmaCacheTestMixin, SimpleTestCase):
    def test_detailed_reports_available_for_a_normal_lookup(self):
        self.assertEqual(lemmatize_german_detailed("Häusern"), ("Haus", True))

    def test_detailed_reports_unavailable_when_the_cache_fails(self):
        with patch(
            "video_study.metadata.get_lemmatizer",
            side_effect=LemmaCacheUnavailable("missing"),
        ):
            self.assertEqual(lemmatize_german_detailed("Häusern"), ("Häusern", False))

    def test_suggestion_carries_the_availability_flag(self):
        self.assertTrue(suggest_word_metadata("Häusern", "")["lemmatizer_available"])
        with patch(
            "video_study.metadata.get_lemmatizer",
            side_effect=LemmaCacheUnavailable("missing"),
        ):
            self.assertFalse(suggest_word_metadata("Häusern", "")["lemmatizer_available"])

    def test_phrases_count_as_available(self):
        self.assertTrue(suggest_word_metadata("zum Beispiel", "")["lemmatizer_available"])
