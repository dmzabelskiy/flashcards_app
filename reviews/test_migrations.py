from datetime import UTC, datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from core.testutils import create_user


def _restore_every_leaf_migration():
    """Migrate every app forward to its leaf migration again."""
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


class DrillItemProgressMigrationTests(TransactionTestCase):
    """0008 moves the Deutsch-online word rows onto DrillItemProgress."""

    migrate_from = ("reviews", "0007_alter_trainerattempt_trainer")
    migrate_to = ("reviews", "0008_drillitemprogress")

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

    def test_word_rows_are_copied_and_a_database_without_any_still_migrates(self):
        owner = create_user("migration-owner")
        other_owner = create_user("migration-owner-2")
        old_apps = self._migrate(self.migrate_from)
        WordProgress = old_apps.get_model("reviews", "DeutschOnlineWordProgress")

        retired_row = WordProgress.objects.create(
            owner_id=owner.pk, set_slug="set-2", entry_id="aufpreis", correct_streak=3
        )
        WordProgress.objects.filter(pk=retired_row.pk).update(
            retired_at=datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC),
            created_at=datetime(2023, 1, 2, 3, 4, 5, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        )
        active_row = WordProgress.objects.create(
            owner_id=other_owner.pk, set_slug="set-1", entry_id="bewerten", correct_streak=1
        )
        WordProgress.objects.filter(pk=active_row.pk).update(
            created_at=datetime(2022, 5, 6, 7, 8, 9, tzinfo=UTC),
            updated_at=datetime(2022, 6, 7, 8, 9, 10, tzinfo=UTC),
        )

        new_apps = self._migrate(self.migrate_to)

        ItemProgress = new_apps.get_model("reviews", "DrillItemProgress")
        self.assertEqual(ItemProgress.objects.count(), 2)
        copied = ItemProgress.objects.filter(owner_id__in=[owner.pk, other_owner.pk]).order_by(
            "item_key"
        )
        self.assertEqual(
            [
                (
                    row.owner_id,
                    row.trainer,
                    row.item_key,
                    row.correct_streak,
                    row.retired_at,
                    row.created_at,
                    row.updated_at,
                )
                for row in copied
            ],
            [
                (
                    other_owner.pk,
                    "deutsch_online",
                    "set-1:bewerten",
                    1,
                    None,
                    datetime(2022, 5, 6, 7, 8, 9, tzinfo=UTC),
                    datetime(2022, 6, 7, 8, 9, 10, tzinfo=UTC),
                ),
                (
                    owner.pk,
                    "deutsch_online",
                    "set-2:aufpreis",
                    3,
                    datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC),
                    datetime(2023, 1, 2, 3, 4, 5, tzinfo=UTC),
                    datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
                ),
            ],
        )

    def test_migrating_a_database_with_no_word_rows_is_a_no_op(self):
        self._migrate(self.migrate_from)

        new_apps = self._migrate(self.migrate_to)

        ItemProgress = new_apps.get_model("reviews", "DrillItemProgress")
        self.assertEqual(ItemProgress.objects.count(), 0)


class RetiredTrainerProgressMigrationTests(TransactionTestCase):
    """0011 deletes the removed trainer's drill progress and keeps its attempt history."""

    migrate_from = ("reviews", "0010_reviewlog_submission_id")
    migrate_to = ("reviews", "0011_delete_deutsch_online_drill_progress")

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
        # core owns DailyTaskCompletion, which the daily-plan history check reads.
        return executor.loader.project_state([target, ("core", "0001_initial")]).apps

    def test_retired_progress_is_deleted_and_other_drills_keep_theirs(self):
        owner = create_user("migration-owner")
        old_apps = self._migrate(self.migrate_from)
        ItemProgress = old_apps.get_model("reviews", "DrillItemProgress")
        Attempt = old_apps.get_model("reviews", "TrainerAttempt")
        ItemProgress.objects.create(
            owner_id=owner.pk, trainer="deutsch_online", item_key="set-1:bewerten", correct_streak=2
        )
        ItemProgress.objects.create(
            owner_id=owner.pk,
            trainer="adjective_preposition",
            item_key="zufrieden-mit",
            correct_streak=1,
        )
        Attempt.objects.create(
            owner_id=owner.pk, trainer="deutsch_online", occurred_at=timezone.now()
        )
        DailyTaskCompletion = old_apps.get_model("core", "DailyTaskCompletion")
        DailyTaskCompletion.objects.create(
            owner_id=owner.pk,
            language_mode="DE-RU",
            task="grammar",
            date=timezone.localdate(),
            item_label="Akkusativ",
            completed_at=timezone.now(),
        )

        new_apps = self._migrate(self.migrate_to)

        ItemProgress = new_apps.get_model("reviews", "DrillItemProgress")
        self.assertEqual(
            list(ItemProgress.objects.values_list("trainer", "item_key")),
            [("adjective_preposition", "zufrieden-mit")],
        )
        Attempt = new_apps.get_model("reviews", "TrainerAttempt")
        self.assertEqual(Attempt.objects.filter(trainer="deutsch_online").count(), 1)
        DailyTaskCompletion = new_apps.get_model("core", "DailyTaskCompletion")
        self.assertEqual(
            list(DailyTaskCompletion.objects.values_list("task", flat=True)), ["grammar"]
        )

    def test_migrating_back_is_a_no_op_for_the_data(self):
        self._migrate(self.migrate_to)

        old_apps = self._migrate(self.migrate_from)

        ItemProgress = old_apps.get_model("reviews", "DrillItemProgress")
        self.assertEqual(ItemProgress.objects.count(), 0)
