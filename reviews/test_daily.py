from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from reviews.daily import DAILY_TARGET, get_daily_task, record_daily_study_result
from reviews.models import DailyStudyProgress
from reviews.testutils import submit_typed_result


class DailyTaskServiceTests(TestCase):
    def setUp(self):
        self.owner = create_user()

    def test_get_daily_task_uses_latest_active_deck(self):
        older = Deck.objects.create(name="Older", owner=self.owner)
        latest = Deck.objects.create(name="Latest", owner=self.owner)
        Deck.objects.create(name="Archived", is_archived=True, owner=self.owner)
        Card.objects.create(deck=older, front="alt", back="old")
        Card.objects.create(deck=latest, front="neu", back="new")

        task = get_daily_task()

        self.assertEqual(task.deck, latest)
        self.assertEqual(task.target_count, DAILY_TARGET)
        self.assertEqual(task.correct_count, 0)
        self.assertFalse(task.is_done)

    def test_get_daily_task_filters_by_language_mode(self):
        Deck.objects.create(name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.owner)
        es_deck = Deck.objects.create(
            name="ES", language_mode=Deck.LanguageMode.DE_ES, owner=self.owner
        )

        task = get_daily_task(language_mode=Deck.LanguageMode.DE_ES)

        self.assertEqual(task.deck, es_deck)

    def test_record_daily_study_result_counts_only_correct_answers(self):
        deck = Deck.objects.create(name="German", owner=self.owner)

        record_daily_study_result(deck, correct=True)
        record_daily_study_result(deck, correct=False)

        progress = DailyStudyProgress.objects.get(deck=deck, date=timezone.localdate())
        self.assertEqual(progress.correct_count, 1)

    def test_daily_task_is_done_at_twenty_correct_answers(self):
        deck = Deck.objects.create(name="German", owner=self.owner)
        for _index in range(DAILY_TARGET):
            record_daily_study_result(deck, correct=True)

        task = get_daily_task()

        self.assertEqual(task.correct_count, DAILY_TARGET)
        self.assertTrue(task.is_done)


class DailyTaskStudyIntegrationTests(AuthedTestCase):
    def test_correct_study_submission_increments_daily_progress(self):
        deck = Deck.objects.create(name="German", owner=self.user)
        Card.objects.create(deck=deck, front="der Tisch", back="table")
        self.client.get(reverse("reviews:start_study", args=[deck.pk]))

        submit_typed_result(self.client, answer="table")

        progress = DailyStudyProgress.objects.get(deck=deck, date=timezone.localdate())
        self.assertEqual(progress.correct_count, 1)
