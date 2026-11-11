import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core import backup, backup_scheduler
from core.backup import BackupConfig, file_lock, load_state
from core.backup_remote import RemoteConfig, UploadError, UploadResult
from core.backup_scheduler import run_scheduler, run_tick

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = REPO_ROOT / "temp"
TEMP_ROOT.mkdir(exist_ok=True)

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
KEY_ID = "AKIDEXAMPLE0123456"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
TOKEN = "IQoJb3JpZ2luX2VjEXAMPLETOKEN"


class FakeClock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class FakeUploader:
    """Stands in for core.backup_remote.upload_file: no network, scriptable failures."""

    def __init__(self):
        self.calls = []
        self.fail_with = None

    def __call__(self, config, path, key, **kwargs):
        self.calls.append((Path(path).name, key))
        if self.fail_with is not None:
            raise self.fail_with
        return UploadResult(key=key, sha256="ab" * 32, size=Path(path).stat().st_size)


def remote_config():
    return RemoteConfig.from_values(
        region="eu-central-1",
        bucket="my-flashcards-backups",
        prefix="prod",
        access_key_id=KEY_ID,
        secret_access_key=SECRET,
        session_token=TOKEN,
    )


class SchedulerCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        conn = sqlite3.connect(str(self.tmp / "source.sqlite3"))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.clock = FakeClock()
        self.uploader = FakeUploader()

    def config(self, **overrides):
        values = dict(
            db_path=self.tmp / "source.sqlite3",
            backup_dir=self.tmp / "backups",
            interval_seconds=24 * 3600,
            retry_seconds=900,
            keep=3,
            check_seconds=60,
        )
        values.update(overrides)
        return BackupConfig(**values)

    def remote(self, **overrides):
        return self.config(remote=remote_config(), remote_enabled=True, **overrides)

    def tick(self, config, **kw):
        return run_tick(config, clock=self.clock, upload=self.uploader, **kw)

    def backups(self, config):
        return sorted(p.name for p in config.backup_dir.glob("db-*.sqlite3"))


class TimerTests(SchedulerCase):
    def test_first_check_backs_up_then_waits_for_the_interval(self):
        config = self.config()
        first = self.tick(config)
        self.assertTrue(first.local_attempted and first.local_path)
        self.clock.advance(hours=23, minutes=59)
        self.assertFalse(self.tick(config).local_attempted)
        self.clock.advance(minutes=1)
        self.assertTrue(self.tick(config).local_attempted)
        self.assertEqual(len(self.backups(config)), 2)

    def test_timestamps_persist_across_a_process_restart(self):
        config = self.config()
        self.tick(config)
        # A brand-new "process": nothing in memory, only the state file.
        self.clock.advance(hours=1)
        self.assertFalse(self.tick(config).local_attempted)
        self.clock.advance(hours=23)
        self.assertTrue(self.tick(config).local_attempted)

    def test_catch_up_after_suspension_takes_exactly_one_backup(self):
        config = self.config()
        self.tick(config)
        self.clock.advance(days=6)  # machine slept; wall clock jumped
        self.assertTrue(self.tick(config).local_attempted)
        self.assertFalse(self.tick(config).local_attempted)
        self.assertEqual(len(self.backups(config)), 2)

    def test_backup_taken_just_before_a_restart_is_not_repeated(self):
        config = self.config()
        backup.run_local_backup(config, clock=self.clock)  # the entrypoint's boot backup
        self.clock.advance(seconds=30)
        self.assertFalse(self.tick(config).local_attempted)

    def test_clock_set_backwards_does_not_postpone_backups_forever(self):
        config = self.config()
        self.clock.now = T0 + timedelta(days=40)
        self.tick(config)
        self.clock.now = T0  # fixed clock now far behind the recorded success
        self.assertTrue(self.tick(config).local_attempted)

    def test_failure_is_retried_with_backoff_and_a_later_success_clears_it(self):
        config = self.config()
        self.tick(config)
        self.clock.advance(hours=24)
        with mock.patch.object(
            backup, "_copy_database", side_effect=sqlite3.OperationalError("disk full")
        ):
            failed = self.tick(config)
            self.assertIn("disk full", failed.local_error)
            self.clock.advance(minutes=14)
            self.assertFalse(self.tick(config).local_attempted)  # still backing off
            self.clock.advance(minutes=1)
            self.assertIn("disk full", self.tick(config).local_error)  # retried, failed again
            self.assertEqual(load_state(config.backup_dir)["local_failures"], 2)
            self.clock.advance(minutes=29)
            self.assertFalse(self.tick(config).local_attempted)  # doubled backoff
        self.clock.advance(minutes=1)
        ok = self.tick(config)
        self.assertIsNone(ok.local_error)
        state = load_state(config.backup_dir)
        self.assertEqual(state["local_failures"], 0)
        self.assertIsNone(state["last_local_error"])
        self.assertEqual(len(self.backups(config)), 2)

    def test_missing_database_is_waited_for_not_counted_as_a_failure(self):
        (self.tmp / "source.sqlite3").unlink()
        config = self.config()
        result = self.tick(config)
        self.assertFalse(result.local_attempted)
        self.assertEqual(load_state(config.backup_dir)["local_failures"], 0)
        self.assertFalse(config.backup_dir.exists())

    def test_busy_backup_lock_skips_quietly_and_retries_next_check(self):
        config = self.config()
        config.backup_dir.mkdir()
        with file_lock(config.backup_dir / ".backup.lock"):
            result = self.tick(config)
        self.assertTrue(result.skipped_busy)
        self.assertEqual(load_state(config.backup_dir)["local_failures"], 0)
        self.assertTrue(self.tick(config).local_path)

    def test_force_backs_up_even_when_not_due(self):
        config = self.config()
        self.tick(config)
        self.assertTrue(self.tick(config, force=True).local_path)

    def test_local_retention_keeps_the_configured_number(self):
        config = self.config(keep=3)
        for _ in range(6):
            self.tick(config)
            self.clock.advance(hours=24)
        self.assertEqual(len(self.backups(config)), 3)


