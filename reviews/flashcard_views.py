import random

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from cards.models import Card
from core.language_modes import get_language_mode, get_target_language, get_target_language_label
from reviews.article_cards import article_cards_for_user
from reviews.drill_engine import retired_item_keys
from reviews.drills import ARTICLE_TRAINER, drill_for_trainer

SESSION_KEY = "flashcards_session"


def _trainer_meta(request, trainer):
    drill = drill_for_trainer(get_language_mode(request), trainer)
    if drill is None:
        return None
    return {"label": drill.label, "description": drill.description}


def _available_trainer(request, trainer):
    if drill_for_trainer(get_language_mode(request), trainer) is None:
        messages.info(request, "That flashcard set is not available in the selected language mode.")
        return False
    return True


def _discard_if_unavailable(request, trainer):
    """Drop a saved session whose trainer is no longer offered; True when dropped."""
    if _available_trainer(request, trainer):
        return False
    request.session.pop(SESSION_KEY, None)
    return True


def _queue_for(request, trainer):
    """The flashcard queue for a trainer, minus the items the user retired."""
    language_mode = get_language_mode(request)
    retired = retired_item_keys(request.user, trainer)
    if trainer == ARTICLE_TRAINER:
        return [
            card_id
            for card_id in article_cards_for_user(request.user, language_mode).values_list(
                "id", flat=True
            )
            if str(card_id) not in retired
        ]
    drill = drill_for_trainer(language_mode, trainer)
    target = get_target_language(language_mode)
    return [item.id for item in drill.items_for(target) if item.id not in retired]


def flashcards_start(request, trainer):
    if not _available_trainer(request, trainer):
        return redirect("core:dashboard")

    queue = _queue_for(request, trainer)
    if not queue:
        messages.info(request, "There are no cards in this flashcard set yet.")
        return redirect("reviews:drills")
    random.shuffle(queue)
    session = {
        "trainer": trainer,
        "language_mode": get_language_mode(request),
        "queue": queue,
        "index": 0,
        "revealed": False,
    }
    request.session[SESSION_KEY] = session
    return redirect("reviews:flashcards", trainer=trainer)


def _valid_session(session, trainer):
    """A saved flashcard round for this trainer whose shape the views can use."""
    return (
        isinstance(session, dict)
        and session.get("trainer") == trainer
        and isinstance(session.get("queue"), list)
        and isinstance(session.get("index"), int)
        and not isinstance(session.get("index"), bool)
        and session["index"] >= 0
    )


def _article_flashcard(request, card_id):
    if not isinstance(card_id, int) or isinstance(card_id, bool):
        return None
    card = get_object_or_404(
        Card.objects.for_user(request.user).active().select_related("deck"),
        pk=card_id,
    )
    return {
        "question_label": "Substantiv",
        "question": card.noun_stem,
        "question_detail": f"Translation: {card.back}",
        "answer_rows": [
            {"label": "Article", "value": f"{card.article} {card.noun_stem}"},
            {"label": "Meaning", "value": card.back},
        ],
        "example": card.example_sentence,
        "accent": card.article,
    }


def _drill_flashcard(request, drill, item_id):
    """A flashcard derived from a drill file: prompt in front, first answers behind."""
    language_mode = get_language_mode(request)
    target = get_target_language(language_mode)
    target_label = get_target_language_label(language_mode)
    item = drill.item(item_id) if isinstance(item_id, str) else None
    alternatives = item.alternatives_for(target) if item else ()
    if not alternatives:
        return None
    first = alternatives[0]
    rows = [
        {
            "label": field.label.replace("{target_label}", target_label),
            "value": " / ".join(
                field.option_label(value) for value in first.accepted(field.key, target)
            ),
        }
        for field in drill.fields
    ]
    rows.extend({"label": label, "value": value} for label, value in first.reveal)
    return {
        "question_label": drill.label,
        "question": item.prompt,
        "question_detail": drill.instruction.replace("{target_label}", target_label),
        "answer_rows": rows,
        "example": first.example,
        "accent": "grammar",
    }


def _flashcard_for(request, trainer, item_id):
    if trainer == ARTICLE_TRAINER:
        return _article_flashcard(request, item_id)
    drill = drill_for_trainer(get_language_mode(request), trainer)
    return _drill_flashcard(request, drill, item_id) if drill else None


def _question_size(question):
    length = len(" ".join(str(question).split()))
    if length >= 44:
        return "extra-long"
    if length >= 30:
        return "long"
    if length >= 20:
        return "medium"
    return "short"


def flashcards(request, trainer):
    session = request.session.get(SESSION_KEY)
    if not _valid_session(session, trainer):
        return redirect("reviews:flashcards_start", trainer=trainer)
    if _discard_if_unavailable(request, trainer):
        return redirect("core:dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "show_answer":
            session["revealed"] = True
        elif action == "next" and session.get("revealed"):
            session["index"] += 1
            session["revealed"] = False
        elif action == "back" and session.get("index", 0) > 0:
            session["index"] -= 1
            session["revealed"] = True
        request.session[SESSION_KEY] = session
        return redirect("reviews:flashcards", trainer=trainer)

    if session["index"] >= len(session["queue"]):
        return redirect("reviews:flashcards_complete", trainer=trainer)

    item = _flashcard_for(request, trainer, session["queue"][session["index"]])
    if item is None:
        return redirect("reviews:flashcards_start", trainer=trainer)
    item["question_size"] = _question_size(item.get("question", ""))
    return render(
        request,
        "reviews/flashcards.html",
        {
            "flashcard": item,
            "revealed": bool(session.get("revealed")),
            "position": session["index"] + 1,
            "total": len(session["queue"]),
            "trainer": trainer,
            "trainer_meta": _trainer_meta(request, trainer),
        },
    )


def flashcards_complete(request, trainer):
    session = request.session.get(SESSION_KEY)
    if not _valid_session(session, trainer):
        return redirect("reviews:flashcards_start", trainer=trainer)
    if _discard_if_unavailable(request, trainer):
        return redirect("core:dashboard")
    return render(
        request,
        "reviews/flashcards_complete.html",
        {
            "trainer": trainer,
            "trainer_meta": _trainer_meta(request, trainer),
            "total": len(session["queue"]),
        },
    )
