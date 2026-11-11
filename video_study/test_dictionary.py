import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import TestCase, override_settings

from video_study.dictionary import (
    FOUND,
    MISSING,
    UNAVAILABLE,
    DictionaryUnavailable,
    lookup_dictionary,
    parse_entry,
)
from video_study.models import DictionaryEntry
from video_study.testutils_wiktionary import DE_PAGES, api_body


def fixture(title):
    return title, DE_PAGES[title]


class ParseEntryTests(TestCase):
    def test_noun_gives_article_plural_and_a_plain_example(self):
        entry = parse_entry(*fixture("Haus"))
        self.assertEqual(entry["status"], FOUND)
        self.assertEqual(entry["lemma"], "Haus")
        self.assertEqual(entry["base_form"], "")
        self.assertEqual(entry["article"], "das")
        self.assertEqual(entry["plural"], "Häuser")
        self.assertEqual(entry["part_of_speech"], "noun")
        self.assertEqual(entry["example_sentence"], "Unser Haus hat ein rotes Dach.")

    def test_toponym_page_with_a_pointer_resolves_to_the_base(self):
        entry = parse_entry(*fixture("Häusern"))
        self.assertEqual(entry["status"], FOUND)
        self.assertEqual(entry["base_form"], "Haus")
        self.assertEqual(entry["lemma"], "Haus")

    def test_participle_page_points_to_the_infinitive(self):
        self.assertEqual(parse_entry(*fixture("gegangen"))["base_form"], "gehen")

    def test_verb_skips_the_opening_quotation(self):
        entry = parse_entry(*fixture("gehen"))
        self.assertEqual(entry["part_of_speech"], "verb")
        self.assertEqual(entry["article"], "")
        self.assertEqual(entry["plural"], "")
        self.assertEqual(entry["example_sentence"], "Wir gehen am Sonntag spazieren.")

    def test_separable_verb_example(self):
        entry = parse_entry(*fixture("aufstehen"))
        self.assertEqual(entry["example_sentence"], "Sie ist heute zeitig aufgestanden.")

    def test_adjective(self):
        entry = parse_entry(*fixture("schnell"))
        self.assertEqual(entry["part_of_speech"], "adjective")
        self.assertEqual(entry["example_sentence"], "Wir nehmen den schnelleren Weg.")

    def test_page_without_a_german_section_is_missing(self):
        wikitext = "== house ({{Sprache|Englisch}}) ==\n=== {{Wortart|Substantiv|Englisch}} ===\n"
        self.assertEqual(parse_entry("house", wikitext)["status"], MISSING)

    def test_missing_examples_and_markup_cleaning(self):
        wikitext = (
            "== Probe ({{Sprache|Deutsch}}) ==\n"
            "=== {{Wortart|Substantiv|Deutsch}}, {{f}} ===\n"
            "{{Deutsch Substantiv Übersicht\n|Genus=f\n|Nominativ Plural=Proben\n}}\n"
            "{{Beispiele}}\n"
            ":[1] {{Beispiele fehlen}}\n"
            ":[1, 2] Die ''[[Probe|Probe]]''&nbsp;war [[kurz]].<ref>{{Lit|x}}</ref>\n"
        )
        entry = parse_entry("Probe", wikitext)
        self.assertEqual(entry["article"], "die")
        self.assertEqual(entry["plural"], "Proben")
        self.assertEqual(entry["example_sentence"], "Die Probe war kurz.")

    def test_long_quotations_are_not_used(self):
        long_quote = "„" + "Wort " * 60 + "“<ref>x</ref>"
        wikitext = (
            "== Wort ({{Sprache|Deutsch}}) ==\n"
            "=== {{Wortart|Substantiv|Deutsch}}, {{n}} ===\n"
            "{{Deutsch Substantiv Übersicht\n|Genus=n\n|Nominativ Plural=—\n}}\n"
            "{{Beispiele}}\n"
            f":[1] {long_quote}\n"
        )
        entry = parse_entry("Wort", wikitext)
        self.assertEqual(entry["example_sentence"], "")
        self.assertEqual(entry["plural"], "")

    def test_one_line_noun_overview_template_is_parsed(self):
        wikitext = (
            "== Haus ({{Sprache|Deutsch}}) ==\n"
            "=== {{Wortart|Substantiv|Deutsch}}, {{n}} ===\n"
            "{{Deutsch Substantiv Übersicht|Genus=n|Nominativ Plural=Häuser}}\n"
        )
        entry = parse_entry("Haus", wikitext)
        self.assertEqual(entry["article"], "das")
        self.assertEqual(entry["plural"], "Häuser")


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


