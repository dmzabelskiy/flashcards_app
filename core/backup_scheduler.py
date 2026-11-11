"""One-process backup timer: periodic snapshots plus opt-in off-site upload.

Run as `python -m core.backup_scheduler` (the container entrypoint does) or
`python manage.py backup_scheduler`. The loop wakes at least every
`BACKUP_CHECK_SECONDS` (at most 60) and compares the wall clock with the
success timestamps persisted in `backup-state.json`; it never counts elapsed
sleeps. A machine that was suspended therefore notices an overdue backup on
the first check after it resumes, and a restarted process picks up where the
last one left off. A lock file keeps a second timer process from running.
"""

import argparse
import logging
import os
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.backup import (
    BackupConfig,
    BackupError,
    Clock,
    LockBusy,
    describe_status,
    file_lock,
    format_ts,
    load_state,
    local_due,
    remote_retry_blocked,
    run_local_backup,
    update_state,
    utcnow,
)
from core.backup_remote import UploadError, redact, upload_file

logger = logging.getLogger(__name__)

Uploader = Callable[..., object]


@dataclass
class TickResult:
    local_attempted: bool = False
    local_path: Path | None = None
    local_error: str | None = None
    uploaded: list[str] = field(default_factory=list)
    upload_error: str | None = None
    skipped_busy: bool = False


def run_tick(
    config: BackupConfig,
    *,
    clock: Clock = utcnow,
    force: bool = False,
    upload: Uploader = upload_file,
) -> TickResult:
    """Do whatever is due right now: a local backup, then pending uploads."""
    result = TickResult()
    state = load_state(config.backup_dir)
    if force or (config.db_path.exists() and local_due(state, config, clock())):
        result.local_attempted = True
        try:
            result.local_path = run_local_backup(config, clock=clock, lock_wait=0.0)
            logger.info("Backup written: %s", result.local_path.name)
        except LockBusy:
            result.skipped_busy = True
            logger.info("Another backup is running; will check again")
        except (BackupError, OSError, sqlite3.Error) as exc:
            result.local_error = str(exc)
            logger.error("Scheduled backup failed: %s", exc)
    if config.remote is not None:
        _upload_pending(config, result, clock=clock, upload=upload)
    elif config.remote_enabled and config.remote_error and result.local_path is not None:
        logger.error("Off-site upload is enabled but not usable: %s", config.remote_error)
    return result


def _upload_pending(config: BackupConfig, result: TickResult, *, clock: Clock, upload) -> None:
    remote = config.remote
    backup_dir = config.backup_dir
    try:
        with file_lock(backup_dir / ".upload.lock", wait=0.0):
            while True:
                state = load_state(backup_dir)
                if not state["pending_uploads"] or remote_retry_blocked(state, config, clock()):
                    return
                # Oldest first, so a recovered destination receives history in order.
                name = state["pending_uploads"][0]
                path = backup_dir / name
                if not path.is_file():
                    logger.warning("Pending backup %s no longer exists locally; dropping it", name)
                    update_state(backup_dir, lambda s, n=name: _drop_pending(s, n))
                    continue
                attempted = clock()
                try:
                    uploaded = upload(remote, path, remote.object_key(name))
                except UploadError as exc:
                    message = redact(str(exc), remote.secrets())
                except Exception as exc:  # defensive: never let a transport bug kill the timer
                    message = redact(f"{type(exc).__name__}: {exc}", remote.secrets())
                else:
                    update_state(
                        backup_dir,
                        lambda s, n=name, u=uploaded: _record_upload_success(s, n, u, clock()),
                    )
                    result.uploaded.append(name)
                    logger.info("Uploaded %s off-site", name)
                    continue
                update_state(
                    backup_dir,
                    lambda s, m=message, a=attempted: _record_upload_failure(s, a, m),
                )
                result.upload_error = message
                logger.warning("Off-site upload of %s failed; will retry: %s", name, message)
                return
    except LockBusy:
        logger.info("Another process is uploading; will check again")


def _drop_pending(state: dict, name: str) -> None:
    state["pending_uploads"] = [n for n in state["pending_uploads"] if n != name]


