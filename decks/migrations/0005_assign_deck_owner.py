from django.db import migrations


def assign_owner(apps, schema_editor):
    Deck = apps.get_model("decks", "Deck")
    User = apps.get_model("auth", "User")

    orphaned = Deck.objects.filter(owner__isnull=True)
    if not orphaned.exists():
        return

    superuser = User.objects.filter(is_superuser=True).order_by("pk").first()
    if superuser is None:
        raise RuntimeError(
            "Cannot assign existing decks to an owner: no superuser exists. "
            "Create one first with `python manage.py createsuperuser`, then re-run migrate."
        )
    orphaned.update(owner=superuser)


def unassign_owner(apps, schema_editor):
    # Reverse is a no-op: we cannot know which decks were previously orphaned.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("decks", "0004_deck_owner_nullable"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(assign_owner, unassign_owner),
    ]
