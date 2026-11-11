from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0012_trainerattempt_trainer_free_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="drillitemprogress",
            name="trainer",
            field=models.CharField(db_index=True, max_length=32),
        ),
    ]
