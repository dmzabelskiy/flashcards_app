"""Caption routing tests use a mocked transport, never live providers."""

import json
import runpy
import traceback
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from requests import Response, Session
from requests.exceptions import ConnectTimeout, ProxyError, ReadTimeout
from youtube_transcript_api._errors import IpBlocked, RequestBlocked

from video_study import services
from video_study.services import TranscriptUnavailable, fetch_english_captions, fetch_transcript

VIDEO_ID = "abcdefghijk"
PROXY = "https://operator:private%40password@proxy.example:8443"
WATCH_HTML = '"INNERTUBE_API_KEY": "fakekey"'
CAPTIONS = {
    "playabilityStatus": {"status": "OK"},
    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "https://www.youtube.com/captions",
                    "name": {"runs": [{"text": "Dutch"}]},
                    "languageCode": "nl",
                    "isTranslatable": True,
                }
            ],
            "translationLanguages": [
                {"languageCode": "en", "languageName": {"runs": [{"text": "English"}]}}
            ],
        }
    },
}
CAPTION_XML = '<transcript><text start="0" dur="2">Hello</text></transcript>'


def response(body, status=200):
    result = Response()
    result.status_code = status
    result._content = body.encode()
    return result


@override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL="", VIDEO_STUDY_YOUTUBE_TIMEOUT=15)
class CaptionClientTests(SimpleTestCase):
    def setUp(self):
        self.close = patch.object(services._CaptionSession, "close", autospec=True).start()
        self.addCleanup(patch.stopall)
        self.transport = patch.object(Session, "send", autospec=True).start()
        self.transport.side_effect = [
            response(WATCH_HTML),
            response(json.dumps(CAPTIONS)),
            response(CAPTION_XML),
        ]

    def assert_workflow(self, proxy, timeout):
        calls = self.transport.call_args_list
        self.assertEqual([call.args[1].method for call in calls], ["GET", "POST", "GET"])
        session = calls[0].args[0]
        self.assertTrue(all(call.args[0] is session for call in calls))
        self.assertFalse(session.trust_env)
        for call in calls:
            self.assertEqual(call.kwargs["timeout"], timeout)
            self.assertEqual(call.kwargs["proxies"], proxy)
        for adapter in session.adapters.values():
            self.assertEqual(adapter.max_retries.total, 0)
        self.close.assert_called_once_with(session)

    @patch.dict("os.environ", {"HTTPS_PROXY": "http://ambient.invalid:9000", "NO_PROXY": "*"})
    def test_direct_workflow_ignores_ambient_proxy(self):
        self.assertEqual(fetch_transcript(VIDEO_ID, ("nl",))[0]["text"], "Hello")
        self.assert_workflow({}, (5.0, 15.0))

    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY, VIDEO_STUDY_YOUTUBE_TIMEOUT=2.5)
    @patch.dict("os.environ", {"HTTPS_PROXY": "http://ambient.invalid:9000", "NO_PROXY": "*"})
    def test_primary_uses_explicit_proxy_and_timeout_on_get_and_post(self):
        fetch_transcript(VIDEO_ID, ("nl",))
        self.assert_workflow({"http": PROXY, "https": PROXY}, (2.5, 2.5))

    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY)
    def test_translated_english_keeps_same_session_until_fetch(self):
        result = fetch_english_captions(VIDEO_ID)
        self.assertEqual(result["caption_origin"], "translated")
        self.assertIn("tlang=en", self.transport.call_args.args[1].url)
        self.assert_workflow({"http": PROXY, "https": PROXY}, (5.0, 15.0))

    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY)
    def test_native_english_uses_same_proxy(self):
        data = json.loads(json.dumps(CAPTIONS))
        data["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0]["languageCode"] = (
            "en"
        )
        self.transport.side_effect = [
            response(WATCH_HTML),
            response(json.dumps(data)),
            response(CAPTION_XML),
        ]
        self.assertEqual(fetch_english_captions(VIDEO_ID)["caption_origin"], "manual")
        self.assert_workflow({"http": PROXY, "https": PROXY}, (5.0, 15.0))

    def test_fresh_session_for_each_workflow(self):
        self.transport.side_effect = [
            response(WATCH_HTML),
            response(json.dumps(CAPTIONS)),
            response(CAPTION_XML),
        ] * 2
        fetch_transcript(VIDEO_ID, ("nl",))
        fetch_transcript(VIDEO_ID, ("nl",))
        self.assertIsNot(
            self.transport.call_args_list[0].args[0], self.transport.call_args_list[3].args[0]
        )
        self.assertEqual(self.close.call_count, 2)

    def test_session_stays_open_for_list_translation_and_fetch(self):
        dutch = Mock(
            language_code="nl",
            is_generated=False,
            is_translatable=True,
            translation_languages=[{"language_code": "en"}],
        )
        english = Mock(language_code="en")

        def list_tracks(_video_id):
            self.close.assert_not_called()
            return [dutch]

        def translate(_code):
            self.close.assert_not_called()
            return english

        def fetch():
            self.close.assert_not_called()
            return Mock(to_raw_data=lambda: [{"text": "Hello", "start": 0, "duration": 2}])

        dutch.translate.side_effect = translate
        english.fetch.side_effect = fetch
        with patch("youtube_transcript_api.YouTubeTranscriptApi") as api:
            api.return_value.list.side_effect = list_tracks
            fetch_english_captions(VIDEO_ID)
        self.close.assert_called_once()

    def test_cleanup_on_no_matching_track(self):
        with self.assertRaisesMessage(TranscriptUnavailable, "No German captions"):
            fetch_transcript(VIDEO_ID)
        self.close.assert_called_once()

    def test_cleanup_on_client_construction_failure(self):
        with patch("youtube_transcript_api.YouTubeTranscriptApi", side_effect=RuntimeError(PROXY)):
            with self.assertRaises(TranscriptUnavailable):
                fetch_transcript(VIDEO_ID)
        self.close.assert_called_once()

    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY)
    def test_transport_failures_are_sanitized_and_never_retried(self):
        for stage in range(3):
            for error, message in [
                (ProxyError(PROXY), "proxy could not be reached"),
                (ConnectTimeout(PROXY), "timed out"),
                (ReadTimeout(PROXY), "timed out"),
                (RequestBlocked(VIDEO_ID), "YouTube blocked"),
                (IpBlocked(VIDEO_ID), "YouTube blocked"),
                (RuntimeError(PROXY), "could not be loaded"),
            ]:
                with self.subTest(stage=stage, error=type(error).__name__):
                    self.transport.reset_mock()
                    self.close.reset_mock()
                    self.transport.side_effect = [
                        response(WATCH_HTML),
                        response(json.dumps(CAPTIONS)),
                        response(CAPTION_XML),
                    ][:stage] + [error]
                    with self.assertLogs("video_study.services", level="WARNING") as logs:
                        with self.assertRaisesMessage(TranscriptUnavailable, message) as caught:
                            fetch_transcript(VIDEO_ID, ("nl",))
                    self.assertEqual(self.transport.call_count, stage + 1)
                    self.close.assert_called_once()
                    public_trace = "".join(traceback.format_exception(caught.exception))
                    self.assertNotIn(PROXY, public_trace + "".join(logs.output))
                    self.assertNotIn("private", public_trace + "".join(logs.output))
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertTrue(caught.exception.__suppress_context__)
                    self.assertIn(VIDEO_ID, logs.output[0])
                    self.assertIn(type(error).__name__, logs.output[0])
                    self.assertTrue(all(record.exc_info is None for record in logs.records))

    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY)
    def test_http_429_does_not_trigger_library_or_transport_retries(self):
        self.transport.side_effect = [response("blocked", 429)]
        with self.assertRaisesMessage(TranscriptUnavailable, "YouTube blocked"):
            fetch_transcript(VIDEO_ID)
        self.transport.assert_called_once()
        self.close.assert_called_once()

    def test_invalid_proxy_is_safe_and_never_falls_back_to_direct(self):
        for value in [
            "socks5://operator:private@proxy.example:1234",
            "http://",
            "http://proxy:wrong",
            "http://proxy:65536",
            "http://proxy:0",
            "http://proxy:",
            "http://[broken",
            "http://operator:private@proxy/path",
            "http://proxy?password=private",
            "http://proxy#private",
            "http://operator:private@@proxy",
            "http://proxy bad",
            "http://operator:private%xx@proxy",
            "http://operator@proxy",
            "http://!invalid",
        ]:
            with self.subTest(value=value), override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=value):
                with self.assertRaisesMessage(
                    TranscriptUnavailable, "configuration is invalid"
                ) as caught:
                    fetch_transcript(VIDEO_ID)
                self.assertNotIn(value, "".join(traceback.format_exception(caught.exception)))
        self.transport.assert_not_called()
        self.close.assert_not_called()

    def test_invalid_configuration_log_is_actionable_and_redacted(self):
        with override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL="http://operator:private@proxy/path"):
            with self.assertLogs("video_study.services", level="WARNING") as logs:
                with self.assertRaises(TranscriptUnavailable):
                    fetch_transcript(VIDEO_ID)
        self.assertEqual(
            logs.records[0].getMessage(), "YouTube caption proxy configuration is invalid"
        )
        self.assertNotIn("private", "".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        self.transport.assert_not_called()

    def test_supported_proxy_transports(self):
        for value in [
            "http://operator:private%40pass@proxy.example:80",
            PROXY,
            "http://[::1]:8080",
        ]:
            with self.subTest(value=value), override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=value):
                proxy = services._caption_proxy_config()
                self.assertEqual(proxy.to_requests_dict(), {"http": value, "https": value})
                self.assertEqual(proxy.retries_when_blocked, 0)


