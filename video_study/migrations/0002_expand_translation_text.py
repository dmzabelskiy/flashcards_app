from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("video_study", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="wordtranslation",
            name="source_text",
            field=models.CharField(max_length=500),
        ),
        migrations.AlterField(
            model_name="wordtranslation",
            name="translated_text",
            field=models.CharField(max_length=1000),
        ),
    ]
