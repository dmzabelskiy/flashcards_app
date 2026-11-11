from django.db import migrations

RETIRED_TRAINER = "deutsch_online"


def delete_retired_trainer_progress(apps, schema_editor):
    """Drop the drill progress of the removed Deutsch-online trainer.

    Forward only. TrainerAttempt rows are activity history and stay.
    """
    DrillItemProgress = apps.get_model("reviews", "DrillItemProgress")
    DrillItemProgress.objects.filter(trainer=RETIRED_TRAINER).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0010_reviewlog_submission_id"),
    ]

    operations = [
        migrations.RunPython(delete_retired_trainer_progress, migrations.RunPython.noop),
    ]
