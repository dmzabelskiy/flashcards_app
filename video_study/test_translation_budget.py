import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from django.test import SimpleTestCase, override_settings

from video_study.services import (
    TranslationRateLimited,
    TranslationUnavailable,
    _read_translation_response,
    _request_deepl_translation,
    request_translation,
    request_translation_detailed,
)


@override_settings(
    VIDEO_STUDY_TRANSLATION_PROVIDERS=["deepl", "mymemory"],
    VIDEO_STUDY_TRANSLATION_BUDGET=5,
)
class TranslationBudgetTests(SimpleTestCase):
    @patch("video_study.services._request_mymemory_translation")
    @patch(
        "video_study.services._request_deepl_translation",
        side_effect=TranslationUnavailable("deepl unavailable"),
    )
    @patch("video_study.services.time.monotonic")
    def test_stops_calling_providers_once_the_budget_is_spent(self, monotonic, deepl, mymemory):
        # start=0, remaining check before deepl=0 (budget still 5), remaining
        # check before mymemory=10 (budget of 5 is long spent).
        monotonic.side_effect = [0, 0, 10]

        with self.assertRaises(TranslationUnavailable):
            request_translation("Wüste", "de", "ru")

        deepl.assert_called_once()
        mymemory.assert_not_called()

    @patch(
        "video_study.services._request_mymemory_translation",
        return_value="отказ",
    )
    @patch(
        "video_study.services._request_deepl_translation",
        side_effect=TranslationUnavailable("deepl unavailable"),
    )
    @patch("video_study.services.time.monotonic")
    def test_calls_the_next_provider_while_budget_remains(self, monotonic, deepl, mymemory):
        # start=0, remaining check before deepl=0 (budget still 5), remaining
        # check before mymemory=1 (still 4 seconds left).
        monotonic.side_effect = [0, 0, 1]

        translated = request_translation("Wüste", "de", "ru")

        self.assertEqual(translated, "отказ")
        deepl.assert_called_once()
        mymemory.assert_called_once()


@override_settings(
    VIDEO_STUDY_TRANSLATION_PROVIDERS=["mymemory"],
    VIDEO_STUDY_TRANSLATION_TIMEOUT=8,
    VIDEO_STUDY_TRANSLATION_BUDGET=3,
)
class TranslationBudgetReachesHttpTimeoutTests(SimpleTestCase):
    @patch("video_study.services.urlopen")
    @patch("video_study.services.time.monotonic")
    def test_budget_caps_the_timeout_passed_to_the_http_call(self, monotonic, urlopen):
        # start=0, remaining check before mymemory=1 (about 1 second already
        # spent, so remaining = budget(3) - 1 = 2, well under the
        # configured 8 second provider timeout).
        monotonic.side_effect = [0, 1]

        response_body = json.dumps(
            {
                "responseData": {"translatedText": "отказ"},
                "matches": [
                    {
                        "segment": "Wüste",
                        "translation": "отказ",
                        "match": 1.0,
                        "quality": 90,
                    }
                ],
            }
        ).encode("utf-8")
        response = MagicMock()
        response.read.return_value = response_body
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        urlopen.return_value = response

        translated = request_translation("Wüste", "de", "ru")

        self.assertEqual(translated, "отказ")
        urlopen.assert_called_once()
        _, kwargs = urlopen.call_args
        self.assertAlmostEqual(kwargs["timeout"], 2, delta=0.1)


class ReadTranslationResponseRateLimitTests(SimpleTestCase):
    @patch("video_study.services.urlopen")
    def test_http_429_raises_translation_rate_limited(self, urlopen):
        urlopen.side_effect = HTTPError("http://example.test", 429, "Too Many Requests", {}, None)

        with self.assertRaises(TranslationRateLimited):
            _read_translation_response(Request("http://example.test"), "wiktionary")

    @patch("video_study.services.urlopen")
    def test_other_http_errors_raise_plain_translation_unavailable(self, urlopen):
        urlopen.side_effect = HTTPError("http://example.test", 500, "Server Error", {}, None)

        with self.assertRaises(TranslationUnavailable) as caught:
            _read_translation_response(Request("http://example.test"), "wiktionary")
        self.assertNotIsInstance(caught.exception, TranslationRateLimited)


@override_settings(VIDEO_STUDY_TRANSLATION_PROVIDERS=["wiktionary", "mymemory"])
class RequestTranslationDetailedTests(SimpleTestCase):
    @patch("video_study.services._request_mymemory_translation", return_value="полка")
    @patch(
        "video_study.services._request_wiktionary_translation",
        side_effect=TranslationRateLimited("rate limited"),
    )
    def test_reports_rate_limited_when_a_skipped_provider_was_throttled(self, wiktionary, mymemory):
        translated, provider, rate_limited = request_translation_detailed("Fach", "de", "ru")

        self.assertEqual(translated, "полка")
        self.assertEqual(provider, "mymemory")
        self.assertTrue(rate_limited)

    @patch("video_study.services._request_mymemory_translation", return_value="")
    @patch("video_study.services._request_wiktionary_translation", return_value="предмет")
    def test_reports_the_winning_provider_without_rate_limiting(self, wiktionary, mymemory):
        translated, provider, rate_limited = request_translation_detailed("Fach", "de", "ru")

        self.assertEqual(translated, "предмет")
        self.assertEqual(provider, "wiktionary")
        self.assertFalse(rate_limited)

    @patch(
        "video_study.services._request_mymemory_translation",
        side_effect=TranslationRateLimited("rate limited"),
    )
    @patch(
        "video_study.services._request_wiktionary_translation",
        side_effect=TranslationRateLimited("rate limited"),
    )
    def test_request_translation_wrapper_still_returns_only_text(self, wiktionary, mymemory):
        with self.assertRaises(TranslationUnavailable):
            request_translation("Fach", "de", "ru")


@override_settings(
    VIDEO_STUDY_TRANSLATION_PROVIDERS=["wiktionary", "deepl"], VIDEO_STUDY_DEEPL_API_KEY="k"
)
class EnglishTargetTests(SimpleTestCase):
    @patch("video_study.services._request_wiktionary_translation")
    @patch("video_study.services._request_deepl_translation", return_value="house")
    def test_english_target_skips_russian_wiktionary(self, deepl, wiktionary):
        text, provider, _ = request_translation_detailed(
            "huis", source_language="nl", target_language="en"
        )
        self.assertEqual((text, provider), ("house", "deepl"))
        wiktionary.assert_not_called()

    @patch(
        "video_study.services._read_translation_response",
        return_value={"translations": [{"text": "house"}]},
    )
    def test_deepl_receives_en_gb(self, read):
        _request_deepl_translation("huis", "nl", "en")
        body = json.loads(read.call_args.args[0].data)
        self.assertEqual((body["source_lang"], body["target_lang"]), ("NL", "EN-GB"))
