import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from cards.models import GLOBAL_DECK_RETIRE_STREAK, Card
from core.language_modes import get_language_mode, get_source_language
from decks.services import get_global_deck
from video_study.nl.capture import resolve_capture_nl
from video_study.normalization import resolve_capture
from video_study.services import clean_translation_text


@require_POST
def add_to_global_deck(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    source_form = " ".join(
        str(payload.get("source_form") or payload.get("word", "")).split()
    ).strip()
    translation = clean_translation_text(
        payload.get("translation", ""),
        max_alternatives=3,
    )
    if not source_form or not translation:
        return JsonResponse(
            {"error": "A source word and translation are required."},
            status=400,
        )

    context = " ".join(str(payload.get("context", "")).split()).strip()
    source_language = payload.get("source_language", "de")
    language_mode = get_language_mode(request)
    if (source_language == "nl") != (get_source_language(language_mode) == "nl"):
        return JsonResponse(
            {"error": "Source language does not match the active study mode."}, status=400
        )
    resolver = resolve_capture_nl if source_language == "nl" else resolve_capture
    resolution = resolver(source_form, context)
    if not resolution.trustworthy:
        return JsonResponse(
            {
                "error": (
                    "Couldn't find the dictionary form of this word right now. "
                    "Try again in a minute."
                )
            },
            status=422,
        )
    word = resolution.lemma

    deck = get_global_deck(request.user, language_mode)
    existing = deck.cards.filter(front__iexact=word).first()
    if existing:
        return JsonResponse(
            {
                "card_id": existing.pk,
                "created": False,
                "word": existing.front,
                "translation": existing.back,
                "article": existing.article,
                "plural": existing.plural,
                "correct_streak": existing.correct_streak,
                "message": "This word is already in your global deck.",
            }
        )

    card = Card.objects.create(
        deck=deck,
        front=word,
        back=translation,
        article=resolution.article,
        plural=resolution.plural,
        example_sentence=resolution.example_sentence,
    )
    return JsonResponse(
        {
            "card_id": card.pk,
            "created": True,
            "word": card.front,
            "translation": card.back,
            "article": card.article,
            "plural": card.plural,
            "part_of_speech": resolution.part_of_speech,
            "correct_streak": card.correct_streak,
            "message": "Added to your global deck.",
        },
        status=201,
    )


@require_POST
def retire_global_card(request, pk):
    card = get_object_or_404(
        Card.objects.for_user(request.user).select_related("deck"),
        pk=pk,
        deck__is_global=True,
        deck__language_mode=get_language_mode(request),
    )
    if not card.can_retire:
        messages.warning(
            request,
            f"Answer this card correctly {GLOBAL_DECK_RETIRE_STREAK} times in a row before retiring it.",
        )
        return redirect(card.deck.get_absolute_url())
    card.retire()
    messages.success(request, f"Retired {card.front} from the global deck.")
    return redirect(card.deck.get_absolute_url())
