import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, TestCase, override_settings

from video_study.models import DictionaryEntry, NlDictionaryEntry
from video_study.nl.wiktionary import UNAVAILABLE, lookup_nl_dictionary, parse_nl_entry
from video_study.testutils_wiktionary import NL_PAGES, api_body


def fixture(title):
    return title, NL_PAGES[title]


class NlDictionaryEntryTests(TestCase):
    def test_independent_from_the_german_cache(self):
        DictionaryEntry.objects.create(headword="die", status="found", lemma="die")
        entry = NlDictionaryEntry.objects.create(headword="die", status="found", lemma="die")

        self.assertTrue(DictionaryEntry.objects.filter(headword="die").exists())
        self.assertTrue(NlDictionaryEntry.objects.filter(headword="die").exists())
        self.assertEqual(entry.headword, "die")


class ParseNlEntryTests(SimpleTestCase):
    def _fields(self, title):
        f = parse_nl_entry(*fixture(title))
        return (
            f["status"],
            f["lemma"],
            f["base_form"],
            f["article"],
            f["plural"],
            f["part_of_speech"],
        )

    def test_nouns_with_gender_and_plural_shorthand(self):
        self.assertEqual(self._fields("huis"), ("found", "huis", "", "het", "huizen", "noun"))
        self.assertEqual(self._fields("boom"), ("found", "boom", "", "de", "bomen", "noun"))
        self.assertEqual(self._fields("fiets"), ("found", "fiets", "", "de", "fietsen", "noun"))
        self.assertEqual(self._fields("kind"), ("found", "kind", "", "het", "kinderen", "noun"))

    def test_ambiguous_shorthand_leaves_the_plural_blank(self):
        # "bel" + "-en" is "bellen", but single vowel + single consonant can also
        # lengthen ("weg" -> "wegen"); blank beats wrong.
        self.assertEqual(self._fields("bel"), ("found", "bel", "", "de", "", "noun"))

    def test_diminutive_is_a_het_lemma(self):
        fields = parse_nl_entry(*fixture("meisje"))
        self.assertEqual(
            (fields["lemma"], fields["article"], fields["plural"]), ("meisje", "het", "meisjes")
        )
        self.assertEqual(fields["glosses"], "girl; girlfriend")

    def test_diminutive_only_page_is_still_its_own_found_lemma(self):
        wikitext = "==Dutch==\n===Noun===\n{{nl-noun-dim}}\n# {{diminutive of|nl|huis}}\n"
        fields = parse_nl_entry("huisje", wikitext)
        self.assertEqual(
            (
                fields["status"],
                fields["lemma"],
                fields["article"],
                fields["plural"],
                fields["glosses"],
            ),
            ("found", "huisje", "het", "huisjes", ""),
        )

    def test_form_only_pages_point_to_the_base(self):
        self.assertEqual(self._fields("kinderen")[:3], ("found", "kind", "kind"))
        self.assertEqual(self._fields("liep")[:3], ("found", "lopen", "lopen"))

    def test_a_lemma_section_beats_a_later_pointer(self):
        fields = parse_nl_entry(*fixture("huizen"))
        self.assertEqual(
            (fields["lemma"], fields["base_form"], fields["part_of_speech"]), ("huizen", "", "verb")
        )
        self.assertTrue(fields["glosses"].startswith("to reside"))

    def test_obsolete_only_sections_yield_to_a_pointer(self):
        self.assertEqual(self._fields("gelopen")[:3], ("found", "lopen", "lopen"))

    def test_verbs_and_adjectives(self):
        opbellen = parse_nl_entry(*fixture("opbellen"))
        self.assertEqual(
            (opbellen["part_of_speech"], opbellen["glosses"]),
            ("verb", "to telephone someone (by phone)"),
        )
        self.assertEqual(parse_nl_entry(*fixture("lopen"))["part_of_speech"], "verb")
        self.assertEqual(
            parse_nl_entry(*fixture("mooi"))["glosses"],
            "pleasing to the eye, attractive; pleasant or agreeable",
        )
        self.assertEqual(parse_nl_entry(*fixture("snel"))["part_of_speech"], "adjective")

    def test_proper_noun_only_page_is_missing(self):
        self.assertEqual(parse_nl_entry(*fixture("Amsterdam"))["status"], "missing")

    def test_page_without_dutch_section_is_missing(self):
        self.assertEqual(
            parse_nl_entry("house", "==English==\n===Noun===\n# a building")["status"], "missing"
        )

    def test_glosses_are_plain_text_and_at_most_three(self):
        self.assertEqual(parse_nl_entry(*fixture("lopen"))["glosses"].count("; "), 2)
        self.assertNotIn("{{", parse_nl_entry(*fixture("lopen"))["glosses"])

    def test_a_semicolon_inside_one_gloss_line_still_caps_at_three(self):
        # "huis"'s first gloss line is itself "a dwelling, home; abode": the
        # cap must count alternatives across the whole joined text, not lines.
        glosses = parse_nl_entry(*fixture("huis"))["glosses"]
        alternatives = glosses.split("; ")
        self.assertEqual(len(alternatives), 3)
        self.assertEqual(alternatives[0], "a dwelling, home")


