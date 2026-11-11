import logging

from django.contrib import messages
from django.db import connection
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from cards.models import Card
from core.language_modes import (
    filter_cards_for_mode,
    filter_decks_for_mode,
    get_language_mode,
    get_target_language_label,
    set_language_mode,
)
from decks.models import Deck
from reviews.analytics import build_personal_analytics
from reviews.daily_plan import build_daily_plan
from video_study.services import (
    KURZGESAGT_DE,
    channel_for_mode,
    start_channel_prefetch,
    start_kurzgesagt_prefetch,
)

logger = logging.getLogger(__name__)


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.error("Health check database query failed", exc_info=True)
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


def _today_range():
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


@require_POST
def set_language_mode_view(request):
    requested_mode = request.POST.get("language_mode", "")
    try:
        set_language_mode(request, requested_mode)
    except ValueError:
        return HttpResponseBadRequest("Unsupported language mode.")
    messages.success(
        request, f"Study language changed to {get_target_language_label(requested_mode)}."
    )
    return redirect("core:dashboard")


def dashboard(request):
    language_mode = get_language_mode(request)
    daily_plan = build_daily_plan(request)
    channel = channel_for_mode(language_mode)
    if channel is KURZGESAGT_DE:
        start_kurzgesagt_prefetch()
    else:
        start_channel_prefetch(channel)
    context = {
        "daily_plan": daily_plan,
        "analytics": build_personal_analytics(request.user, language_mode, 7),
    }
    return render(request, "core/dashboard.html", context)


def stats(request):
    language_mode = get_language_mode(request)
    analytics = build_personal_analytics(
        request.user,
        language_mode,
        request.GET.get("period", 7),
    )
    context = {
        "total_cards": filter_cards_for_mode(
            Card.objects.for_user(request.user), language_mode
        ).count(),
        "total_decks": filter_decks_for_mode(
            Deck.objects.for_user(request.user), language_mode
        ).count(),
        "analytics": analytics,
        "activity": analytics["activity"],
    }
    return render(request, "core/stats.html", context)
