from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0011_delete_deutsch_online_drill_progress"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trainerattempt",
            name="trainer",
            field=models.CharField(db_index=True, max_length=32),
        ),
    ]
