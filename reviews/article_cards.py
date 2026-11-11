import csv
from functools import lru_cache

from django.conf import settings
from django.db import transaction

from cards.articles import parse_leading_article
from cards.models import Card
from core.language_packs import pack_for_mode
from decks.models import Deck
from decks.services import get_global_deck

ARTICLE_DRILL_TAG = "article-drill-b1-b2"
ARTICLE_DRILL_SOURCE_URL = (
    "https://www.goethe.de/pro/relaunch/prf/et/Goethe-Zertifikat_B1_Wortliste.pdf"
)
ARTICLE_DRILL_DATA_PATH = settings.BASE_DIR / "card_sets" / "DE-RU" / "german_b1_b2_nouns.csv"


@lru_cache(maxsize=1)
def article_seed_rows():
    with ARTICLE_DRILL_DATA_PATH.open(encoding="utf-8-sig", newline="") as source:
        rows = tuple(csv.DictReader(source))

    if len(rows) != 100:
        raise ValueError(f"Expected 100 article cards, found {len(rows)}.")
    counts = {article: 0 for article in ("der", "die", "das")}
    for row in rows:
        article, _stem = parse_leading_article(row.get("front", ""))
        if article not in counts:
            raise ValueError(f"Article seed row has invalid article: {row.get('front')!r}")
        counts[article] += 1
    if counts != {"der": 34, "die": 33, "das": 33}:
        raise ValueError(f"Article seed is not balanced: {counts}")
    return rows


@transaction.atomic
def seed_article_cards(deck):
    """Add the curated B1–B2 article set to a deck without duplicating cards."""
    if deck.language_mode != Deck.LanguageMode.DE_RU:
        raise ValueError("The curated article seed currently supports Russian mode only.")

    existing = {card.front: card for card in Card.objects.filter(deck=deck, tags=ARTICLE_DRILL_TAG)}
    cards_to_create = []
    cards_to_update = []
    for row in article_seed_rows():
        front = row["front"].strip()
        article, _stem = parse_leading_article(front)
        card = existing.get(front)
        if card is None:
            cards_to_create.append(
                Card(
                    deck=deck,
                    front=front,
                    back=row["back"].strip(),
                    example_sentence=row["example_sentence"].strip(),
                    notes=row["notes"].strip(),
                    tags=ARTICLE_DRILL_TAG,
                    card_type=Card.CardType.VOCABULARY,
                    article=article,
                    source_url=ARTICLE_DRILL_SOURCE_URL,
                )
            )
            continue

        changed = False
        if card.article != article:
            card.article = article
            changed = True
        if not card.source_url:
            card.source_url = ARTICLE_DRILL_SOURCE_URL
            changed = True
        if changed:
            cards_to_update.append(card)

    if cards_to_create:
        Card.objects.bulk_create(cards_to_create)
    if cards_to_update:
        Card.objects.bulk_update(cards_to_update, ["article", "source_url", "updated_at"])
    return len(existing) + len(cards_to_create)


def article_cards_for_user(user, language_mode):
    """Return the user's article pool, seeding the empty default deck on demand."""
    valid_articles = pack_for_mode(language_mode).articles
    cards = (
        Card.objects.for_user(user)
        .active()
        .nouns()
        .filter(deck__language_mode=language_mode, article__in=valid_articles)
    )
    if language_mode == Deck.LanguageMode.DE_RU:
        global_deck = Deck.objects.filter(
            owner=user,
            language_mode=language_mode,
            is_global=True,
        ).first()
        if global_deck:
            seed_article_cards(global_deck)
            curated = cards.filter(tags=ARTICLE_DRILL_TAG)
            if curated.exists():
                return curated

    if cards.exists():
        curated = cards.filter(tags=ARTICLE_DRILL_TAG)
        return curated if curated.exists() else cards

    if language_mode != Deck.LanguageMode.DE_RU:
        return cards

    deck = get_global_deck(user, language_mode)
    seed_article_cards(deck)
    return (
        Card.objects.for_user(user)
        .active()
        .nouns()
        .filter(
            deck__language_mode=language_mode,
            tags=ARTICLE_DRILL_TAG,
        )
    )
