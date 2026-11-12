import random
import uuid

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cards.models import GLOBAL_DECK_RETIRE_STREAK, Card
from core.language_modes import (
    filter_cards_for_mode,
    filter_decks_for_mode,
    get_language_mode,
    get_target_language_label,
)
from decks.models import Deck
from reviews.analytics import weak_practice_cards
from reviews.daily import DAILY_TARGET, daily_correct_count
from reviews.models import ReviewLog, TrainerAttempt
from reviews.ratings import Rating
from reviews.services import CardUnavailable, ReviewRejected, record_review
from reviews.study import (
    accepted_german_forms,
    accepted_translations,
    check_answer,
    check_german_answer,
    clean_example_sentence,
)
from reviews.study_queue import STUDY_SESSION_LIMIT, select_study_queue

SESSION_KEY = "study_session"


def _eligible_cards(request):
    """Cards the signed-in user may study now, in the active language mode."""
    return filter_cards_for_mode(
        Card.objects.for_user(request.user).active(), get_language_mode(request)
    )


def _begin_session(request, queue, *, kind, direction, deck=None):
    request.session[SESSION_KEY] = {
        "sid": uuid.uuid4().hex,
        "deck_id": deck.pk if deck else None,
        "kind": kind,
        "queue": queue,
        "index": 0,
        "correct": 0,
        "incorrect": 0,
        "incorrect_ids": [],
        "shuffled": True,
        "direction": direction,
    }


def _direction(value):
    return "reverse" if value == "reverse" else "forward"


def start_study(request, deck_id):
    deck = get_object_or_404(
        filter_decks_for_mode(Deck.objects.for_user(request.user), get_language_mode(request)),
        pk=deck_id,
    )
    kind, limit = "deck", STUDY_SESSION_LIMIT
    if request.GET.get("all") == "1":
        kind, limit = "all", None
    elif request.GET.get("daily") == "1":
        remaining = max(0, DAILY_TARGET - daily_correct_count(deck))
        if remaining:
            kind, limit = "daily", min(remaining, STUDY_SESSION_LIMIT)
        else:
            kind = "extra"
            messages.info(
                request, "Today's goal is already complete, so this is optional extra practice."
            )

    queue = select_study_queue(_eligible_cards(request).filter(deck=deck), limit)
    if not queue:
        messages.info(request, "This deck has no active cards to study.")
        return redirect(deck.get_absolute_url())
    if kind == "daily" and len(queue) < limit:
        messages.info(
            request,
            f"Only {len(queue)} active word{'s' if len(queue) != 1 else ''} to study, "
            f"fewer than the {limit} left for today's goal.",
        )

    _begin_session(
        request, queue, kind=kind, direction=_direction(request.GET.get("direction")), deck=deck
    )
    return redirect("reviews:study_current")


@require_POST
def start_weak_practice(request):
    """Practice the user's weakest active words, recomputed here, not taken from the client."""
    cards = weak_practice_cards(
        request.user, get_language_mode(request), request.POST.get("period", 7)
    )
    if not cards:
        messages.info(request, "No weak words to practice right now.")
        return redirect("core:stats")
    queue = [card.pk for card in cards]
    random.shuffle(queue)
    _begin_session(request, queue, kind="weak", direction=_direction(request.POST.get("direction")))
    return redirect("reviews:study_current")


def _finished(study_session):
    return study_session["index"] >= len(study_session["queue"])


@require_POST
def retry_mistakes(request):
    study_session = request.session.get(SESSION_KEY)
    if not study_session or not _finished(study_session):
        messages.info(request, "Finish a study session to retry its mistakes.")
        return redirect("core:dashboard")
    mistakes = study_session.get("incorrect_ids", [])
    eligible = set(_eligible_cards(request).filter(pk__in=mistakes).values_list("pk", flat=True))
    queue = [card_id for card_id in mistakes if card_id in eligible]
    if not queue:
        messages.info(request, "None of those words are available to practice any more.")
        return redirect("reviews:study_complete")
    random.shuffle(queue)
    deck = Deck.objects.for_user(request.user).filter(pk=study_session.get("deck_id")).first()
    _begin_session(
        request,
        queue,
        kind="retry",
        direction=_direction(study_session.get("direction")),
        deck=deck,
    )
    return redirect("reviews:study_current")


def _current_session(request):
    return request.session.get(SESSION_KEY)


def _typed_submission_id(study_session, card_id):
    """One token per queue position, so a position can be answered only once."""
    return f"typed:{study_session['sid']}:{study_session['index']}:{card_id}"


def _record_result(study_session, card_id, correct):
    if correct:
        study_session["correct"] += 1
        return
    study_session["incorrect"] += 1
    mistakes = study_session.setdefault("incorrect_ids", [])
    if card_id not in mistakes:
        mistakes.append(card_id)


def _open_card(request, study_session):
    """The card at the queue position, skipping positions that cannot be studied.

    A position is skipped when the card became ineligible (retired, suspended,
    archived, another mode, deleted) or was already answered but the session
    never recorded it, for example because saving the session failed after the
    answer committed.
    """
    queue = study_session["queue"]
    start = study_session["index"]
    card = None
    while study_session["index"] < len(queue):
        card_id = queue[study_session["index"]]
        answered = (
            ReviewLog.objects.filter(submission_id=_typed_submission_id(study_session, card_id))
            .values_list("rating", flat=True)
            .first()
        )
        if answered is None:
            card = _eligible_cards(request).select_related("deck").filter(pk=card_id).first()
            if card:
                break
        else:
            _record_result(study_session, card_id, answered != Rating.AGAIN)
        study_session["index"] += 1
    if study_session["index"] != start:
        request.session[SESSION_KEY] = study_session
    return card


