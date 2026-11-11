"""One transaction per answered card.

Both study flows (typed study and quick review) go through ``record_review``:
the fresh card read, the schedule, the review log, the trainer attempt, the
daily progress, and an optional retirement commit together or not at all.
SQLite takes the write lock when the outer transaction begins (the database
is configured with ``transaction_mode: IMMEDIATE``), so the card is read after
any competing writer has finished; ``select_for_update`` is a no-op here.
"""

from dataclasses import dataclass

from django.db import transaction

from cards.models import Card
from core.language_modes import filter_cards_for_mode
from reviews.analytics import record_trainer_attempt
from reviews.daily import record_daily_study_result
from reviews.models import ReviewLog
from reviews.ratings import Rating
from reviews.scheduler import ScheduleResult, schedule_card

# A response slower than a day is a forgotten tab, not a measurement, and the
# bound keeps the value inside SQLite's integer range.
MAX_ELAPSED_SECONDS = 24 * 60 * 60


class ReviewRejected(Exception):
    """The answer was not recorded and nothing was written."""


class CardUnavailable(ReviewRejected):
    """The card is not the user's, is inactive, or belongs to another mode."""


class DuplicateSubmission(ReviewRejected):
    """This rendered form was already submitted."""


class StaleSubmission(ReviewRejected):
    """The card was reviewed since the form was rendered."""


@dataclass(frozen=True)
class ReviewOutcome:
    card: Card
    schedule: ScheduleResult
    correct: bool
    retired: bool


def parse_elapsed_seconds(raw):
    """Return whole seconds, ``None`` when absent; raise ``ValueError`` if invalid."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    if not (text.isascii() and text.isdigit()):
        raise ValueError("Elapsed time must be a whole number of seconds.")
    seconds = int(text)
    if seconds > MAX_ELAPSED_SECONDS:
        raise ValueError("Elapsed time is out of range.")
    return seconds


def record_review(
    *,
    user,
    card_id,
    language_mode,
    rating,
    trainer,
    submission_id=None,
    expected_review_count=None,
    elapsed_seconds=None,
    retire=False,
    record_daily=False,
):
    rating = Rating(rating)
    correct = rating != Rating.AGAIN
    with transaction.atomic():
        if submission_id and ReviewLog.objects.filter(submission_id=submission_id).exists():
            raise DuplicateSubmission(submission_id)
        card = (
            filter_cards_for_mode(Card.objects.for_user(user).active(), language_mode)
            .select_related("deck")
            .filter(pk=card_id)
            .first()
        )
        if card is None:
            raise CardUnavailable(card_id)
        if expected_review_count is not None and card.review_count != expected_review_count:
            raise StaleSubmission(card_id)
        result = schedule_card(
            card, rating, elapsed_seconds=elapsed_seconds, submission_id=submission_id
        )
        record_trainer_attempt(
            user=user,
            trainer=trainer,
            language_mode=language_mode,
            is_correct=correct,
            card=card,
            item_label=card.front,
            response_ms=elapsed_seconds * 1000 if elapsed_seconds is not None else None,
        )
        if record_daily:
            record_daily_study_result(card.deck, correct)
        retired = bool(retire) and card.retire()
    return ReviewOutcome(card=card, schedule=result, correct=correct, retired=retired)
