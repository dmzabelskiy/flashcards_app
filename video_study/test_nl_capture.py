import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from core.testutils import AuthedTestCase
from video_study.models import WordTranslation
from video_study.nl.capture import resolve_capture_nl
from video_study.nl.wiktionary import NlDictionaryResult, parse_nl_entry
from video_study.normalization import CaptureResolution
from video_study.testutils_lemma import LemmaCacheTestMixin
from video_study.testutils_wiktionary import NL_PAGES


def _fixture_result(title):
    """Parse a hand-written Wiktionary page into an `NlDictionaryResult`."""
    fields = parse_nl_entry(title, NL_PAGES[title])
    return NlDictionaryResult(
        status=fields["status"],
        lemma=fields["lemma"],
        article=fields["article"],
        plural=fields["plural"],
        part_of_speech=fields["part_of_speech"],
        glosses=fields["glosses"],
    )


def _fixture_lookup(*titles):
    """Build a `lookup_nl_dictionary` fake from parsed fixtures, missing otherwise."""
    entries = {title: _fixture_result(title) for title in titles}

    def fake(candidate, surface, budget_start=None):
        return entries.get(candidate, NlDictionaryResult(status="missing"))

    return fake


FOUND_HUIS = NlDictionaryResult(
    status="found",
    lemma="huis",
    article="het",
    plural="huizen",
    part_of_speech="noun",
    glosses="house; home",
)