def _submitted_result(request):
    result = request.POST.get("result")
    if result == "retire":
        return True
    if result == "auto":
        return request.POST.get("auto_correct") == "1"
    return result == "correct"


def _submit_result(request, study_session, card):
    submission_id = _typed_submission_id(study_session, card.pk)
    if request.POST.get("submission") != submission_id:
        # A form for another position or session: this answer was already
        # taken or never belonged here. Show where the session really is.
        return redirect("reviews:study_current")
    retirement_requested = request.POST.get("result") == "retire"
    correct = _submitted_result(request)
    try:
        outcome = record_review(
            user=request.user,
            card_id=card.pk,
            language_mode=get_language_mode(request),
            rating=Rating.GOOD if correct else Rating.AGAIN,
            trainer=TrainerAttempt.Trainer.TYPED_REVIEW,
            submission_id=submission_id,
            retire=retirement_requested,
            record_daily=True,
        )
    except CardUnavailable:
        study_session["index"] += 1
        request.session[SESSION_KEY] = study_session
        messages.info(request, "That word is no longer available, so it was skipped.")
        return redirect("reviews:study_current")
    except ReviewRejected:
        # Duplicate submission: the first one already advanced the session.
        # Leave the session untouched so this request cannot overwrite it.
        return redirect("reviews:study_current")
    _record_result(study_session, card.pk, correct)
    if outcome.retired:
        messages.success(request, f"Retired {card.front} from future study sessions.")
    study_session["index"] += 1
    study_session.pop("checked_answer", None)
    request.session[SESSION_KEY] = study_session
    if _finished(study_session):
        return redirect("reviews:study_complete")
    return redirect("reviews:study_current")


def study_current(request):
    study_session = _current_session(request)
    if not study_session:
        messages.info(request, "Start a study session from a deck.")
        return redirect("decks:list")
    if "sid" not in study_session:
        # A stored session without a submission id; give it one now.
        study_session["sid"] = uuid.uuid4().hex
        request.session[SESSION_KEY] = study_session

    card = _open_card(request, study_session)
    if not card:
        return redirect("reviews:study_complete")

    direction = study_session.get("direction", "forward")
    is_reverse = direction == "reverse"
    submission_id = _typed_submission_id(study_session, card.pk)
    saved_check = study_session.get("checked_answer", {})
    checked = saved_check.get("submission") == submission_id
    user_answer = saved_check["answer"] if checked else ""

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "check":
            if request.POST.get("submission") != submission_id or checked:
                # A stale or replayed check must never reveal/evaluate the next
                # card. Refresh recovers the current card's existing result.
                return redirect("reviews:study_current")
            user_answer = request.POST.get("answer", "")
            study_session["checked_answer"] = {
                "submission": submission_id,
                "answer": user_answer,
            }
            request.session[SESSION_KEY] = study_session
            checked = True
        elif action == "submit_result":
            return _submit_result(request, study_session, card)

    answer_check = (
        check_german_answer(user_answer, card)
        if is_reverse
        else check_answer(user_answer, card.back)
    )
    context = {
        "card": card,
        "study_session": study_session,
        "position": study_session["index"] + 1,
        "total": len(study_session["queue"]),
        "checked": checked,
        "submission_token": submission_id,
        "user_answer": user_answer,
        "is_correct": answer_check.is_correct,
        "had_typo": answer_check.had_typo,
        "different_form": answer_check.different_form,
        "matched_answer": answer_check.matched_answer,
        "accepted_answers": (
            accepted_german_forms(card) if is_reverse else accepted_translations(card.back)
        ),
        "display_example_sentence": clean_example_sentence(card.example_sentence),
        "direction": direction,
        "is_reverse": is_reverse,
        # Reverse cards type the studied language; forward cards type the translation.
        "extra_letters": card.extra_letters if is_reverse else (),
        "target_language_label": get_target_language_label(get_language_mode(request)),
        "can_retire": (
            checked
            and card.deck.is_global
            and not card.is_retired
            and card.correct_streak + 1 >= GLOBAL_DECK_RETIRE_STREAK
        ),
    }
    return render(request, "reviews/study_card.html", context)


def study_complete(request):
    study_session = _current_session(request)
    if not study_session:
        messages.info(request, "No study session is active.")
        return redirect("decks:list")
    deck = None
    if study_session.get("deck_id"):
        deck = (
            filter_decks_for_mode(Deck.objects.for_user(request.user), get_language_mode(request))
            .filter(pk=study_session["deck_id"])
            .first()
        )
        if deck is None:
            messages.info(request, "That study session belongs to another language mode.")
            return redirect("core:dashboard")
    retry_count = 0
    if _finished(study_session):
        retry_count = (
            _eligible_cards(request).filter(pk__in=study_session.get("incorrect_ids", [])).count()
        )
    return render(
        request,
        "reviews/study_complete.html",
        {
            "study_session": study_session,
            "deck": deck,
            "direction": study_session.get("direction", "forward"),
            "retry_count": retry_count,
        },
    )
