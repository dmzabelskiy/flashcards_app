import html
import re

from django.db import migrations


def _strip_balanced_templates(value):
    output = []
    depth = 0
    index = 0
    while index < len(value):
        if value.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if depth and value.startswith("}}", index):
            depth -= 1
            index += 2
            continue
        if depth == 0:
            output.append(value[index])
        index += 1
    return "".join(output)


def _clean_translation(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(
        r"<ref\b[^>]*>.*?</ref\s*>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<ref\b[^>]*/\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "; ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _strip_balanced_templates(text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"[{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ;,:")

    alternatives = []
    seen = set()
    for alternative in re.split(r"\s*(?:;|\s+/\s+)\s*", text):
        alternative = re.sub(r"\s+", " ", alternative).strip(" -;,:|")
        normalized = alternative.casefold()
        if not alternative or normalized in seen:
            continue
        alternatives.append(alternative)
        seen.add(normalized)
        if len(alternatives) == 3:
            break
    return "; ".join(alternatives)


def clean_existing_video_cards(apps, schema_editor):
    Card = apps.get_model("cards", "Card")
    cards = Card.objects.exclude(source_url="").only("pk", "back").iterator()
    for card in cards:
        cleaned = _clean_translation(card.back)
        if cleaned and cleaned != card.back:
            Card.objects.filter(pk=card.pk).update(back=cleaned)


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0004_merge_default_100_word_decks"),
    ]

    operations = [
        migrations.RunPython(clean_existing_video_cards, migrations.RunPython.noop),
    ]