class OffsiteTests(SchedulerCase):
    def test_remote_disabled_never_uploads_or_queues(self):
        config = self.config()
        self.tick(config)
        self.assertEqual(self.uploader.calls, [])
        self.assertEqual(load_state(config.backup_dir)["pending_uploads"], [])

    def test_successful_upload_records_proof_and_clears_the_queue(self):
        config = self.remote()
        result = self.tick(config)
        name = result.local_path.name
        self.assertEqual(self.uploader.calls, [(name, f"prod/{name}")])
        state = load_state(config.backup_dir)
        self.assertEqual(state["pending_uploads"], [])
        self.assertEqual(state["last_remote_success_at"], backup.format_ts(self.clock.now))
        self.assertEqual(state["last_remote_file"], name)
        self.assertEqual(state["last_remote_key"], f"prod/{name}")
        self.assertEqual(state["last_remote_sha256"], "ab" * 32)
        status = backup.describe_status(config, state, self.clock.now)
        self.assertEqual(status["remote"], "enabled")

    def test_failed_upload_never_claims_offsite_success(self):
        config = self.remote()
        self.uploader.fail_with = UploadError("curl exited with status 6")
        result = self.tick(config)
        # The local backup stands on its own...
        self.assertIsNotNone(result.local_path)
        self.assertIsNone(result.local_error)
        # ...but nothing off-site is claimed.
        state = load_state(config.backup_dir)
        self.assertIsNone(state["last_remote_success_at"])
        self.assertIsNone(state["last_remote_file"])
        self.assertEqual(state["pending_uploads"], [result.local_path.name])
        self.assertEqual(state["remote_failures"], 1)
        self.assertIn("status 6", state["last_remote_error"])
        self.assertIsNone(state["last_local_error"])

    def test_unexpected_transport_bug_is_contained_and_treated_as_failure(self):
        config = self.remote()
        self.uploader.fail_with = RuntimeError(f"boom with {SECRET}")
        result = self.tick(config)
        self.assertIsNotNone(result.local_path)
        state = load_state(config.backup_dir)
        self.assertIsNone(state["last_remote_success_at"])
        self.assertNotIn(SECRET, state["last_remote_error"])
        self.assertNotIn(SECRET, result.upload_error)

    def test_pending_backup_survives_pruning_until_it_is_uploaded(self):
        config = self.remote(keep=1)
        self.uploader.fail_with = UploadError("down")
        first = self.tick(config).local_path
        for _ in range(3):
            self.clock.advance(hours=24)
            self.tick(config)
        # keep=1 would normally leave a single file; the never-uploaded ones stay.
        self.assertIn(first.name, self.backups(config))
        self.assertEqual(len(self.backups(config)), 4)

        self.uploader.fail_with = None
        self.clock.advance(days=1)
        result = self.tick(config)
        # Oldest first, and the whole queue drains in one check once reachable.
        self.assertEqual(result.uploaded[0], first.name)
        self.assertEqual(len(result.uploaded), 5)
        self.assertEqual(load_state(config.backup_dir)["pending_uploads"], [])
        self.clock.advance(days=1)
        self.tick(config)  # next prune can now release them
        self.assertEqual(len(self.backups(config)), 1)

    def test_upload_retries_back_off_independently_of_the_local_timer(self):
        config = self.remote()
        self.uploader.fail_with = UploadError("down")
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 1)
        self.clock.advance(minutes=10)
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 1)  # still backing off
        self.clock.advance(minutes=5)
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 2)
        self.clock.advance(minutes=29)
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 2)  # doubled
        self.uploader.fail_with = None
        self.clock.advance(minutes=1)
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 3)
        state = load_state(config.backup_dir)
        self.assertEqual(state["remote_failures"], 0)
        self.assertIsNotNone(state["last_remote_success_at"])

    def test_upload_failure_stops_the_queue_and_keeps_the_remaining_entries(self):
        config = self.remote(keep=5)
        self.uploader.fail_with = UploadError("down")
        for _ in range(3):
            self.tick(config)
            self.clock.advance(hours=24)
        self.assertEqual(len(load_state(config.backup_dir)["pending_uploads"]), 3)
        self.uploader.calls.clear()
        self.clock.advance(hours=1)
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 1)  # stopped at the first failure

    def test_pending_file_deleted_by_hand_is_dropped_without_claiming_success(self):
        config = self.remote()
        self.uploader.fail_with = UploadError("down")
        name = self.tick(config).local_path.name
        (config.backup_dir / name).unlink()
        self.uploader.fail_with = None
        self.clock.advance(hours=1)
        self.tick(config)
        state = load_state(config.backup_dir)
        self.assertEqual(state["pending_uploads"], [])
        self.assertIsNone(state["last_remote_success_at"])

    def test_misconfigured_remote_keeps_local_backups_running_and_uploads_nothing(self):
        config = self.config(
            remote=None, remote_enabled=True, remote_error="missing BACKUP_S3_BUCKET"
        )
        with self.assertLogs("core.backup_scheduler", "ERROR") as logs:
            result = self.tick(config)
        self.assertTrue(result.local_path)
        self.assertEqual(self.uploader.calls, [])
        self.assertIn("BACKUP_S3_BUCKET", "\n".join(logs.output))
        status = backup.describe_status(config, load_state(config.backup_dir), self.clock.now)
        self.assertEqual(status["remote"], "misconfigured")
        self.assertIsNone(status["last_remote_success_at"])

    def test_concurrent_uploader_process_is_respected(self):
        config = self.remote()
        config.backup_dir.mkdir()
        with file_lock(config.backup_dir / ".upload.lock"):
            self.tick(config)
        self.assertEqual(self.uploader.calls, [])
        self.tick(config)
        self.assertEqual(len(self.uploader.calls), 1)

    def test_logs_and_state_never_contain_credentials(self):
        config = self.remote()
        self.uploader.fail_with = UploadError(
            br_redact(f"curl: (22) denied {KEY_ID} {SECRET} {TOKEN}", config)
        )
        with self.assertLogs("core", "DEBUG") as logs:
            self.tick(config)
            self.uploader.fail_with = RuntimeError(f"{SECRET} {TOKEN} {KEY_ID}")
            self.clock.advance(hours=2)
            self.tick(config)
        text = "\n".join(logs.output) + (config.backup_dir / backup.STATE_FILE).read_text()
        for secret in (KEY_ID, SECRET, TOKEN):
            self.assertNotIn(secret, text)


