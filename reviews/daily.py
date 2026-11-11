from dataclasses import dataclass

from django.db.models import F
from django.utils import timezone

from core.language_modes import filter_decks_for_mode
from decks.models import Deck
from reviews.models import DailyStudyProgress

DAILY_TARGET = 20


@dataclass(frozen=True)
class DailyTask:
    deck: Deck | None
    date: object
    correct_count: int
    target_count: int = DAILY_TARGET

    @property
    def remaining_count(self):
        return max(0, self.target_count - self.correct_count)

    @property
    def is_done(self):
        return self.correct_count >= self.target_count


def latest_active_deck(language_mode=None, user=None):
    queryset = Deck.objects.filter(is_archived=False)
    if user is not None:
        queryset = queryset.filter(owner=user)
    if language_mode:
        queryset = filter_decks_for_mode(queryset, language_mode)
    return queryset.order_by("-created_at", "-id").first()


def get_daily_task(date=None, language_mode=None, user=None):
    date = date or timezone.localdate()
    deck = latest_active_deck(language_mode=language_mode, user=user)
    if not deck:
        return DailyTask(deck=None, date=date, correct_count=0)
    progress = DailyStudyProgress.objects.filter(deck=deck, date=date).first()
    return DailyTask(deck=deck, date=date, correct_count=progress.correct_count if progress else 0)


def daily_correct_count(deck, date=None):
    progress = DailyStudyProgress.objects.filter(deck=deck, date=date or timezone.localdate())
    return progress.values_list("correct_count", flat=True).first() or 0


def record_daily_study_result(deck, correct, date=None):
    if not correct or deck.is_archived:
        return None
    date = date or timezone.localdate()
    progress, created = DailyStudyProgress.objects.get_or_create(deck=deck, date=date)
    DailyStudyProgress.objects.filter(pk=progress.pk).update(correct_count=F("correct_count") + 1)
    progress.refresh_from_db()
    return progress
