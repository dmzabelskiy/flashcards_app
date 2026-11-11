from importlib import import_module

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from cards.models import Card
from decks.models import Deck

User = get_user_model()

assign_owner_migration = import_module("decks.migrations.0005_assign_deck_owner")
assign_owner = assign_owner_migration.assign_owner


class ForUserHelperTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.alice_deck = Deck.objects.create(name="A", owner=self.alice)
        self.bob_deck = Deck.objects.create(name="B", owner=self.bob)

    def test_deck_for_user_excludes_other_owners(self):
        self.assertQuerySetEqual(Deck.objects.for_user(self.alice), [self.alice_deck])

    def test_card_for_user_scopes_via_deck(self):
        alice_card = Card.objects.create(deck=self.alice_deck, front="a", back="b")
        Card.objects.create(deck=self.bob_deck, front="c", back="d")
        self.assertQuerySetEqual(Card.objects.for_user(self.alice), [alice_card])


class AssignOwnerMigrationTests(TransactionTestCase):
    """Exercises the real 0005 `assign_owner` data migration in place, at the
    historical schema state where `Deck.owner` was still nullable (0004).

    Since migration 0006, `Deck.owner` is a NOT NULL column in the
    head schema, so a plain `TestCase` (which runs against the fully
    migrated test database) can no longer create an orphaned (owner=NULL)
    Deck row at all -- the DB itself rejects the insert regardless of which
    historical `apps` registry is used to build the query. To still cover
    the orphan-backfill / no-superuser-raises branches of 0005 -- which run
    unattended against the real production DB at deploy time -- we use
    Django's `MigrationExecutor` to physically rewind the test database to
    migration 0004 for the duration of each test, create orphaned rows
    against the *historical* model there, then migrate forward through the
    real 0005 migration and assert on its effect. `TransactionTestCase` is
    required (not `TestCase`) because this performs real schema DDL, which
    cannot happen inside the outer atomic transaction a plain `TestCase`
    wraps around each test.
    """

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor

    def setUp(self):
        super().setUp()
        # Rewind so `owner` is nullable again (0005/0006 unapplied).
        self._migrate([("decks", "0004_deck_owner_nullable")])

    def tearDown(self):
        # Always restore to head so the rest of the suite sees the final
        # (non-nullable) schema. Must run before super().tearDown()'s flush,
        # which assumes the head schema is in place.
        self._migrate([("decks", "0006_deck_owner_required")])
        super().tearDown()

    def test_assigns_orphaned_decks_to_first_superuser_by_pk(self):
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state([("decks", "0004_deck_owner_nullable")]).apps
        HistoricalUser = old_apps.get_model("auth", "User")
        HistoricalDeck = old_apps.get_model("decks", "Deck")

        first_super = HistoricalUser.objects.create(
            username="root1", is_superuser=True, is_staff=True, password="x"
        )
        second_super = HistoricalUser.objects.create(
            username="root2", is_superuser=True, is_staff=True, password="x"
        )
        self.assertLess(first_super.pk, second_super.pk)

        orphan_a = HistoricalDeck.objects.create(name="Orphan A", language_mode="DE-RU")
        orphan_b = HistoricalDeck.objects.create(name="Orphan B", language_mode="DE-RU")

        self._migrate([("decks", "0005_assign_deck_owner")])

        orphan_a.refresh_from_db()
        orphan_b.refresh_from_db()
        self.assertEqual(orphan_a.owner_id, first_super.pk)
        self.assertEqual(orphan_b.owner_id, first_super.pk)

    def test_raises_when_orphaned_decks_but_no_superuser(self):
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state([("decks", "0004_deck_owner_nullable")]).apps
        HistoricalUser = old_apps.get_model("auth", "User")
        HistoricalDeck = old_apps.get_model("decks", "Deck")

        regular = HistoricalUser.objects.create(
            username="regular", is_superuser=False, is_staff=False, password="x"
        )
        orphan = HistoricalDeck.objects.create(name="Orphan", language_mode="DE-RU")

        with self.assertRaises(RuntimeError):
            self._migrate([("decks", "0005_assign_deck_owner")])

        # The migration raised before assigning an owner, so the row is
        # still orphaned and migration 0005 is still unapplied. Clean up
        # here (outside of what's under test) so tearDown's forward-migrate
        # to head doesn't re-run 0005 against the same orphan and raise
        # again.
        HistoricalDeck.objects.filter(pk=orphan.pk).update(owner=regular)


class AssignOwnerNoOpTests(TestCase):
    def test_no_op_when_no_orphaned_decks(self):
        owner = User.objects.create_user("alice", password="pw")
        deck = Deck.objects.create(name="Owned", owner=owner)

        # No superuser exists at all; if this incorrectly tried to assign
        # an owner it would raise RuntimeError instead of returning quietly.
        assign_owner(global_apps, None)

        deck.refresh_from_db()
        self.assertEqual(deck.owner, owner)