def br_redact(text, config):
    from core.backup_remote import redact

    return redact(text, config.remote.secrets())


class LoopTests(SchedulerCase):
    def run_loop(self, config, ticks, **kw):
        stop = threading.Event()
        waits = []

        def wait(seconds):
            waits.append(seconds)
            self.clock.advance(seconds=seconds)
            if len(waits) >= ticks:
                stop.set()

        code = run_scheduler(
            config, stop_event=stop, clock=self.clock, upload=self.uploader, wait=wait, **kw
        )
        return code, waits

    def test_loop_checks_every_check_interval_and_catches_up_without_a_restart(self):
        config = self.config(check_seconds=60)
        code, waits = self.run_loop(config, ticks=3)
        self.assertEqual(code, 0)
        self.assertEqual(waits, [60, 60, 60])
        self.assertEqual(len(self.backups(config)), 1)

        # Suspension: the next check sees the jumped wall clock.
        self.clock.advance(days=5)
        self.run_loop(config, ticks=1)
        self.assertEqual(len(self.backups(config)), 2)

    def test_loop_survives_a_tick_that_raises(self):
        config = self.config()
        with mock.patch.object(
            backup_scheduler, "run_tick", side_effect=[RuntimeError("unexpected"), None, None]
        ) as tick:
            with self.assertLogs("core.backup_scheduler", "ERROR") as logs:
                code, waits = self.run_loop(config, ticks=3)
        self.assertEqual(code, 0)
        self.assertEqual(tick.call_count, 3)
        self.assertIn("unexpected", "\n".join(logs.output))

    def test_second_scheduler_does_not_start_while_one_is_running(self):
        config = self.config()
        config.backup_dir.mkdir()
        with file_lock(config.backup_dir / ".scheduler.lock"):
            with self.assertLogs("core.backup_scheduler", "INFO") as logs:
                code, waits = self.run_loop(config, ticks=1)
        self.assertEqual(code, 0)
        self.assertEqual(waits, [])
        self.assertEqual(self.backups(config), [])
        self.assertIn("already running", "\n".join(logs.output))
        # ...and the lock is released afterwards so a successor can run.
        code, waits = self.run_loop(config, ticks=1)
        self.assertEqual(waits, [60])

    def test_loop_exits_when_the_parent_process_is_gone(self):
        config = self.config()
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        code, waits = self.run_loop(config, ticks=5, parent_pid=gone.pid)
        self.assertEqual(code, 0)
        self.assertEqual(waits, [])
        code, waits = self.run_loop(config, ticks=2, parent_pid=os.getpid())
        self.assertEqual(len(waits), 2)

    def test_loop_stops_promptly_when_asked(self):
        config = self.config()
        stop = threading.Event()
        stop.set()
        self.assertEqual(run_scheduler(config, stop_event=stop, clock=self.clock), 0)
        self.assertEqual(self.backups(config), [])

    def test_system_exit_from_a_signal_handler_is_not_swallowed(self):
        config = self.config()
        with mock.patch.object(backup_scheduler, "run_tick", side_effect=SystemExit(0)):
            with self.assertRaises(SystemExit):
                self.run_loop(config, ticks=2)
        # The scheduler lock was released on the way out.
        with file_lock(config.backup_dir / ".scheduler.lock"):
            pass


