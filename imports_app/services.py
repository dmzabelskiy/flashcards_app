import csv
from dataclasses import dataclass, field
from io import StringIO

from cards.articles import parse_leading_article
from cards.models import Card
from core.language_packs import pack_for_mode


@dataclass(frozen=True)
class CsvImportError:
    row_number: int
    message: str


@dataclass
class CsvImportResult:
    imported_count: int = 0
    errors: list[CsvImportError] = field(default_factory=list)


OPTIONAL_FIELDS = {"example_sentence", "notes", "tags", "card_type"}
VALID_CARD_TYPES = {choice for choice, _label in Card.CardType.choices}


def _clean(value):
    return (value or "").strip()


def _row_is_empty(row):
    return not any(_clean(value) for value in row.values())


def _article_for(front, card_type, articles):
    """The article a vocabulary front starts with, or "".

    Only a lower-case article of the deck's pack counts, followed by at least one
    more word, and the front must not read as a sentence.  The front itself is
    stored unchanged.
    """
    if card_type != Card.CardType.VOCABULARY or front.endswith((".", "!", "?")):
        return ""
    first_word = front.split(" ", 1)[0]
    if first_word not in articles:
        return ""
    return parse_leading_article(front, articles)[0]


def import_cards_from_csv(deck, csv_text):
    result = CsvImportResult()
    articles = pack_for_mode(deck.language_mode).articles
    reader = csv.DictReader(StringIO(csv_text))

    if not reader.fieldnames:
        result.errors.append(CsvImportError(row_number=1, message="CSV file has no header row."))
        return result

    fieldnames = {name.strip() for name in reader.fieldnames if name}
    missing = {"front", "back"} - fieldnames
    if missing:
        result.errors.append(
            CsvImportError(
                row_number=1, message=f"Missing required column(s): {', '.join(sorted(missing))}."
            )
        )
        return result

    cards = []
    for row_number, row in enumerate(reader, start=2):
        normalized = {key.strip(): _clean(value) for key, value in row.items() if key}
        if _row_is_empty(normalized):
            continue

        front = normalized.get("front", "")
        back = normalized.get("back", "")
        if not front or not back:
            result.errors.append(
                CsvImportError(row_number=row_number, message="Both front and back are required.")
            )
            continue

        card_type = normalized.get("card_type") or Card.CardType.VOCABULARY
        if card_type not in VALID_CARD_TYPES:
            result.errors.append(
                CsvImportError(row_number=row_number, message=f"Unknown card_type '{card_type}'.")
            )
            continue

        cards.append(
            Card(
                deck=deck,
                front=front,
                article=_article_for(front, card_type, articles),
                back=back,
                example_sentence=normalized.get("example_sentence", ""),
                notes=normalized.get("notes", ""),
                tags=normalized.get("tags", ""),
                card_type=card_type,
            )
        )

    Card.objects.bulk_create(cards)
    result.imported_count = len(cards)
    return result
