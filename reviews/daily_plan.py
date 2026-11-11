import datetime
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.completion import task_is_complete
from core.language_modes import get_language_mode
from core.language_packs import pack_for_mode
from core.rotation import rotating_choice
from decks.services import get_global_deck
from reviews.daily import DAILY_TARGET, daily_correct_count
from reviews.drills import drill_for_trainer, drills_for_mode
from reviews.models import TrainerAttempt
from reviews.study_queue import STUDY_SESSION_LIMIT
from video_study.services import (
    KURZGESAGT_DE,
    channel_for_mode,
    get_channel_video_of_day,
    get_kurzgesagt_video_of_day,
    get_podcast_of_day,
)


def available_drills(language_mode):
    """The drills the active pack offers for a language mode, in pack order."""
    return [
        {
            "trainer": drill.trainer,
            "slug": drill.slug,
            "label": drill.label,
            "start_url": reverse("reviews:drill_start", args=[drill.slug]),
            "description": drill.description,
            "pictogram": drill.pictogram,
        }
        for drill in drills_for_mode(language_mode)
    ]


def drill_of_the_day(language_mode, date=None):
    date = date or timezone.localdate()
    pool = [drill.trainer for drill in drills_for_mode(language_mode)]
    return rotating_choice(pool, date, salt=f"drill:{language_mode}")


def _today_bounds():
    today = timezone.localdate()
    start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min))
    return today, start, start + datetime.timedelta(days=1)


def _percent(done, total):
    return round(done / total * 100) if total else 0


def _study_item(user, language_mode, today):
    deck = get_global_deck(user, language_mode)
    correct = daily_correct_count(deck, today)
    is_done = correct >= DAILY_TARGET
    remaining = max(0, DAILY_TARGET - correct)
    available = Card.objects.for_user(user).active().filter(deck=deck).count()
    # After the goal, explicitly offer the selected deck's entire active pool.
    session_target = min(remaining, STUDY_SESSION_LIMIT) if remaining else available
    base_url = reverse("reviews:start_study", args=[deck.pk])
    session_params = [("all", "1")] if is_done else [("daily", "1")]
    return {
        "correct_count": correct,
        "target": DAILY_TARGET,
        "remaining": remaining,
        "is_done": is_done,
        "percent": min(100, _percent(correct, DAILY_TARGET)),
        "deck": deck,
        "available": available,
        "has_cards": available > 0,
        "session_size": min(session_target, available),
        "is_short": available < session_target,
        "study_url": f"{base_url}?{urlencode(session_params)}",
        "reverse_url": f"{base_url}?{urlencode([*session_params, ('direction', 'reverse')])}",
        "add_words_url": reverse("cards:create"),
    }


def _drill_item(user, language_mode, day_start, day_end, today):
    trainer = drill_of_the_day(language_mode, today)
    correct = TrainerAttempt.objects.filter(
        owner=user,
        language_mode=language_mode,
        trainer=trainer,
        is_correct=True,
        occurred_at__gte=day_start,
        occurred_at__lt=day_end,
    ).count()
    drill = drill_for_trainer(language_mode, trainer)
    return {
        "trainer": trainer,
        "label": drill.label,
        "correct_count": correct,
        "target": DAILY_TARGET,
        "is_done": correct >= DAILY_TARGET,
        "start_url": reverse("reviews:drill_start", args=[drill.slug]),
    }


def _media_item(user, language_mode, today):
    pack = pack_for_mode(language_mode)
    channel = channel_for_mode(language_mode)
    video = (
        get_kurzgesagt_video_of_day()
        if channel is KURZGESAGT_DE
        else get_channel_video_of_day(channel)
    )
    podcast = get_podcast_of_day() if "podcast" in pack.features else None
    return {
        "is_done": task_is_complete(user, "media", language_mode, today),
        "video": video,
        "podcast": podcast,
        "video_url": (
            reverse("video_study:workspace")
            + f"?video={video.youtube_id}&lang={channel.source_language}&autofetch=1"
            if video
            else None
        ),
        "podcast_url": (
            reverse("podcasts:workspace") + f"?episode={podcast.pk}" if podcast else None
        ),
    }


def build_daily_plan(request):
    user = request.user
    language_mode = get_language_mode(request)
    today, day_start, day_end = _today_bounds()

    study = _study_item(user, language_mode, today)
    drill = _drill_item(user, language_mode, day_start, day_end, today)
    media = _media_item(user, language_mode, today)

    items = [study, drill, media]
    completed = sum(1 for item in items if item["is_done"])
    return {
        "study": study,
        "drill": drill,
        "media": media,
        "completed": completed,
        "total": len(items),
        "percent": _percent(completed, len(items)),
    }
