import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

TEMP_ROOT = Path(__file__).resolve().parent.parent / "temp"
TEMP_ROOT.mkdir(exist_ok=True)


def _make_source_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO widgets (name) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()


def _database_settings(db_path):
    return override_settings(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(db_path),
            }
        }
    )


class BackupDatabaseTests(SimpleTestCase):
    def test_backup_copies_tables_and_row_counts(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "source.sqlite3"
            _make_source_db(db_path)
            output_dir = tmp / "backups"

            with _database_settings(db_path):
                call_command("backup_database", "--output", str(output_dir))

            files = sorted(output_dir.glob("db-*.sqlite3"))
            self.assertEqual(len(files), 1)

            conn = sqlite3.connect(str(files[0]))
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("widgets", tables)
                count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
                self.assertEqual(count, 3)
            finally:
                conn.close()

    def test_backup_reads_uncheckpointed_wal_content(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "source.sqlite3"
            output_dir = tmp / "backups"

            # Keep a connection open in WAL mode with an uncheckpointed
            # write so the backup only sees the committed rows if it reads
            # through sqlite3's online backup API (which merges the WAL)
            # rather than naively copying the main database file.
            source_conn = sqlite3.connect(str(db_path))
            try:
                source_conn.execute("PRAGMA journal_mode=WAL")
                source_conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
                source_conn.executemany(
                    "INSERT INTO widgets (name) VALUES (?)",
                    [("a",), ("b",), ("c",)],
                )
                source_conn.commit()

                with _database_settings(db_path):
                    call_command("backup_database", "--output", str(output_dir))
            finally:
                source_conn.close()

            files = sorted(output_dir.glob("db-*.sqlite3"))
            self.assertEqual(len(files), 1)

            conn = sqlite3.connect(str(files[0]))
            try:
                count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
                self.assertEqual(count, 3)
            finally:
                conn.close()

    def test_prune_keeps_only_the_newest_n_and_leaves_other_files_alone(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "source.sqlite3"
            _make_source_db(db_path)
            output_dir = tmp / "backups"
            output_dir.mkdir()

            older_names = [
                "db-20200101-000000.sqlite3",
                "db-20200102-000000.sqlite3",
                "db-20200103-000000.sqlite3",
            ]
            for name in older_names:
                (output_dir / name).write_bytes(b"stub")
            unrelated_file = output_dir / "notes.txt"
            unrelated_file.write_bytes(b"keep me")

            with _database_settings(db_path):
                call_command(
                    "backup_database",
                    "--output",
                    str(output_dir),
                    "--keep",
                    "2",
                )

            remaining = sorted(p.name for p in output_dir.glob("db-*.sqlite3"))
            self.assertEqual(len(remaining), 2)
            self.assertNotIn(older_names[0], remaining)
            self.assertNotIn(older_names[1], remaining)
            self.assertIn(older_names[2], remaining)
            self.assertTrue(unrelated_file.exists())

    def test_prune_never_deletes_the_backup_just_written(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "source.sqlite3"
            _make_source_db(db_path)
            output_dir = tmp / "backups"
            output_dir.mkdir()

            # These stubs sort after any timestamp the backup command could
            # generate today, simulating a clock rollback or a hand-copied
            # file with a later-sorting name than the file we're about to
            # write.
            future_names = [
                "db-29990101-000000.sqlite3",
                "db-29990102-000000.sqlite3",
            ]
            for name in future_names:
                (output_dir / name).write_bytes(b"stub")

            with _database_settings(db_path):
                call_command(
                    "backup_database",
                    "--output",
                    str(output_dir),
                    "--keep",
                    "1",
                )

            remaining = sorted(p.name for p in output_dir.glob("db-*.sqlite3"))
            self.assertEqual(len(remaining), 1)
            new_backup = remaining[0]
            self.assertNotIn(new_backup, future_names)
            self.assertGreater((output_dir / new_backup).stat().st_size, 0)

    def test_keep_zero_keeps_everything(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "source.sqlite3"
            _make_source_db(db_path)
            output_dir = tmp / "backups"
            output_dir.mkdir()
            (output_dir / "db-20200101-000000.sqlite3").write_bytes(b"stub")

            with _database_settings(db_path):
                call_command(
                    "backup_database",
                    "--output",
                    str(output_dir),
                    "--keep",
                    "0",
                )

            remaining = sorted(p.name for p in output_dir.glob("db-*.sqlite3"))
            self.assertEqual(len(remaining), 2)

    def test_missing_database_raises_command_error(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "missing.sqlite3"

            with _database_settings(db_path):
                with self.assertRaises(CommandError):
                    call_command("backup_database", "--output", str(tmp / "backups"))

            self.assertFalse((tmp / "backups").exists())


class SqliteTuningOptionsTests(SimpleTestCase):
    def test_default_database_has_wal_tuning_options(self):
        options = settings.DATABASES["default"]["OPTIONS"]
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertEqual(options["timeout"], 20)
        self.assertEqual(
            options["init_command"],
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        )
