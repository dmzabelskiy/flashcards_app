from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from cards.models import GLOBAL_DECK_RETIRE_STREAK, Card
from core.language_modes import filter_cards_for_mode, get_language_mode
from reviews.models import TrainerAttempt
from reviews.ratings import Rating
from reviews.services import (
    CardUnavailable,
    ReviewRejected,
    StaleSubmission,
    parse_elapsed_seconds,
    record_review,
)


def start_review(request):
    language_mode = get_language_mode(request)
    card = (
        filter_cards_for_mode(Card.objects.for_user(request.user).due(), language_mode)
        .order_by("?")
        .first()
    )
    if not card:
        messages.info(request, "No cards are due right now.")
        return redirect("core:dashboard")
    return redirect("reviews:review_card", card_id=card.pk)


def _submission_token(card):
    """Bind a rendered form to the card state it was rendered for."""
    return f"spaced:{card.pk}:{card.review_count}"


def _rendered_review_count(card_id, token):
    """The review count a token was rendered for, or ``None`` if it is not ours."""
    prefix = f"spaced:{card_id}:"
    count = token[len(prefix) :] if token.startswith(prefix) else ""
    return int(count) if count.isascii() and count.isdigit() and len(count) <= 9 else None


def _next_due_response(request, language_mode, excluding):
    next_card = (
        filter_cards_for_mode(
            Card.objects.for_user(request.user).due().exclude(pk=excluding), language_mode
        )
        .order_by("?")
        .first()
    )
    if next_card:
        return redirect("reviews:review_card", card_id=next_card.pk)
    messages.success(request, "Review queue complete.")
    return redirect("core:dashboard")


def review_card(request, card_id):
    language_mode = get_language_mode(request)
    queryset = filter_cards_for_mode(
        Card.objects.for_user(request.user).select_related("deck"), language_mode
    )
    card = get_object_or_404(queryset, pk=card_id)
    if card.is_suspended or card.is_retired or card.deck.is_archived:
        messages.info(request, f"{card.front} is not in review right now.")
        return redirect(card.get_absolute_url())
    if request.method == "POST":
        retire_requested = request.POST.get("retire") == "1"
        rating = Rating.GOOD if retire_requested else request.POST.get("rating")
        if rating not in Rating.values:
            return HttpResponseBadRequest("Invalid rating.")
        try:
            elapsed_seconds = parse_elapsed_seconds(request.POST.get("elapsed_seconds"))
        except ValueError:
            return HttpResponseBadRequest("Invalid elapsed time.")
        token = request.POST.get("submission", "")
        rendered_count = _rendered_review_count(card.pk, token)
        try:
            if rendered_count is None:
                raise StaleSubmission(card.pk)
            outcome = record_review(
                user=request.user,
                card_id=card.pk,
                language_mode=language_mode,
                rating=rating,
                trainer=TrainerAttempt.Trainer.SPACED_REVIEW,
                submission_id=token,
                expected_review_count=rendered_count,
                elapsed_seconds=elapsed_seconds,
                retire=retire_requested,
            )
        except CardUnavailable:
            raise Http404("Card not found.") from None
        except ReviewRejected:
            messages.info(request, f"{card.front} was already reviewed.")
            return _next_due_response(request, language_mode, excluding=card.pk)
        if outcome.retired:
            messages.success(request, f"Retired {card.front} from future study sessions.")
        return _next_due_response(request, language_mode, excluding=card.pk)

    context = {
        "card": card,
        "show_answer": request.GET.get("answer") == "1",
        "submission_token": _submission_token(card),
        "ratings": Rating,
        "can_retire": (
            card.deck.is_global
            and not card.is_retired
            and card.correct_streak + 1 >= GLOBAL_DECK_RETIRE_STREAK
        ),
    }
    return render(request, "reviews/review_card.html", context)