class RealProcessTests(SchedulerCase):
    """The shipped entry point (`python -m core.backup_scheduler`) as a real process."""

    def env(self, **extra):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "DJANGO_DB_PATH": str(self.tmp / "source.sqlite3"),
            "DJANGO_DEBUG": "true",
            "BACKUP_CHECK_SECONDS": "5",
            "VIDEO_STUDY_TRANSCRIPT_PREFETCH": "false",
        }
        env.update(extra)
        return env

    def start(self, *args, **env):
        return subprocess.Popen(
            [sys.executable, "-m", "core.backup_scheduler", *args],
            cwd=REPO_ROOT,
            env=self.env(**env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        return False

    def test_runs_a_backup_rejects_a_duplicate_and_exits_on_sigterm(self):
        backup_dir = self.tmp / "backups"
        proc = self.start()
        try:
            self.assertTrue(
                self.wait_for(lambda: list(backup_dir.glob("db-*.sqlite3"))),
                "no backup appeared",
            )
            self.assertTrue(self.wait_for(lambda: (backup_dir / backup.STATE_FILE).exists()))
            duplicate = self.start()
            out, _ = duplicate.communicate(timeout=20)
            self.assertEqual(duplicate.returncode, 0)
            self.assertIn("already running", out)
            self.assertIsNone(proc.poll())
        finally:
            proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(len(list(backup_dir.glob("db-*.sqlite3"))), 1)
        self.assertFalse(list(backup_dir.glob("*.partial")))
        with file_lock(backup_dir / ".scheduler.lock"):  # lock released on exit
            pass

    def test_exits_by_itself_when_its_parent_dies(self):
        backup_dir = self.tmp / "backups"
        parent_code = (
            "import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-m', 'core.backup_scheduler',"
            " '--parent-pid', str(__import__('os').getpid())])\n"
            "print(child.pid, flush=True)\n"
            "import time; time.sleep(60)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            cwd=REPO_ROOT,
            env=self.env(),
            stdout=subprocess.PIPE,
            text=True,
        )
        child_pid = int(parent.stdout.readline())
        try:
            self.assertTrue(self.wait_for(lambda: list(backup_dir.glob("db-*.sqlite3"))))
            parent.kill()
            parent.wait()
            self.assertTrue(
                self.wait_for(lambda: not _alive(child_pid), timeout=30),
                "scheduler outlived its parent",
            )
        finally:
            parent.kill()
            with _suppress():
                os.kill(child_pid, signal.SIGKILL)

    def test_disabled_scheduler_exits_immediately_and_writes_nothing(self):
        proc = self.start(BACKUP_SCHEDULER_ENABLED="false")
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse((self.tmp / "backups").exists())

    def test_once_flag_runs_a_single_check_and_reports_failure(self):
        proc = self.start("--once")
        proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(list((self.tmp / "backups").glob("db-*.sqlite3"))), 1)
        (self.tmp / "source.sqlite3").write_bytes(b"not a database" * 100)
        proc = self.start("--once", "--force")
        _, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 1)

    def test_remote_secrets_from_the_environment_never_reach_the_log(self):
        proc = self.start(
            "--once",
            BACKUP_REMOTE_ENABLED="true",
            BACKUP_S3_REGION="eu-central-1",
            BACKUP_S3_BUCKET="my-flashcards-backups",
            BACKUP_S3_ENDPOINT="https://127.0.0.1:1",  # refused locally; never leaves the machine
            BACKUP_S3_ACCESS_KEY_ID=KEY_ID,
            BACKUP_S3_SECRET_ACCESS_KEY=SECRET,
            BACKUP_S3_SESSION_TOKEN=TOKEN,
            BACKUP_UPLOAD_TIMEOUT_SECONDS="10",
        )
        out, _ = proc.communicate(timeout=60)
        for secret in (KEY_ID, SECRET, TOKEN):
            self.assertNotIn(secret, out)
        state = load_state(self.tmp / "backups")
        self.assertIsNone(state["last_remote_success_at"])


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); ask ps whether it is actually running.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


