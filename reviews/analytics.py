from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.activity import build_activity_calendar
from core.language_packs import pack_for_mode
from reviews.drills import drills_for_mode
from reviews.models import ReviewLog, TrainerAttempt
from reviews.ratings import Rating
from reviews.study_queue import STUDY_SESSION_LIMIT

# Labels for the trainers that are not drills (review, video, podcast), and the
# fallback for stored drill trainers; a drill's own label comes from its file.
TRAINER_LABELS = {
    TrainerAttempt.Trainer.TYPED_REVIEW: "Typed review",
    TrainerAttempt.Trainer.SPACED_REVIEW: "Quick review",
    TrainerAttempt.Trainer.ARTICLE: "Article trainer",
    TrainerAttempt.Trainer.ADJECTIVE_PREPOSITION: "Adjective + preposition",
    TrainerAttempt.Trainer.VERB_PREPOSITION: "Verb + preposition",
    TrainerAttempt.Trainer.IRREGULAR_VERB: "Irregular verbs",
    TrainerAttempt.Trainer.DEUTSCH_ONLINE: "Deutsch-online",
    TrainerAttempt.Trainer.VIDEO: "Video study",
    TrainerAttempt.Trainer.PODCAST: "Podcast study",
    TrainerAttempt.Trainer.GRAMMAR: "Grammar book",
    TrainerAttempt.Trainer.TELC_B2: "telc Deutsch B2",
}


@dataclass(frozen=True)
class AnalyticsWindow:
    days: int
    start: object
    previous_start: object
    now: object


def get_analytics_window(days=7):
    days = int(days) if str(days).isdigit() else 7
    if days not in {7, 30, 90}:
        days = 7
    now = timezone.now()
    start = now - timezone.timedelta(days=days)
    return AnalyticsWindow(
        days=days,
        start=start,
        previous_start=start - timezone.timedelta(days=days),
        now=now,
    )


def record_trainer_attempt(
    *,
    user,
    trainer,
    language_mode,
    is_correct=None,
    card=None,
    item_label="",
    response_ms=None,
    occurred_at=None,
):
    return TrainerAttempt.objects.create(
        owner=user,
        trainer=trainer,
        language_mode=language_mode,
        is_correct=is_correct,
        card=card,
        item_label=item_label[:240],
        response_ms=response_ms,
        occurred_at=occurred_at or timezone.now(),
    )


def record_trainer_session(request, trainer, language_mode, item_label=""):
    """Record one opened media-learning session per trainer/item/day."""
    today = timezone.localdate().isoformat()
    key = f"{today}:{trainer}:{item_label[:120]}"
    seen = request.session.get("analytics_sessions", [])
    if key in seen:
        return None
    record = record_trainer_attempt(
        user=request.user,
        trainer=trainer,
        language_mode=language_mode,
        item_label=item_label,
    )
    request.session["analytics_sessions"] = (seen + [key])[-100:]
    return record


def _correct_rating_filter():
    return ~Q(rating=Rating.AGAIN)


def _day_counts(review_logs, trainer_events):
    counts = defaultdict(int)
    for timestamp in review_logs.values_list("reviewed_at", flat=True):
        counts[timezone.localtime(timestamp).date()] += 1
    for timestamp in trainer_events.exclude(
        trainer__in=[TrainerAttempt.Trainer.TYPED_REVIEW, TrainerAttempt.Trainer.SPACED_REVIEW]
    ).values_list("occurred_at", flat=True):
        counts[timezone.localtime(timestamp).date()] += 1
    return counts


def _activity_days(day_counts, window):
    today = timezone.localdate()
    days = []
    for offset in range(window.days - 1, -1, -1):
        day = today - timezone.timedelta(days=offset)
        days.append({"date": day, "count": day_counts.get(day, 0)})
    max_count = max((day["count"] for day in days), default=0) or 1
    for day in days:
        day["height"] = max(8, round(day["count"] / max_count * 100)) if day["count"] else 4
    return days


def _format_card(card):
    article = f"{card.article} " if card.article else ""
    return {
        "card": card,
        "label": f"{article}{card.front}",
        # Only an active card can be put in front of the learner for review.
        "is_active": not (card.is_suspended or card.is_retired or card.deck.is_archived),
        "accuracy": card.analytics_accuracy,
        "attempts": card.analytics_attempts,
        "last_reviewed": card.analytics_last_reviewed,
        "streak": card.correct_streak,
    }