@override_settings(VIDEO_STUDY_DICTIONARY_ENDPOINT="https://dictionary.test/api.php")
class LookupDictionaryTests(TestCase):
    @patch("video_study.dictionary.urlopen")
    def test_lemma_page_is_fetched_once_and_cached(self, urlopen):
        urlopen.return_value = api_response("Haus")
        first = lookup_dictionary("Haus", "Häusern")
        second = lookup_dictionary("Haus", "Häusern")
        self.assertEqual(first.status, FOUND)
        self.assertEqual((first.lemma, first.article, first.plural), ("Haus", "das", "Häuser"))
        self.assertEqual(second, first)
        self.assertEqual(urlopen.call_count, 1)

    @patch("video_study.dictionary.urlopen")
    def test_request_carries_only_the_word(self, urlopen):
        urlopen.return_value = api_response("Haus")
        lookup_dictionary("Haus", "Häusern")
        url = urlopen.call_args.args[0].full_url
        self.assertTrue(url.startswith("https://dictionary.test/api.php?"))
        self.assertIn("page=Haus", url)
        self.assertNotIn("context", url)

    @patch("video_study.dictionary.urlopen")
    def test_pointer_page_follows_to_the_base(self, urlopen):
        urlopen.side_effect = [api_response("Häusern"), api_response("Haus")]
        result = lookup_dictionary("Häusern", "Häusern")
        self.assertEqual((result.status, result.lemma, result.article), (FOUND, "Haus", "das"))
        self.assertTrue(
            DictionaryEntry.objects.filter(headword="Häusern", base_form="Haus").exists()
        )

    @patch("video_study.dictionary.urlopen")
    def test_missing_candidate_falls_back_to_the_surface_form(self, urlopen):
        urlopen.side_effect = [MISSING_RESPONSE, api_response("gegangen"), api_response("gehen")]
        result = lookup_dictionary("gegangenen", "gegangen")
        self.assertEqual((result.status, result.lemma), (FOUND, "gehen"))
        self.assertEqual(DictionaryEntry.objects.get(headword="gegangenen").status, MISSING)

    @patch("video_study.dictionary.urlopen", return_value=MISSING_RESPONSE)
    def test_missing_page_is_cached_as_missing(self, urlopen):
        self.assertEqual(lookup_dictionary("Quatschwort", "Quatschwort").status, MISSING)
        self.assertEqual(lookup_dictionary("Quatschwort", "Quatschwort").status, MISSING)
        self.assertEqual(urlopen.call_count, 1)

    @patch("video_study.dictionary.urlopen")
    def test_rate_limit_is_unavailable_and_not_cached(self, urlopen):
        urlopen.side_effect = HTTPError("u", 429, "Too Many Requests", {}, None)
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)
        self.assertFalse(DictionaryEntry.objects.exists())

    @patch("video_study.dictionary.urlopen", side_effect=URLError("offline"))
    def test_network_error_is_unavailable(self, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)

    @patch("video_study.dictionary.urlopen", side_effect=TimeoutError())
    def test_timeout_is_unavailable(self, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)

    @patch("video_study.dictionary.urlopen", return_value=_FakeResponse(b"not json"))
    def test_unreadable_json_is_unavailable(self, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)

    @patch("video_study.dictionary.urlopen", return_value=_FakeResponse(b"[]"))
    def test_malformed_envelope_is_unavailable_and_not_cached(self, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)
        self.assertFalse(DictionaryEntry.objects.exists())

    @patch("video_study.dictionary.urlopen")
    @patch("video_study.dictionary.time.monotonic", side_effect=[0, 100])
    def test_spent_budget_makes_no_request(self, monotonic, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)
        urlopen.assert_not_called()

    @override_settings(VIDEO_STUDY_DICTIONARY_ENDPOINT="")
    @patch("video_study.dictionary.urlopen")
    def test_empty_endpoint_disables_lookups(self, urlopen):
        self.assertEqual(lookup_dictionary("Haus", "Haus").status, UNAVAILABLE)
        urlopen.assert_not_called()

    def test_dictionary_unavailable_is_a_runtime_error(self):
        self.assertTrue(issubclass(DictionaryUnavailable, RuntimeError))
