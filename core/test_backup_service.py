import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core import backup
from core.backup import (
    BackupConfig,
    BackupError,
    LockBusy,
    file_lock,
    load_state,
    local_due,
    run_local_backup,
    update_state,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = REPO_ROOT / "temp"
TEMP_ROOT.mkdir(exist_ok=True)

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def make_clock(start=T0):
    box = {"now": start}

    def clock():
        return box["now"]

    clock.box = box
    return clock


def make_wal_db(path, rows=3):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO widgets (name) VALUES (?)", [(f"w{i}",) for i in range(rows)])
    conn.commit()
    return conn


def make_config(tmp, **overrides):
    values = dict(db_path=tmp / "source.sqlite3", backup_dir=tmp / "backups")
    values.update(overrides)
    return BackupConfig(**values)


class TmpDirCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)


class SnapshotConsistencyTests(TmpDirCase):
    def test_snapshot_contains_committed_rows_a_file_copy_would_miss(self):
        source = self.tmp / "source.sqlite3"
        conn = make_wal_db(source, rows=0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # schema reaches the main file
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executemany("INSERT INTO widgets (name) VALUES (?)", [("a",), ("b",)])
        conn.commit()
        try:
            # Precondition that makes the test meaningful: the rows live only
            # in the WAL, so copying the main file yields an empty table.
            naive = self.tmp / "naive.sqlite3"
            naive.write_bytes(source.read_bytes())
            naive_conn = sqlite3.connect(str(naive))
            self.assertEqual(naive_conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0], 0)
            naive_conn.close()

            path = run_local_backup(make_config(self.tmp))
        finally:
            conn.close()
        snap = sqlite3.connect(str(path))
        try:
            self.assertEqual(snap.execute("SELECT COUNT(*) FROM widgets").fetchone()[0], 2)
        finally:
            snap.close()

    def test_snapshot_taken_during_concurrent_writes_is_whole_transactions(self):
        source = self.tmp / "source.sqlite3"
        conn = make_wal_db(source, rows=0)
        conn.execute("DROP TABLE widgets")
        conn.execute("CREATE TABLE pairs (id INTEGER PRIMARY KEY, grp INTEGER, side TEXT)")
        conn.commit()
        conn.close()

        stop = threading.Event()
        errors = []

        def writer():
            writer_conn = sqlite3.connect(str(source), timeout=30)
            try:
                group = 0
                while not stop.is_set():
                    group += 1
                    with writer_conn:
                        writer_conn.execute(
                            "INSERT INTO pairs (grp, side) VALUES (?, 'l')", (group,)
                        )
                        writer_conn.execute(
                            "INSERT INTO pairs (grp, side) VALUES (?, 'r')", (group,)
                        )
            except Exception as exc:  # pragma: no cover - surfaced by the assertion below
                errors.append(exc)
            finally:
                writer_conn.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            paths = [
                run_local_backup(make_config(self.tmp, keep=10), keep=0, lock_wait=5)
                for _ in range(5)
            ]
        finally:
            stop.set()
            thread.join()
        self.assertEqual(errors, [])
        for path in paths:
            snap = sqlite3.connect(str(path))
            try:
                self.assertEqual(snap.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                torn = snap.execute(
                    "SELECT COUNT(*) FROM (SELECT grp FROM pairs GROUP BY grp HAVING COUNT(*) != 2)"
                ).fetchone()[0]
                self.assertEqual(torn, 0)
            finally:
                snap.close()

    def test_snapshot_is_one_self_contained_file(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        path = run_local_backup(make_config(self.tmp))
        header = path.read_bytes()[:100]
        # Bytes 18/19 are the file-format read/write versions: 1 = rollback
        # journal, 2 = WAL. A WAL snapshot could not be opened read-only
        # without sidecar files.
        self.assertEqual((header[18], header[19]), (1, 1))
        self.assertEqual(sorted(p.name for p in path.parent.glob("*-wal")), [])

    def test_snapshot_name_never_collides_within_one_second(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp)
        clock = make_clock()
        first = run_local_backup(config, clock=clock)
        second = run_local_backup(config, clock=clock)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())


class FailureTests(TmpDirCase):
    def test_failed_copy_leaves_no_file_keeps_old_backups_and_records_failure(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp, keep=1)
        good = run_local_backup(config, clock=make_clock(T0))
        before = good.read_bytes()

        clock = make_clock(T0 + timedelta(days=1))
        with mock.patch.object(
            backup, "_copy_database", side_effect=sqlite3.OperationalError("disk full")
        ):
            with self.assertRaises(BackupError):
                run_local_backup(config, clock=clock)

        names = sorted(p.name for p in config.backup_dir.iterdir() if p.name.startswith("db-"))
        self.assertEqual(names, [good.name])  # nothing partial, nothing pruned
        self.assertEqual(good.read_bytes(), before)
        state = load_state(config.backup_dir)
        self.assertEqual(state["local_failures"], 1)
        self.assertIn("disk full", state["last_local_error"])
        # The failed attempt did not count as a success.
        self.assertEqual(state["last_local_success_at"], backup.format_ts(T0))

    def test_snapshot_that_fails_verification_is_discarded(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp, keep=1)
        good = run_local_backup(config)
        with mock.patch.object(backup, "_verify_snapshot", side_effect=BackupError("bad snapshot")):
            with self.assertRaises(BackupError):
                run_local_backup(config, clock=make_clock(T0 + timedelta(days=2)))
        self.assertEqual(sorted(p.name for p in config.backup_dir.glob("db-*")), [good.name])

    def test_real_verification_rejects_a_corrupt_snapshot(self):
        bad = self.tmp / "bad.sqlite3.partial"
        bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
        with self.assertRaises(sqlite3.Error):
            backup._verify_snapshot(bad)

    def test_non_database_source_fails_cleanly(self):
        source = self.tmp / "source.sqlite3"
        source.write_bytes(b"this is not a database" * 100)
        config = make_config(self.tmp)
        with self.assertRaises(BackupError):
            run_local_backup(config)
        self.assertEqual(list(config.backup_dir.glob("db-*")), [])

    def test_missing_database_is_an_error_without_side_effects(self):
        config = make_config(self.tmp)
        with self.assertRaises(BackupError):
            run_local_backup(config)
        self.assertFalse(config.backup_dir.exists())

    def test_stale_partial_files_are_cleaned_up_and_never_listed_or_pruned_as_backups(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp)
        config.backup_dir.mkdir()
        stale = config.backup_dir / "db-20200101-000000.sqlite3.partial"
        stale.write_bytes(b"half")
        run_local_backup(config)
        self.assertFalse(stale.exists())


class LockTests(TmpDirCase):
    def test_second_backup_waits_out_or_refuses_while_lock_is_held(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp)
        config.backup_dir.mkdir()
        with file_lock(config.backup_dir / ".backup.lock"):
            with self.assertRaises(LockBusy):
                run_local_backup(config, lock_wait=0)
        self.assertEqual(list(config.backup_dir.glob("db-*")), [])
        self.assertTrue(run_local_backup(config, lock_wait=0).exists())

    def test_lock_is_exclusive_across_processes_and_freed_when_holder_dies(self):
        lock_path = self.tmp / "x.lock"
        code = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from pathlib import Path\n"
            "from core.backup import file_lock\n"
            f"with file_lock(Path({str(lock_path)!r})):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(60)\n"
        )
        holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(LockBusy):
                with file_lock(lock_path):
                    pass
        finally:
            holder.kill()
            holder.wait()
        with file_lock(lock_path):  # the OS released it with the process
            pass

    def test_concurrent_state_updates_do_not_lose_writes(self):
        directory = self.tmp / "backups"
        update_state(directory, lambda s: s.update(local_failures=0))

        def bump():
            for _ in range(20):
                update_state(directory, lambda s: s.update(local_failures=s["local_failures"] + 1))

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(load_state(directory)["local_failures"], 80)