def _score_words(card_queryset, window):
    """Every scored card, split into (best, attention) and fully sorted."""
    review_rows = (
        ReviewLog.objects.filter(
            card__in=card_queryset,
            reviewed_at__gte=window.start,
            reviewed_at__lte=window.now,
        )
        .values("card_id")
        .annotate(
            attempts=Count("id"),
            correct=Count("id", filter=_correct_rating_filter()),
            last_reviewed=Max("reviewed_at"),
        )
        .filter(attempts__gte=2)
    )
    rows = {row["card_id"]: row for row in review_rows}
    cards = card_queryset.filter(pk__in=rows).select_related("deck").order_by("front")
    best = []
    attention = []
    for card in cards:
        row = rows[card.pk]
        card.analytics_attempts = row["attempts"]
        card.analytics_accuracy = round(row["correct"] / row["attempts"] * 100)
        card.analytics_last_reviewed = row["last_reviewed"]
        target = best if card.analytics_accuracy >= 80 else attention
        target.append(_format_card(card))
    best.sort(key=lambda item: (-item["accuracy"], -item["attempts"], item["label"]))
    attention.sort(key=lambda item: (item["accuracy"], -item["attempts"], item["label"]))
    return best, attention


def weak_practice_cards(user, language_mode, period=7, limit=STUDY_SESSION_LIMIT):
    """The signed-in user's weakest active cards for ``language_mode``, worst first.

    Recomputed from review history on every call; callers pass no card ids.
    """
    window = get_analytics_window(period)
    active_cards = Card.objects.for_user(user).active().filter(deck__language_mode=language_mode)
    _best, attention = _score_words(active_cards, window)
    return [item["card"] for item in attention][:limit]


def _trainer_rows(language_mode):
    """(trainer, label, start_url) for each trainer shown: reviews, the mode's drills, media."""
    Trainer = TrainerAttempt.Trainer
    review_url = reverse("reviews:start")
    rows = [
        (trainer, TRAINER_LABELS[trainer], review_url)
        for trainer in (Trainer.TYPED_REVIEW, Trainer.SPACED_REVIEW)
    ]
    rows.extend(
        (drill.trainer, drill.label, reverse("reviews:drill_start", args=[drill.slug]))
        for drill in drills_for_mode(language_mode)
    )
    rows.append((Trainer.VIDEO, TRAINER_LABELS[Trainer.VIDEO], review_url))
    if "podcast" in pack_for_mode(language_mode).features:
        rows.append((Trainer.PODCAST, TRAINER_LABELS[Trainer.PODCAST], review_url))
    return rows


def _trainer_performance(user, language_mode, window):
    events = TrainerAttempt.objects.filter(
        owner=user,
        language_mode=language_mode,
        occurred_at__gte=window.start,
        occurred_at__lte=window.now,
    )
    performance = []
    for trainer, label, start_url in _trainer_rows(language_mode):
        # Each trainer row is sourced from its own TrainerAttempt rows. Typed and
        # spaced reviews both also write a ReviewLog, but ReviewLog carries no
        # trainer marker, so attributing that shared table here would count
        # typed reviews into the "Quick review" row. The headline review totals in
        # build_personal_analytics come straight from ReviewLog.
        trainer_events = events.filter(trainer=trainer)
        attempts = trainer_events.filter(is_correct__isnull=False).count()
        correct = trainer_events.filter(is_correct=True).count()
        sessions = trainer_events.filter(is_correct__isnull=True).count()
        if not attempts and not sessions:
            continue
        response_values = list(
            trainer_events.exclude(response_ms__isnull=True).values_list("response_ms", flat=True)
        )
        last_active = (
            trainer_events.order_by("-occurred_at").values_list("occurred_at", flat=True).first()
        )
        accuracy = round(correct / attempts * 100) if attempts else None
        performance.append(
            {
                "key": trainer,
                "label": label,
                "start_url": start_url,
                "attempts": attempts,
                "sessions": sessions,
                "correct": correct,
                "accuracy": accuracy,
                "average_response": round(sum(response_values) / len(response_values))
                if response_values
                else None,
                "last_active": last_active,
                "bar_width": accuracy or 4,
            }
        )
    return performance


