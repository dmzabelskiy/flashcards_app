from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from reviews.models import ReviewLog
from reviews.ratings import Rating


@dataclass(frozen=True)
class ScheduleResult:
    previous_due_at: object
    new_due_at: object
    previous_interval_days: int
    new_interval_days: int
    previous_ease_factor: float
    new_ease_factor: float


def _clamp_ease(value):
    return max(1.3, round(value, 2))


def _next_interval(card, rating):
    if card.review_count == 0 or card.interval_days == 0:
        return {
            Rating.AGAIN: 1,
            Rating.HARD: 1,
            Rating.GOOD: 3,
            Rating.EASY: 5,
        }[rating]

    if rating == Rating.AGAIN:
        return 1
    if rating == Rating.HARD:
        return max(1, round(card.interval_days * 1.2))
    if rating == Rating.GOOD:
        return max(card.interval_days + 1, round(card.interval_days * card.ease_factor))
    if rating == Rating.EASY:
        return max(card.interval_days + 2, round(card.interval_days * (card.ease_factor + 0.35)))
    raise ValueError(f"Unknown rating: {rating}")


def _next_ease(card, rating):
    if rating == Rating.AGAIN:
        return _clamp_ease(card.ease_factor - 0.25)
    if rating == Rating.HARD:
        return _clamp_ease(card.ease_factor - 0.15)
    if rating == Rating.EASY:
        return _clamp_ease(card.ease_factor + 0.15)
    return _clamp_ease(card.ease_factor)


SCHEDULING_FIELDS = [
    "interval_days",
    "ease_factor",
    "review_count",
    "lapse_count",
    "due_at",
    "correct_streak",
]


def schedule_card(card, rating, elapsed_seconds=None, submission_id=None):
    """Apply a rating to ``card`` and log it, committing both or neither.

    The scheduling fields are re-read inside the transaction (SQLite's
    IMMEDIATE mode already holds the write lock by then), so a card object
    loaded before another review does not get its interval computed from
    stale values. ``card`` is updated in place.
    """
    rating = Rating(rating)
    with transaction.atomic():
        card.refresh_from_db(fields=SCHEDULING_FIELDS)
        reviewed_at = timezone.now()
        previous_due_at = card.due_at
        previous_interval = card.interval_days
        previous_ease = card.ease_factor
        new_interval = _next_interval(card, rating)
        new_ease = _next_ease(card, rating)
        new_due_at = reviewed_at + timezone.timedelta(days=new_interval)

        card.interval_days = new_interval
        card.ease_factor = new_ease
        card.review_count += 1
        if rating == Rating.AGAIN:
            card.lapse_count += 1
        if card.deck.is_global:
            card.correct_streak = 0 if rating == Rating.AGAIN else card.correct_streak + 1
        card.due_at = new_due_at
        update_fields = [
            "interval_days",
            "ease_factor",
            "review_count",
            "lapse_count",
            "due_at",
            "updated_at",
        ]
        if card.deck.is_global:
            update_fields.append("correct_streak")
        card.save(update_fields=update_fields)

        ReviewLog.objects.create(
            card=card,
            rating=rating,
            reviewed_at=reviewed_at,
            previous_due_at=previous_due_at,
            new_due_at=new_due_at,
            previous_interval_days=previous_interval,
            new_interval_days=new_interval,
            elapsed_seconds=elapsed_seconds,
            submission_id=submission_id,
        )

    return ScheduleResult(
        previous_due_at=previous_due_at,
        new_due_at=new_due_at,
        previous_interval_days=previous_interval,
        new_interval_days=new_interval,
        previous_ease_factor=previous_ease,
        new_ease_factor=new_ease,
    )