class ResolveCaptureNlTests(LemmaCacheTestMixin, SimpleTestCase):
    @patch("video_study.nl.capture.lookup_nl_dictionary", return_value=FOUND_HUIS)
    def test_found_noun_carries_article_plural_and_translation(self, lookup):
        r = resolve_capture_nl("huizen", "De huizen zijn duur.")
        self.assertEqual(
            (r.lemma, r.article, r.plural, r.part_of_speech, r.translation, r.trustworthy),
            ("huis", "het", "huizen", "noun", "house; home", True),
        )

    @patch("video_study.nl.capture.lookup_nl_dictionary", return_value=FOUND_HUIS)
    def test_sentence_initial_capital_is_lowercased(self, lookup):
        resolve_capture_nl("Huizen", "Huizen zijn duur.")
        candidate, surface = lookup.call_args.args[:2]
        self.assertEqual((candidate, surface), (candidate.lower(), "huizen"))

    @patch("video_study.nl.capture.lookup_nl_dictionary", return_value=FOUND_HUIS)
    def test_leading_article_is_split_off(self, lookup):
        r = resolve_capture_nl("het huis", "Ik zie het huis.")
        self.assertEqual((r.lemma, r.article), ("huis", "het"))

    @patch("video_study.nl.capture.lookup_nl_dictionary")
    def test_phrase_skips_the_dictionary(self, lookup):
        r = resolve_capture_nl("heel erg mooi", "Het is heel erg mooi.")
        self.assertEqual(
            (r.part_of_speech, r.dictionary_status, r.translation), ("phrase", "skipped", "")
        )
        lookup.assert_not_called()

    @patch("video_study.nl.capture.lookup_nl_dictionary")
    def test_article_plus_a_phrase_is_still_a_phrase(self, lookup):
        r = resolve_capture_nl("de mooie fiets", "Ik zie de mooie fiets.")
        self.assertEqual(
            (r.part_of_speech, r.article, r.dictionary_status, r.translation),
            ("phrase", "", "skipped", ""),
        )
        lookup.assert_not_called()

    def test_separable_verb_is_joined_when_confirmed(self):
        def fake(candidate, surface, budget_start=None):
            if candidate == "opbellen":
                return NlDictionaryResult(
                    status="found", lemma="opbellen", part_of_speech="verb", glosses="to call up"
                )
            return NlDictionaryResult(
                status="found", lemma="bellen", part_of_speech="verb", glosses="to ring"
            )

        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("bel", "Ik bel je morgen op.")
        self.assertEqual((r.lemma, r.translation), ("opbellen", "to call up"))

    def test_unconfirmed_particle_is_not_joined(self):
        found = NlDictionaryResult(
            status="found", lemma="lopen", part_of_speech="verb", glosses="to walk"
        )
        missing = NlDictionaryResult(status="missing")
        with patch(
            "video_study.nl.capture.lookup_nl_dictionary",
            side_effect=lambda c, s, budget_start=None: missing if c == "oplopen" else found,
        ):
            r = resolve_capture_nl("liep", "Hij liep de trap op.")
        self.assertEqual(r.lemma, "lopen")

    def test_particle_probe_is_skipped_after_a_determiner(self):
        found_bel = NlDictionaryResult(
            status="found", lemma="bel", article="de", part_of_speech="noun", glosses="bell"
        )
        with patch("video_study.nl.capture.lookup_nl_dictionary", return_value=found_bel) as lookup:
            r = resolve_capture_nl("bel", "Ik hang de bel op.")
        self.assertEqual((r.lemma, r.part_of_speech, r.translation), ("bel", "noun", "bell"))
        lookup.assert_called_once_with("bel", "bel", budget_start=None)

    def test_determiner_noun_falls_back_to_the_candidate_when_unconfirmed(self):
        missing = NlDictionaryResult(status="missing")
        found_bellen = NlDictionaryResult(
            status="found", lemma="bellen", part_of_speech="verb", glosses="to ring"
        )

        def fake(candidate, surface, budget_start=None):
            return missing if candidate == "bel" else found_bellen

        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("bel", "Ik hoor een bel klinken.")
        self.assertEqual((r.lemma, r.part_of_speech), ("bellen", "verb"))

    @patch(
        "video_study.nl.capture.lookup_nl_dictionary",
        return_value=NlDictionaryResult(status="unavailable"),
    )
    def test_fallback_articles_from_suffixes(self, lookup):
        self.assertEqual(resolve_capture_nl("katje", "Een katje.").article, "het")
        self.assertEqual(resolve_capture_nl("vrijheid", "De vrijheid.").article, "de")
        r = resolve_capture_nl("tafel", "Een tafel.")
        self.assertEqual((r.article, r.dictionary_status, r.translation), ("", "unavailable", ""))

    @patch(
        "video_study.nl.capture.lookup_nl_dictionary",
        return_value=NlDictionaryResult(status="missing"),
    )
    def test_diaeresis_and_apostrophe_words_stay_whole(self, lookup):
        for word in ("geïnteresseerd", "ideeën", "auto's"):
            resolve_capture_nl(word, f"Het gaat om {word}.")
            self.assertEqual(lookup.call_args.args[1], word)

    def test_lexicalised_diminutive_keeps_its_own_surface(self):
        found_broodje = NlDictionaryResult(
            status="found",
            lemma="broodje",
            article="het",
            plural="broodjes",
            part_of_speech="noun",
            glosses="bread roll",
        )
        with patch(
            "video_study.nl.capture.lookup_nl_dictionary", return_value=found_broodje
        ) as lookup:
            r = resolve_capture_nl("broodje", "Ik eet een broodje.")
        self.assertEqual((r.lemma, r.article, r.translation), ("broodje", "het", "bread roll"))
        lookup.assert_called_once_with("broodje", "broodje", budget_start=None)

    def test_diminutive_with_no_glosses_still_resolves_as_itself(self):
        found_huisje = NlDictionaryResult(
            status="found",
            lemma="huisje",
            article="het",
            plural="huisjes",
            part_of_speech="noun",
            glosses="",
        )
        with patch("video_study.nl.capture.lookup_nl_dictionary", return_value=found_huisje):
            r = resolve_capture_nl("huisje", "Een huisje aan het water.")
        self.assertEqual(
            (r.lemma, r.article, r.plural, r.translation, r.dictionary_status),
            ("huisje", "het", "huisjes", "", "found"),
        )

    def test_diminutive_missing_as_its_own_page_falls_back_to_the_candidate(self):
        missing = NlDictionaryResult(status="missing")
        found_kat = NlDictionaryResult(
            status="found",
            lemma="kat",
            article="de",
            plural="katten",
            part_of_speech="noun",
            glosses="cat",
        )

        def fake(candidate, surface, budget_start=None):
            return missing if candidate == "katje" else found_kat

        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("katje", "Een katje.")
        self.assertEqual((r.lemma, r.article), ("kat", "de"))