def build_personal_analytics(user, language_mode, period=7):
    window = get_analytics_window(period)
    review_logs = ReviewLog.objects.filter(
        card__deck__owner=user,
        card__deck__language_mode=language_mode,
        reviewed_at__gte=window.previous_start,
        reviewed_at__lte=window.now,
    )
    trainer_events = TrainerAttempt.objects.filter(
        owner=user,
        language_mode=language_mode,
        occurred_at__gte=window.previous_start,
        occurred_at__lte=window.now,
    )
    current_reviews = review_logs.filter(reviewed_at__gte=window.start)
    previous_reviews = review_logs.filter(
        reviewed_at__gte=window.previous_start,
        reviewed_at__lt=window.start,
    )
    current_events = trainer_events.filter(occurred_at__gte=window.start)
    previous_events = trainer_events.filter(
        occurred_at__gte=window.previous_start,
        occurred_at__lt=window.start,
    )
    event_trainers = [TrainerAttempt.Trainer.TYPED_REVIEW, TrainerAttempt.Trainer.SPACED_REVIEW]
    current_non_review_events = current_events.exclude(trainer__in=event_trainers)
    previous_non_review_events = previous_events.exclude(trainer__in=event_trainers)
    current_attempts = (
        current_reviews.count() + current_non_review_events.filter(is_correct__isnull=False).count()
    )
    current_correct = (
        current_reviews.filter(_correct_rating_filter()).count()
        + current_non_review_events.filter(is_correct=True).count()
    )
    previous_attempts = (
        previous_reviews.count()
        + previous_non_review_events.filter(is_correct__isnull=False).count()
    )
    previous_correct = (
        previous_reviews.filter(_correct_rating_filter()).count()
        + previous_non_review_events.filter(is_correct=True).count()
    )
    day_counts = _day_counts(review_logs, trainer_events)
    activity = build_activity_calendar(day_counts, timezone.localdate())
    card_queryset = Card.objects.for_user(user).filter(deck__language_mode=language_mode)
    best_words, attention_words = _score_words(card_queryset, window)
    weak_practice_count = min(
        STUDY_SESSION_LIMIT, sum(1 for word in attention_words if word["is_active"])
    )
    best_words, attention_words = best_words[:5], attention_words[:5]
    trainers = _trainer_performance(user, language_mode, window)
    gradable = [
        trainer
        for trainer in trainers
        if trainer["attempts"] >= 3 and trainer["accuracy"] is not None
    ]
    focus = min(gradable, key=lambda trainer: trainer["accuracy"]) if gradable else None
    due_count = card_queryset.due().count()
    # Typed and quick review write a ReviewLog and a TrainerAttempt for the same
    # response, so use the ReviewLog while the card exists. Deletion cascades
    # its logs but retains its attempts with card=NULL, so their time is now
    # counted through those orphan events. Other trainers use their own events.
    timed_events = current_events.filter(~Q(trainer__in=event_trainers) | Q(card__isnull=True))
    active_seconds = sum(
        current_reviews.exclude(elapsed_seconds__isnull=True).values_list(
            "elapsed_seconds", flat=True
        )
    )
    active_seconds += (
        sum(timed_events.exclude(response_ms__isnull=True).values_list("response_ms", flat=True))
        / 1000
    )
    return {
        "period": window.days,
        "period_label": f"Last {window.days} days",
        "period_options": [7, 30, 90],
        "review_count": current_attempts,
        "review_change": current_attempts - previous_attempts,
        "accuracy": round(current_correct / current_attempts * 100, 1)
        if current_attempts
        else None,
        "accuracy_change": round(
            current_correct / current_attempts * 100 - previous_correct / previous_attempts * 100, 1
        )
        if current_attempts and previous_attempts
        else None,
        "active_minutes": round(active_seconds / 60),
        "streak": activity["current_streak"],
        "longest_streak": activity["longest_streak"],
        "daily_activity": _activity_days(day_counts, window),
        "trainers": trainers,
        "best_words": best_words,
        "attention_words": attention_words,
        "weak_practice_count": weak_practice_count,
        "activity": activity,
        "due_count": due_count,
        "focus_trainer": focus,
        "focus_word": attention_words[0] if attention_words else None,
    }
