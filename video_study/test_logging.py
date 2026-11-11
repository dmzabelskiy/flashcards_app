from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.test import SimpleTestCase, override_settings

from video_study.services import (
    TranscriptUnavailable,
    TranslationUnavailable,
    fetch_transcript,
    request_translation,
)


@override_settings(VIDEO_STUDY_TRANSLATION_PROVIDERS=["mymemory"])
class TranslationProviderLoggingTests(SimpleTestCase):
    @patch("video_study.services.urlopen", side_effect=URLError("connection refused"))
    def test_provider_failure_logs_a_warning(self, urlopen):
        with self.assertLogs("video_study.services", level="WARNING"):
            with self.assertRaises(TranslationUnavailable):
                request_translation("Wüste", "de", "ru")


class TranscriptFetchLoggingTests(SimpleTestCase):
    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_empty_transcript_logs_a_warning(self, api_class):
        fetched = MagicMock()
        fetched.to_raw_data.return_value = []
        track = MagicMock(language_code="de", is_generated=False)
        track.fetch.return_value = fetched
        api_class.return_value.list.return_value = [track]

        with self.assertLogs("video_study.services", level="WARNING") as logs:
            with self.assertRaises(TranscriptUnavailable):
                fetch_transcript("abcdefghijk")

        self.assertIn("abcdefghijk", logs.output[0])