def api_response(title):
    """Return a fake urlopen() result carrying a hand-written page."""
    return _FakeResponse(api_body(*fixture(title)))


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


MISSING_RESPONSE = _FakeResponse(
    json.dumps({"error": {"code": "missingtitle", "info": "The page does not exist."}}).encode()
)


@override_settings(VIDEO_STUDY_NL_DICTIONARY_ENDPOINT="https://dictionary.test/api.php")
class LookupNlDictionaryTests(TestCase):
    @patch("video_study.nl.wiktionary.urlopen")
    def test_lemma_page_is_fetched_once_and_cached(self, urlopen):
        urlopen.return_value = api_response("huis")
        first = lookup_nl_dictionary("huis", "huis")
        second = lookup_nl_dictionary("huis", "huis")
        self.assertEqual(first.status, "found")
        self.assertEqual((first.lemma, first.article, first.plural), ("huis", "het", "huizen"))
        self.assertEqual(second, first)
        self.assertEqual(urlopen.call_count, 1)

    @patch("video_study.nl.wiktionary.urlopen")
    def test_request_carries_only_the_word(self, urlopen):
        urlopen.return_value = api_response("huis")
        lookup_nl_dictionary("huis", "huis")
        url = urlopen.call_args.args[0].full_url
        self.assertTrue(url.startswith("https://dictionary.test/api.php?"))
        self.assertIn("page=huis", url)
        self.assertNotIn("context", url)

    @patch("video_study.nl.wiktionary.urlopen")
    def test_pointer_page_follows_to_the_base(self, urlopen):
        urlopen.side_effect = [api_response("kinderen"), api_response("kind")]
        result = lookup_nl_dictionary("kinderen", "kinderen")
        self.assertEqual(
            (result.status, result.lemma, result.article, result.plural),
            ("found", "kind", "het", "kinderen"),
        )
        self.assertEqual(result.glosses, parse_nl_entry(*fixture("kind"))["glosses"])
        self.assertTrue(
            NlDictionaryEntry.objects.filter(headword="kinderen", base_form="kind").exists()
        )

    @patch("video_study.nl.wiktionary.urlopen")
    def test_missing_candidate_falls_back_to_the_surface_form(self, urlopen):
        urlopen.side_effect = [MISSING_RESPONSE, api_response("huis")]
        result = lookup_nl_dictionary("huizzz", "huis")
        self.assertEqual((result.status, result.lemma), ("found", "huis"))
        self.assertEqual(NlDictionaryEntry.objects.get(headword="huizzz").status, "missing")

    @patch("video_study.nl.wiktionary.urlopen", return_value=MISSING_RESPONSE)
    def test_missing_page_is_cached_as_missing(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("quatschwoord", "quatschwoord").status, "missing")
        self.assertEqual(lookup_nl_dictionary("quatschwoord", "quatschwoord").status, "missing")
        self.assertEqual(urlopen.call_count, 1)

    @patch("video_study.nl.wiktionary.urlopen")
    def test_rate_limit_is_unavailable_and_not_cached(self, urlopen):
        urlopen.side_effect = HTTPError("u", 429, "Too Many Requests", {}, None)
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)
        self.assertFalse(NlDictionaryEntry.objects.exists())

    @patch("video_study.nl.wiktionary.urlopen", side_effect=URLError("offline"))
    def test_network_error_is_unavailable(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)

    @patch("video_study.nl.wiktionary.urlopen", side_effect=TimeoutError())
    def test_timeout_is_unavailable(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)

    @patch("video_study.nl.wiktionary.urlopen", return_value=_FakeResponse(b"not json"))
    def test_unreadable_json_is_unavailable(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)

    @patch("video_study.nl.wiktionary.urlopen", return_value=_FakeResponse(b"[]"))
    def test_malformed_envelope_is_unavailable_and_not_cached(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)
        self.assertFalse(NlDictionaryEntry.objects.exists())

    @patch("video_study.nl.wiktionary.urlopen")
    @patch("video_study.nl.wiktionary.time.monotonic", side_effect=[0, 100])
    def test_spent_budget_makes_no_request(self, monotonic, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)
        urlopen.assert_not_called()

    @override_settings(VIDEO_STUDY_NL_DICTIONARY_ENDPOINT="")
    @patch("video_study.nl.wiktionary.urlopen")
    def test_empty_endpoint_disables_lookups(self, urlopen):
        self.assertEqual(lookup_nl_dictionary("huis", "huis").status, UNAVAILABLE)
        urlopen.assert_not_called()

    @patch("video_study.nl.wiktionary.urlopen")
    def test_dutch_lookup_does_not_touch_the_german_table(self, urlopen):
        urlopen.return_value = api_response("huis")
        lookup_nl_dictionary("huis", "huis")
        self.assertFalse(DictionaryEntry.objects.exists())
