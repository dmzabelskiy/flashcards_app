from unittest.mock import patch

from django.utils import timezone

from core.completion import mark_task_complete
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.analytics import record_trainer_attempt
from reviews.daily_plan import _media_item
from reviews.models import TrainerAttempt

Trainer = TrainerAttempt.Trainer


class MediaItemLanguageModeIsolationTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    @patch("reviews.daily_plan.get_channel_video_of_day", return_value=None)
    def test_german_completion_does_not_complete_the_dutch_media_row(self, _video):
        mark_task_complete(self.user, "media", Deck.LanguageMode.DE_RU)
        media = _media_item(self.user, Deck.LanguageMode.NL_EN, self.today)
        self.assertFalse(media["is_done"])

    @patch("reviews.daily_plan.get_channel_video_of_day", return_value=None)
    def test_dutch_completion_completes_the_dutch_media_row(self, _video):
        mark_task_complete(self.user, "media", Deck.LanguageMode.NL_EN)
        media = _media_item(self.user, Deck.LanguageMode.NL_EN, self.today)
        self.assertTrue(media["is_done"])

    @patch("reviews.daily_plan.get_podcast_of_day", return_value=None)
    @patch("reviews.daily_plan.get_kurzgesagt_video_of_day", return_value=None)
    def test_de_es_completion_does_not_complete_the_de_ru_media_row(self, _video, _podcast):
        mark_task_complete(self.user, "media", Deck.LanguageMode.DE_ES)
        media = _media_item(self.user, Deck.LanguageMode.DE_RU, self.today)
        self.assertFalse(media["is_done"])
        media = _media_item(self.user, Deck.LanguageMode.DE_ES, self.today)
        self.assertTrue(media["is_done"])

    @patch("reviews.daily_plan.get_podcast_of_day", return_value=None)
    @patch("reviews.daily_plan.get_kurzgesagt_video_of_day", return_value=None)
    def test_opening_media_records_activity_but_does_not_complete_the_row(self, _video, _podcast):
        record_trainer_attempt(
            user=self.user, trainer=Trainer.VIDEO, language_mode=Deck.LanguageMode.DE_RU
        )
        record_trainer_attempt(
            user=self.user, trainer=Trainer.PODCAST, language_mode=Deck.LanguageMode.DE_RU
        )
        media = _media_item(self.user, Deck.LanguageMode.DE_RU, self.today)
        self.assertFalse(media["is_done"])