class CaptionSettingsTests(SimpleTestCase):
    def test_invalid_timeout_and_cap_fail_without_echoing_configuration(self):
        for name, values in {
            "VIDEO_STUDY_YOUTUBE_TIMEOUT": ["0", "-1", "nan", "inf", "private-password"],
            "VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS": ["-1", "1.5", "private-password"],
        }.items():
            for value in values:
                with self.subTest(name=name, value=value), patch.dict("os.environ", {name: value}):
                    with self.assertRaises(ImproperlyConfigured) as caught:
                        runpy.run_path(str(settings.BASE_DIR / "config/settings.py"))
                    self.assertIn(name, str(caught.exception))
                    self.assertNotIn(
                        "private-password", "".join(traceback.format_exception(caught.exception))
                    )

    def test_defaults_and_valid_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            config = runpy.run_path(str(settings.BASE_DIR / "config/settings.py"))
        self.assertEqual(config["VIDEO_STUDY_YOUTUBE_PROXY_URL"], "")
        self.assertEqual(config["VIDEO_STUDY_YOUTUBE_TIMEOUT"], 15)
        self.assertEqual(config["VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS"], 3)
        with patch.dict(
            "os.environ",
            {
                "VIDEO_STUDY_YOUTUBE_TIMEOUT": "0.25",
                "VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS": "0",
            },
        ):
            config = runpy.run_path(str(settings.BASE_DIR / "config/settings.py"))
        self.assertEqual(config["VIDEO_STUDY_YOUTUBE_TIMEOUT"], 0.25)
        self.assertEqual(config["VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS"], 0)
