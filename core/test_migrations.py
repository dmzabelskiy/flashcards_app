from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from core.testutils import create_user


def _restore_every_leaf_migration():
    """Migrate every app forward to its leaf migration again."""
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


class RemoveGrammarBookRecordsMigrationTests(TransactionTestCase):
    """0002 drops the retired Grammar book's table, content types and migration rows."""

    migrate_from = ("core", "0001_initial")
    migrate_to = ("core", "0002_remove_grammar_book_records")

    def setUp(self):
        super().setUp()
        # Registered before any test migrates backwards, so the schema comes back
        # even when a test fails part-way through.
        self.addCleanup(_restore_every_leaf_migration)

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    def _drop_grammar_book_table(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS grammar_book_grammarprogress")

    def test_grammar_book_table_content_types_and_history_are_removed(self):
        # Runs before the leaf restore above: cleanups are last-in, first-out.
        self.addCleanup(self._drop_grammar_book_table)
        self._migrate(self.migrate_from)
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE grammar_book_grammarprogress "
                "(id integer NOT NULL PRIMARY KEY AUTOINCREMENT, owner_id integer NOT NULL)"
            )
        content_type = ContentType.objects.create(app_label="grammar_book", model="grammarprogress")
        permission = Permission.objects.create(
            content_type=content_type, codename="view_grammarprogress", name="Can view progress"
        )
        reader = create_user("grammar-reader")
        reader.user_permissions.add(permission)
        log_entry = LogEntry.objects.create(
            user=reader,
            content_type=content_type,
            object_id="1",
            object_repr="Grammar progress",
            action_flag=ADDITION,
            change_message="",
        )
        unrelated = ContentType.objects.create(app_label="unrelated_app", model="thing")
        MigrationRecorder(connection).record_applied("grammar_book", "0001_initial")

        self._migrate(self.migrate_to)

        self.assertNotIn("grammar_book_grammarprogress", connection.introspection.table_names())
        self.assertFalse(ContentType.objects.filter(app_label="grammar_book").exists())
        self.assertFalse(Permission.objects.filter(codename="view_grammarprogress").exists())
        self.assertTrue(ContentType.objects.filter(pk=unrelated.pk).exists())
        self.assertFalse(
            any(
                app == "grammar_book"
                for app, _ in MigrationRecorder(connection).applied_migrations()
            )
        )
        # The admin history row survives with its content type cleared, and the user
        # who held the removed permission still loads.
        self.assertIsNone(LogEntry.objects.get(pk=log_entry.pk).content_type_id)
        reloaded = get_user_model().objects.get(pk=reader.pk)
        self.assertEqual(reloaded.username, "grammar-reader")
        self.assertFalse(reloaded.user_permissions.exists())
