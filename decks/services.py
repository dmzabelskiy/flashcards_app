import logging
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from decks.models import Deck
from imports_app.services import import_cards_from_csv

logger = logging.getLogger(__name__)

# The language modes whose empty global deck starts with a set of words, and the CSV
# file of each.  A mode that is not listed starts empty.
STARTER_SETS = {
    Deck.LanguageMode.ES_RU: Path(
        settings.BASE_DIR, "card_sets", "ES-RU", "spanish_a2_starter.csv"
    ),
}


def seed_starter_cards(deck):
    """Fill a global deck that holds no card at all with its mode's starter set.

    Call it inside the transaction that read or created the deck: the check for
    cards and the import then share it, and the import itself skips any front the
    deck already has.  A missing or unreadable file is logged and leaves the deck
    empty.
    """
    path = STARTER_SETS.get(deck.language_mode)
    if path is None or deck.cards.exists():
        return
    try:
        csv_text = Path(path).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        logger.exception("The starter set %s could not be read; the deck stays empty.", path)
        return
    result = import_cards_from_csv(deck, csv_text)
    if result.errors:
        logger.error("The starter set %s had %d row(s) with errors.", path, len(result.errors))


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
            seed_starter_cards(deck)
    except IntegrityError:
        deck = Deck.objects.get(
            owner=user,
            language_mode=language_mode,
            is_global=True,
        )
    return deck
