from django.db import IntegrityError, transaction

from decks.models import Deck


def get_global_deck(user, language_mode):
    """Return the user's one main vocabulary deck for a language mode."""
    label = dict(Deck.LanguageMode.choices).get(language_mode, "Russian")
    defaults = {
        "name": f"Global deck · {label}",
        "description": ("Your main vocabulary deck. Add words manually or from video transcripts."),
    }
    try:
        with transaction.atomic():
            deck, _created = Deck.objects.get_or_create(
                owner=user,
                language_mode=language_mode,
                is_global=True,
                defaults=defaults,
            )
    except IntegrityError:
        deck = Deck.objects.get(
            owner=user,
            language_mode=language_mode,
            is_global=True,
        )
    return deck
