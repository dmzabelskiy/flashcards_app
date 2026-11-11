from django.db import migrations
from django.db.models import Count


def merge_default_decks(apps, schema_editor):
    Card = apps.get_model("cards", "Card")
    Deck = apps.get_model("decks", "Deck")

    legacy_decks = (
        Deck.objects.filter(is_global=False, is_archived=False)
        .annotate(card_total=Count("cards"))
        .filter(card_total=100)
        .order_by("owner_id", "language_mode", "id")
    )
    labels = {"DE-RU": "Russian", "DE-ES": "Spanish"}
    for source in legacy_decks:
        target, _created = Deck.objects.get_or_create(
            owner_id=source.owner_id,
            language_mode=source.language_mode,
            is_global=True,
            defaults={
                "name": f"Global deck · {labels.get(source.language_mode, source.language_mode)}",
                "description": (
                    "Your main vocabulary deck. Add words manually or from video transcripts."
                ),
            },
        )
        Card.objects.filter(deck_id=source.id).update(deck_id=target.id)
        source.is_archived = True
        source.save(update_fields=["is_archived"])


def noop_reverse(apps, schema_editor):
    # The source deck for each moved card is not retained in the merged model.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0003_card_correct_streak_card_is_retired_card_retired_at_and_more"),
    ]

    operations = [
        migrations.RunPython(merge_default_decks, noop_reverse),
    ]
