from django.db import migrations


def remove_grammar_book_records(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    types = ContentType.objects.filter(app_label="grammar_book")
    Permission.objects.filter(content_type__in=types).delete()
    types.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunSQL(
            "DROP TABLE IF EXISTS grammar_book_grammarprogress",
            migrations.RunSQL.noop,
        ),
        migrations.RunPython(remove_grammar_book_records, migrations.RunPython.noop),
        migrations.RunSQL(
            "DELETE FROM django_migrations WHERE app = 'grammar_book'",
            migrations.RunSQL.noop,
        ),
    ]