def _record_upload_success(state: dict, name: str, uploaded, now) -> None:
    _drop_pending(state, name)
    state["last_remote_success_at"] = format_ts(now)
    state["last_remote_attempt_at"] = format_ts(now)
    state["last_remote_file"] = name
    state["last_remote_key"] = getattr(uploaded, "key", None)
    state["last_remote_sha256"] = getattr(uploaded, "sha256", None)
    state["remote_failures"] = 0
    state["last_remote_error"] = None


def _record_upload_failure(state: dict, attempted, message: str) -> None:
    state["last_remote_attempt_at"] = format_ts(attempted)
    state["remote_failures"] = state.get("remote_failures", 0) + 1
    state["last_remote_error"] = message[:500]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_scheduler(
    config: BackupConfig,
    *,
    stop_event: threading.Event | None = None,
    parent_pid: int | None = None,
    clock: Clock = utcnow,
    upload: Uploader = upload_file,
    wait: Callable[[float], object] | None = None,
) -> int:
    """Run the timer loop until stopped; returns a process exit code."""
    stop_event = stop_event or threading.Event()
    wait = wait or stop_event.wait
    for warning in config.warnings:
        logger.warning("Backup configuration: %s", warning)
    try:
        with file_lock(config.backup_dir / ".scheduler.lock", wait=0.0, write_pid=True):
            _loop(config, stop_event, parent_pid, clock, upload, wait)
    except LockBusy:
        logger.info("Backup scheduler already running; this process will not start a second")
    return 0


def _loop(config, stop_event, parent_pid, clock, upload, wait) -> None:
    status = describe_status(config, load_state(config.backup_dir), clock())
    logger.info(
        "Backup scheduler started: every %.4g h, checking every %d s, keeping %d local, "
        "off-site %s%s",
        status["interval_hours"],
        config.check_seconds,
        config.keep,
        status["remote"],
        f" ({config.remote_error})" if config.remote_error else "",
    )
    try:
        while not stop_event.is_set():
            if parent_pid is not None and not _pid_alive(parent_pid):
                logger.info("Parent process %s is gone; backup scheduler exiting", parent_pid)
                break
            try:
                run_tick(config, clock=clock, upload=upload)
            except Exception as exc:  # the timer outlives any single failed tick
                logger.error("Backup scheduler tick failed: %s", _safe(exc, config))
            wait(config.check_seconds)
    finally:
        logger.info("Backup scheduler stopped")


def _safe(exc: Exception, config: BackupConfig) -> str:
    secrets = config.remote.secrets() if config.remote is not None else ()
    return redact(f"{type(exc).__name__}: {exc}", secrets)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup_scheduler",
        description="Run the periodic database backup timer (and off-site upload if enabled).",
    )
    parser.add_argument(
        "--once", action="store_true", help="Do one check (backup if due, upload pending) and exit."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --once: take a backup now even if one is not due yet.",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="Exit when this process id no longer exists (the container entrypoint's shell).",
    )
    return parser


def _terminate(signum, frame):
    # Raised from the handler so a running backup or curl is torn down at once;
    # snapshots are written to a .partial name and state is replaced atomically,
    # so stopping at any point leaves nothing half-written.
    raise SystemExit(0)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = BackupConfig.from_settings()
    if args.once:
        result = run_tick(config, force=args.force)
        if result.local_error:
            print(f"Backup failed: {result.local_error}", file=sys.stderr)
        if result.upload_error:
            print(f"Upload failed: {result.upload_error}", file=sys.stderr)
        return 1 if (result.local_error or result.upload_error) else 0
    if not config.scheduler_enabled:
        logger.info("Backup scheduler disabled (BACKUP_SCHEDULER_ENABLED=false)")
        return 0
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    return run_scheduler(config, parent_pid=args.parent_pid)


def cli() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.conf import settings
    from django.utils.log import configure_logging

    # The settings module is all this process needs; django.setup() would also
    # import every app's models and cost memory the 256 MB machine cannot spare.
    configure_logging(settings.LOGGING_CONFIG, settings.LOGGING)
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