class StateTests(TmpDirCase):
    def test_state_survives_a_restart(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp)
        path = run_local_backup(config, clock=make_clock(T0))
        reloaded = load_state(config.backup_dir)  # a fresh process would do exactly this
        self.assertEqual(reloaded["last_local_success_at"], backup.format_ts(T0))
        self.assertEqual(reloaded["last_local_file"], path.name)

    def test_corrupt_state_is_quarantined_and_treated_as_empty(self):
        directory = self.tmp / "backups"
        directory.mkdir()
        (directory / backup.STATE_FILE).write_text("{not json")
        state = load_state(directory)
        self.assertIsNone(state["last_local_success_at"])
        self.assertTrue((directory / (backup.STATE_FILE + ".corrupt")).exists())

    def test_tampered_state_values_are_rejected(self):
        directory = self.tmp / "backups"
        directory.mkdir()
        (directory / backup.STATE_FILE).write_text(
            '{"pending_uploads": ["../../etc/passwd", "/abs/db-20200101-000000.sqlite3",'
            ' "db-20200101-000000.sqlite3", 7, "db-20200101-000000.sqlite3"],'
            ' "last_local_success_at": "yesterday", "local_failures": -3,'
            ' "last_remote_sha256": "nothex"}'
        )
        state = load_state(directory)
        self.assertEqual(state["pending_uploads"], ["db-20200101-000000.sqlite3"])
        self.assertIsNone(state["last_local_success_at"])
        self.assertEqual(state["local_failures"], 0)
        self.assertIsNone(state["last_remote_sha256"])

    def test_state_write_is_atomic_json(self):
        directory = self.tmp / "backups"
        update_state(directory, lambda s: s.update(last_local_error="x"))
        self.assertEqual(
            sorted(p.name for p in directory.iterdir() if not p.name.startswith(".state")),
            [backup.STATE_FILE],
        )