class ResolveCaptureNlWithFixturesTests(LemmaCacheTestMixin, SimpleTestCase):
    """Exercises the real lemmatiser against dictionary entries parsed from fixtures."""

    def test_noun_after_a_determiner_is_not_the_verb(self):
        fake = _fixture_lookup("bel", "bellen")
        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("bel", "Ik hang de bel op.")
        self.assertEqual((r.lemma, r.part_of_speech, r.article), ("bel", "noun", "de"))
        self.assertTrue(r.translation.startswith("bell"))

    def test_separable_verb_still_wins_without_a_preceding_determiner(self):
        fake = _fixture_lookup("bel", "bellen", "opbellen")
        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("bel", "Ik bel je morgen op.")
        self.assertEqual(r.lemma, "opbellen")

    def test_plural_noun_after_a_determiner_resolves_to_its_lemma(self):
        # "huizen" is both the plural of "huis" and its own verb lemma; the
        # verb's own page must not shadow the noun once a determiner rules
        # the verb out.
        fake = _fixture_lookup("huis", "huizen")
        with patch("video_study.nl.capture.lookup_nl_dictionary", side_effect=fake):
            r = resolve_capture_nl("huizen", "De huizen zijn duur.")
        self.assertEqual((r.lemma, r.article), ("huis", "het"))


@override_settings(VIDEO_STUDY_NL_DICTIONARY_ENDPOINT="")
class NlTranslateWordTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = "NL-EN"
        session.save()

    def _post(self, text, context):
        return self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": text, "context": context, "source_language": "nl"}),
            content_type="application/json",
        )

    @patch("video_study.views.request_translation")
    @patch(
        "video_study.resolvers.resolve_capture_nl",
        return_value=CaptureResolution(
            lemma="huis",
            article="het",
            plural="huizen",
            part_of_speech="noun",
            example_sentence="",
            dictionary_status="found",
            trustworthy=True,
            translation="house; home",
        ),
    )
    def test_wiktionary_glosses_are_the_translation(self, resolve, provider):
        body = self._post("huizen", "De huizen.").json()
        self.assertEqual(
            (body["word"], body["translation"], body["suggestion"]["gender"]),
            ("het huis", "house; home", "neuter"),
        )
        provider.assert_not_called()
        row = WordTranslation.objects.get(
            source_text="huis", source_language="nl", target_language="en"
        )
        self.assertEqual(row.provider, "wiktionary-en")

    @patch("video_study.views.request_translation", return_value="to call")
    @patch(
        "video_study.resolvers.resolve_capture_nl",
        return_value=CaptureResolution(
            lemma="tafel",
            article="",
            plural="",
            part_of_speech="",
            example_sentence="",
            dictionary_status="unavailable",
            trustworthy=True,
        ),
    )
    def test_without_glosses_the_provider_chain_runs(self, resolve, provider):
        self.assertEqual(self._post("tafel", "Een tafel.").json()["translation"], "to call")
        provider.assert_called_once()

    @patch("video_study.resolvers.resolve_capture")
    @patch(
        "video_study.resolvers.resolve_capture_nl",
        return_value=CaptureResolution(
            lemma="huis",
            article="het",
            plural="huizen",
            part_of_speech="noun",
            example_sentence="",
            dictionary_status="found",
            trustworthy=True,
            translation="house",
        ),
    )
    def test_german_resolver_is_not_used_for_dutch(self, resolve_nl, resolve_de):
        self._post("huis", "Het huis.")
        resolve_de.assert_not_called()

    def test_german_source_language_is_rejected_in_nl_mode(self):
        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": "Haus", "context": "Das Haus.", "source_language": "de"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class NlDiminutiveTranslateWordTests(LemmaCacheTestMixin, AuthedTestCase):
    """Exercises the real resolve_capture_nl, only the dictionary fetch is mocked."""

    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = "NL-EN"
        session.save()

    @patch("video_study.views.request_translation", return_value="a small house")
    @patch("video_study.nl.capture.lookup_nl_dictionary")
    def test_diminutive_without_glosses_falls_through_to_the_provider_chain(self, lookup, provider):
        lookup.return_value = NlDictionaryResult(
            status="found",
            lemma="huisje",
            article="het",
            plural="huisjes",
            part_of_speech="noun",
            glosses="",
        )
        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps(
                {"text": "huisje", "context": "Een huisje aan het water.", "source_language": "nl"}
            ),
            content_type="application/json",
        )
        body = response.json()
        self.assertEqual((body["word"], body["translation"]), ("het huisje", "a small house"))
        provider.assert_called_once()
        self.assertFalse(WordTranslation.objects.filter(provider="wiktionary-en").exists())
