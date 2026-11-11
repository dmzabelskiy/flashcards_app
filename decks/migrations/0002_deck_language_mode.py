# Generated for language mode support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("decks", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="deck",
            name="language_mode",
            field=models.CharField(
                choices=[("DE-RU", "DE-RU"), ("DE-ES", "DE-ES")],
                db_index=True,
                default="DE-RU",
                max_length=8,
            ),
        ),
    ]