class PruneTests(TmpDirCase):
    def test_pending_uploads_are_protected_beyond_the_keep_limit(self):
        directory = self.tmp / "backups"
        directory.mkdir()
        names = [f"db-2020010{i}-000000.sqlite3" for i in range(1, 6)]
        for name in names:
            (directory / name).write_bytes(b"x")
        new = directory / "db-20200106-000000.sqlite3"
        new.write_bytes(b"x")

        backup.prune_backups(directory, new, keep=2, protected={names[0]})

        remaining = sorted(p.name for p in directory.glob("db-*"))
        # newest other + new survive; the protected oldest survives too.
        self.assertEqual(remaining, [names[0], names[4], new.name])

    def test_pending_queue_is_bounded_but_keeps_the_newest(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        remote = _fake_remote()
        config = make_config(self.tmp, remote=remote, remote_enabled=True, keep=1)
        clock = make_clock()
        last = None
        for _ in range(backup.MAX_PENDING_UPLOADS + 3):
            clock.box["now"] += timedelta(hours=1)
            last = run_local_backup(config, clock=clock)
        pending = load_state(config.backup_dir)["pending_uploads"]
        self.assertEqual(len(pending), backup.MAX_PENDING_UPLOADS)
        self.assertEqual(pending[-1], last.name)
        self.assertTrue((config.backup_dir / pending[0]).exists())

    def test_nothing_is_queued_when_remote_is_off(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        config = make_config(self.tmp)
        run_local_backup(config)
        self.assertEqual(load_state(config.backup_dir)["pending_uploads"], [])


def _fake_remote():
    from core.backup_remote import RemoteConfig

    return RemoteConfig.from_values(
        region="eu-central-1",
        bucket="bucket-name",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secretsecretsecret",
    )


class DueLogicTests(SimpleTestCase):
    config = BackupConfig(
        db_path=Path("/x/db"),
        backup_dir=Path("/x/b"),
        interval_seconds=24 * 3600,
        retry_seconds=900,
    )

    def state(self, **kw):
        base = backup._empty_state()
        base.update(kw)
        return base

    def test_due_when_never_succeeded(self):
        self.assertTrue(local_due(self.state(), self.config, T0))

    def test_not_due_before_the_interval_and_due_exactly_at_it(self):
        state = self.state(last_local_success_at=backup.format_ts(T0))
        self.assertFalse(local_due(state, self.config, T0 + timedelta(hours=23, minutes=59)))
        self.assertTrue(local_due(state, self.config, T0 + timedelta(hours=24)))

    def test_long_suspension_is_simply_overdue(self):
        state = self.state(last_local_success_at=backup.format_ts(T0))
        self.assertTrue(local_due(state, self.config, T0 + timedelta(days=9)))

    def test_future_success_timestamp_means_clock_went_back_so_back_up(self):
        state = self.state(last_local_success_at=backup.format_ts(T0 + timedelta(days=30)))
        self.assertTrue(local_due(state, self.config, T0))

    def test_small_clock_skew_is_tolerated(self):
        state = self.state(last_local_success_at=backup.format_ts(T0 + timedelta(minutes=2)))
        self.assertFalse(local_due(state, self.config, T0))

    def test_failure_backoff_grows_then_caps_at_the_interval(self):
        old = backup.format_ts(T0 - timedelta(days=3))
        attempt = backup.format_ts(T0)
        for failures, wait in ((1, 900), (2, 1800), (3, 3600)):
            state = self.state(
                last_local_success_at=old, last_local_attempt_at=attempt, local_failures=failures
            )
            self.assertFalse(local_due(state, self.config, T0 + timedelta(seconds=wait - 1)))
            self.assertTrue(local_due(state, self.config, T0 + timedelta(seconds=wait)))
        capped = self.state(
            last_local_success_at=old, last_local_attempt_at=attempt, local_failures=50
        )
        self.assertTrue(local_due(capped, self.config, T0 + timedelta(hours=24)))
        self.assertFalse(local_due(capped, self.config, T0 + timedelta(hours=23)))


class ConfigParsingTests(SimpleTestCase):
    def build(self, **settings_values):
        with override_settings(**settings_values):
            return BackupConfig.from_settings()

    def test_defaults_are_safe(self):
        config = self.build()
        self.assertTrue(config.scheduler_enabled)
        self.assertEqual(config.interval_seconds, 24 * 3600)
        self.assertEqual(config.keep, 7)
        self.assertEqual(config.check_seconds, 60)
        self.assertFalse(config.remote_enabled)
        self.assertIsNone(config.remote)

    def test_check_interval_can_never_exceed_sixty_seconds(self):
        config = self.build(BACKUP_CHECK_SECONDS="3600")
        self.assertEqual(config.check_seconds, 60)
        self.assertTrue(config.warnings)

    def test_garbage_numbers_fall_back_to_defaults_with_a_warning(self):
        config = self.build(
            BACKUP_INTERVAL_HOURS="soon",
            BACKUP_KEEP_LOCAL="nan",
            BACKUP_RETRY_SECONDS="-5",
            BACKUP_SCHEDULER_ENABLED="maybe",
        )
        self.assertEqual(config.interval_seconds, 24 * 3600)
        self.assertEqual(config.keep, 7)
        self.assertEqual(config.retry_seconds, 60)
        self.assertTrue(config.scheduler_enabled)
        self.assertEqual(len(config.warnings), 4)

    def test_keep_zero_is_not_allowed_for_the_timer(self):
        self.assertEqual(self.build(BACKUP_KEEP_LOCAL="0").keep, 1)

    def test_remote_stays_off_without_the_explicit_flag_even_if_fully_configured(self):
        config = self.build(
            BACKUP_S3_REGION="eu-central-1",
            BACKUP_S3_BUCKET="bucket-name",
            BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
            BACKUP_S3_SECRET_ACCESS_KEY="secretsecretsecret",
        )
        self.assertIsNone(config.remote)
        self.assertFalse(config.remote_enabled)

    def test_enabled_but_incomplete_remote_is_reported_without_values(self):
        config = self.build(BACKUP_REMOTE_ENABLED="true", BACKUP_S3_BUCKET="bucket-name")
        self.assertIsNone(config.remote)
        self.assertTrue(config.remote_enabled)
        self.assertIn("BACKUP_S3_REGION", config.remote_error)
        self.assertNotIn("bucket-name", config.remote_error)

    def test_complete_remote_is_accepted(self):
        config = self.build(
            BACKUP_REMOTE_ENABLED="yes",
            BACKUP_S3_REGION="eu-central-1",
            BACKUP_S3_BUCKET="bucket-name",
            BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
            BACKUP_S3_SECRET_ACCESS_KEY="secretsecretsecret",
        )
        self.assertIsNotNone(config.remote)
        self.assertEqual(config.remote_error, "")


class RestoreDrillTests(TmpDirCase):
    def snapshot(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        return run_local_backup(make_config(self.tmp))

    def test_restores_into_a_new_database_and_reports_checks(self):
        snap = self.snapshot()
        digest = hashlib.sha256(snap.read_bytes()).hexdigest()
        target = self.tmp / "scratch" / "restored.sqlite3"
        report = backup.restore_to_scratch(
            snap, target, protected_paths=[self.tmp / "source.sqlite3"]
        )
        self.assertTrue(report["integrity_ok"])
        self.assertEqual(report["foreign_key_violations"], 0)
        self.assertEqual(report["tables"], {"widgets": 3})
        self.assertTrue(target.exists())
        # The snapshot is opened read-only and immutable.
        self.assertEqual(hashlib.sha256(snap.read_bytes()).hexdigest(), digest)

    def test_refuses_to_overwrite_an_existing_file(self):
        snap = self.snapshot()
        target = self.tmp / "existing.sqlite3"
        target.write_bytes(b"owner data")
        with self.assertRaises(BackupError):
            backup.restore_to_scratch(snap, target)
        self.assertEqual(target.read_bytes(), b"owner data")

    def test_refuses_the_live_database_even_through_a_symlink(self):
        snap = self.snapshot()
        live = self.tmp / "source.sqlite3"
        live_bytes = live.read_bytes()
        link = self.tmp / "link.sqlite3"
        link.symlink_to(live)
        for target in (live, link):
            with self.assertRaises(BackupError):
                backup.restore_to_scratch(snap, target, protected_paths=[live])
        self.assertEqual(live.read_bytes(), live_bytes)

    def test_refuses_to_restore_over_the_backup_itself(self):
        snap = self.snapshot()
        with self.assertRaises(BackupError):
            backup.restore_to_scratch(snap, snap)

    def test_a_damaged_snapshot_fails_loudly_and_leaves_no_scratch_file(self):
        snap = self.tmp / "damaged.sqlite3"
        conn = make_wal_db(snap, rows=500)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        data = bytearray(snap.read_bytes())
        data[4096 * 2 : 4096 * 2 + 64] = b"\xff" * 64
        snap.write_bytes(bytes(data))
        target = self.tmp / "restored.sqlite3"
        with self.assertRaises(BackupError):
            backup.restore_to_scratch(snap, target)
        self.assertFalse(target.exists())

    def test_command_fails_when_integrity_check_does_not_pass(self):
        snap = self.snapshot()
        failing = {
            "target": "x",
            "integrity_ok": False,
            "integrity_messages": ["row 3 missing from index i"],
            "foreign_key_violations": 0,
            "tables": {},
        }
        out = StringIO()
        with (
            override_settings(
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": str(self.tmp / "source.sqlite3"),
                    }
                }
            ),
            mock.patch(
                "core.management.commands.restore_backup_drill.restore_to_scratch",
                return_value=failing,
            ),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "restore_backup_drill",
                    str(self.tmp / "t.sqlite3"),
                    "--backup",
                    str(snap),
                    stdout=out,
                )
        self.assertIn("FAILED", out.getvalue())
        self.assertIn("row 3 missing from index i", out.getvalue())

    def test_command_restores_latest_snapshot_into_scratch_and_checks_it(self):
        self.snapshot()
        target = self.tmp / "drill.sqlite3"
        with override_settings(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.tmp / "source.sqlite3"),
                }
            }
        ):
            out = StringIO()
            call_command("restore_backup_drill", str(target), stdout=out)
            self.assertIn("integrity_check: ok", out.getvalue())
            with self.assertRaises(CommandError):
                call_command("restore_backup_drill", str(self.tmp / "source.sqlite3"))
            with self.assertRaises(CommandError):
                call_command("restore_backup_drill", str(target))  # already exists