class EntrypointTests(SimpleTestCase):
    script = (REPO_ROOT / "scripts" / "docker-entrypoint.sh").read_text()

    def test_script_is_valid_bash(self):
        proc = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "scripts" / "docker-entrypoint.sh")], capture_output=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_boot_backup_runs_before_migrate_and_the_timer_starts_after_it(self):
        boot = self.script.index("manage.py backup_database")
        migrate = self.script.index("manage.py migrate")
        timer = self.script.index("core.backup_scheduler")
        serve = self.script.index("exec gunicorn")
        self.assertLess(boot, migrate)
        self.assertLess(migrate, timer)
        self.assertLess(timer, serve)

    def test_timer_is_backgrounded_once_and_cannot_stop_the_server(self):
        match = re.search(
            r"^\{ python -m core\.backup_scheduler .*?\} &$", self.script, re.M | re.S
        )
        self.assertIsNotNone(match, "scheduler must run as a background group")
        group = match.group(0)
        self.assertIn('--parent-pid "$$"', group)
        self.assertIn("||", group)  # a crash is logged, not fatal under `set -e`
        self.assertEqual(
            len(re.findall(r"^\{ python -m core\.backup_scheduler", self.script, re.M)), 1
        )
        self.assertNotIn("exec python", self.script)

    def test_boot_backup_failure_still_does_not_abort_startup(self):
        self.assertRegex(
            self.script, r"backup_database[^\n]*\\\n\s+\|\| echo \"WARNING: database backup failed"
        )


class StatusCommandTests(SchedulerCase):
    def test_overdue_and_failing_remote_make_check_fail_and_text_is_honest(self):
        db = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(self.tmp / "source.sqlite3"),
            }
        }
        with override_settings(
            DATABASES=db,
            BACKUP_REMOTE_ENABLED="true",
            BACKUP_S3_REGION="eu-central-1",
            BACKUP_S3_BUCKET="my-flashcards-backups",
            BACKUP_S3_ACCESS_KEY_ID=KEY_ID,
            BACKUP_S3_SECRET_ACCESS_KEY=SECRET,
        ):
            config = BackupConfig.from_settings()
            uploader = FakeUploader()
            uploader.fail_with = UploadError("curl exited with status 7")
            run_tick(config, upload=uploader)
            out = StringIO()
            with self.assertRaises(CommandError) as ctx:
                call_command("backup_status", "--check", stdout=out)
            self.assertIn("off-site", str(ctx.exception))
            self.assertIn("last_remote_success_at: None", out.getvalue())
            self.assertIn("status 7", out.getvalue())
            for secret in (KEY_ID, SECRET):
                self.assertNotIn(secret, out.getvalue())
