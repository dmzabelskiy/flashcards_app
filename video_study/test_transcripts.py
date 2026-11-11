from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from video_study.services import TranscriptUnavailable, fetch_transcript


class FakeTrack:
    def __init__(self, language_code, is_generated, text):
        self.language_code = language_code
        self.is_generated = is_generated
        self._text = text

    def fetch(self):
        fetched = MagicMock()
        fetched.to_raw_data.return_value = [{"text": self._text, "start": 0.0, "duration": 1.0}]
        return fetched


class RequestBlocked(Exception):
    pass


class TranscriptRegionalMatchingTests(SimpleTestCase):
    def _mock_tracks(self, api_class, tracks):
        api_class.return_value.list.return_value = tracks

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_regional_manual_track_is_picked_for_base_language(self, api_class):
        self._mock_tracks(
            api_class,
            [
                FakeTrack("en", is_generated=False, text="hello"),
                FakeTrack("nl-NL", is_generated=False, text="hallo"),
            ],
        )

        cues = fetch_transcript("mOpU9Wf-wg1", languages=("nl",))

        self.assertEqual(cues[0]["text"], "hallo")

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_exact_manual_track_is_preferred_over_regional_manual(self, api_class):
        self._mock_tracks(
            api_class,
            [
                FakeTrack("nl-BE", is_generated=False, text="Belgian"),
                FakeTrack("nl", is_generated=False, text="exact"),
            ],
        )

        cues = fetch_transcript("aaaaaaaaaaa", languages=("nl",))

        self.assertEqual(cues[0]["text"], "exact")

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_regional_manual_track_is_preferred_over_exact_generated(self, api_class):
        self._mock_tracks(
            api_class,
            [
                FakeTrack("nl", is_generated=True, text="generated exact"),
                FakeTrack("nl-NL", is_generated=False, text="manual regional"),
            ],
        )

        cues = fetch_transcript("bbbbbbbbbbb", languages=("nl",))

        self.assertEqual(cues[0]["text"], "manual regional")

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_base_code_does_not_match_unrelated_or_lookalike_codes(self, api_class):
        self._mock_tracks(
            api_class,
            [
                FakeTrack("en", is_generated=False, text="hello"),
                FakeTrack("nld", is_generated=False, text="lookalike"),
            ],
        )

        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript("ccccccccccc", languages=("nl",))

        self.assertEqual(str(ctx.exception), "No Dutch captions are available for this video.")

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_no_matching_track_raises_transcript_unavailable(self, api_class):
        self._mock_tracks(api_class, [FakeTrack("fr", is_generated=False, text="bonjour")])

        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript("ddddddddddd", languages=("de",))

        self.assertEqual(str(ctx.exception), "No German captions are available for this video.")

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_list_exception_maps_to_blocked_message(self, api_class):
        api_class.return_value.list.side_effect = RequestBlocked("blocked")

        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript("eeeeeeeeeee", languages=("de",))

        self.assertEqual(
            str(ctx.exception),
            "YouTube blocked transcript requests from this connection.",
        )

    @patch("youtube_transcript_api.YouTubeTranscriptApi")
    def test_track_fetch_exception_maps_to_blocked_message(self, api_class):
        track = FakeTrack("de", is_generated=False, text="hallo")
        track.fetch = MagicMock(side_effect=RequestBlocked("blocked"))
        self._mock_tracks(api_class, [track])

        with self.assertRaises(TranscriptUnavailable) as ctx:
            fetch_transcript("fffffffffff", languages=("de",))

        self.assertEqual(
            str(ctx.exception),
            "YouTube blocked transcript requests from this connection.",
        )