@unittest.skipIf(sys.platform == "win32", "flock is POSIX only")
class CommandIntegrationTests(TmpDirCase):
    def settings_for(self, **extra):
        return override_settings(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.tmp / "source.sqlite3"),
                }
            },
            **extra,
        )

    def test_backup_database_records_state_so_the_timer_does_not_repeat_it(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        with self.settings_for():
            call_command("backup_database", stdout=StringIO())
            state = load_state(self.tmp / "backups")
            self.assertIsNotNone(state["last_local_success_at"])
            config = BackupConfig.from_settings()
            self.assertFalse(local_due(state, config, datetime.now(UTC)))

    def test_backup_database_default_keep_comes_from_settings(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        out = self.tmp / "backups"
        out.mkdir()
        for day in range(1, 5):
            (out / f"db-2020010{day}-000000.sqlite3").write_bytes(b"stub")
        with self.settings_for(BACKUP_KEEP_LOCAL="2"):
            call_command("backup_database", stdout=StringIO())
        self.assertEqual(len(list(out.glob("db-*.sqlite3"))), 2)

    def test_backup_database_failure_is_a_command_error(self):
        source = self.tmp / "source.sqlite3"
        source.write_bytes(b"junk" * 1000)
        with self.settings_for():
            with self.assertRaises(CommandError):
                call_command("backup_database", stdout=StringIO())

    def test_backup_status_check_flags_overdue_and_json_hides_secrets(self):
        make_wal_db(self.tmp / "source.sqlite3").close()
        with self.settings_for(
            BACKUP_REMOTE_ENABLED="true",
            BACKUP_S3_REGION="eu-central-1",
            BACKUP_S3_BUCKET="bucket-name",
            BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
            BACKUP_S3_SECRET_ACCESS_KEY="top-secret-value-123",
            BACKUP_S3_SESSION_TOKEN="session-token-value-456",
        ):
            with self.assertRaises(CommandError):
                call_command("backup_status", "--check", stdout=StringIO())  # never backed up
            call_command("backup_database", stdout=StringIO())
            out = StringIO()
            call_command("backup_status", "--json", stdout=out)
            self.assertNotIn("top-secret-value-123", out.getvalue())
            self.assertNotIn("session-token-value-456", out.getvalue())
            self.assertNotIn("AKIDEXAMPLE", out.getvalue())
            call_command("backup_status", "--check", stdout=StringIO())  # fresh, none failing
