"""Consistent SQLite snapshots, durable backup state, and process locks.

Imports only the standard library and `django.conf.settings`, so the timer can
run as `python -m core.backup_scheduler` without loading Django's apps, which
keeps the extra process small on the 256 MB production machine.
"""

import contextlib
import errno
import fcntl
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.backup_remote import RemoteConfig, RemoteConfigError

logger = logging.getLogger(__name__)

BACKUP_NAME_RE = re.compile(r"db-\d{8}-\d{6}(?:-\d+)?\.sqlite3")
STATE_FILE = "backup-state.json"
STATE_VERSION = 1
# More pending uploads than this means the destination has been down for
# weeks; the oldest ones stop being protected from local pruning (the newest
# pending backup is never dropped).
MAX_PENDING_UPLOADS = 14
# A last-success timestamp this far in the future means the clock was set back.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

DEFAULT_INTERVAL_HOURS = 24.0
DEFAULT_CHECK_SECONDS = 60
DEFAULT_RETRY_SECONDS = 900
DEFAULT_KEEP_LOCAL = 7

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class BackupError(Exception):
    """A backup, verification, or restore step failed. Safe to show and log."""


class LockBusy(Exception):
    """Another process holds the lock."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupConfig:
    db_path: Path
    backup_dir: Path
    scheduler_enabled: bool = True
    interval_seconds: float = DEFAULT_INTERVAL_HOURS * 3600
    check_seconds: int = DEFAULT_CHECK_SECONDS
    retry_seconds: int = DEFAULT_RETRY_SECONDS
    keep: int = DEFAULT_KEEP_LOCAL
    remote: RemoteConfig | None = None
    remote_enabled: bool = False
    remote_error: str = ""  # why remote is enabled but unusable; never holds a value
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_settings(cls) -> "BackupConfig":
        from django.conf import settings

        def raw(name, default=""):
            return getattr(settings, name, default)

        warnings: list[str] = []
        db_path = Path(settings.DATABASES["default"]["NAME"])
        configured_dir = str(raw("BACKUP_DIR")).strip()
        backup_dir = Path(configured_dir) if configured_dir else db_path.parent / "backups"

        interval_hours = _number(
            raw("BACKUP_INTERVAL_HOURS"),
            "BACKUP_INTERVAL_HOURS",
            DEFAULT_INTERVAL_HOURS,
            0.25,
            168,
            float,
            warnings,
        )
        check_seconds = _number(
            raw("BACKUP_CHECK_SECONDS"),
            "BACKUP_CHECK_SECONDS",
            DEFAULT_CHECK_SECONDS,
            5,
            60,
            int,
            warnings,
        )
        retry_seconds = _number(
            raw("BACKUP_RETRY_SECONDS"),
            "BACKUP_RETRY_SECONDS",
            DEFAULT_RETRY_SECONDS,
            60,
            86400,
            int,
            warnings,
        )
        keep = _number(
            raw("BACKUP_KEEP_LOCAL"),
            "BACKUP_KEEP_LOCAL",
            DEFAULT_KEEP_LOCAL,
            1,
            365,
            int,
            warnings,
        )
        scheduler_enabled = _flag(
            raw("BACKUP_SCHEDULER_ENABLED", "true"), "BACKUP_SCHEDULER_ENABLED", True, warnings
        )

        remote_enabled = _flag(
            raw("BACKUP_REMOTE_ENABLED", "false"), "BACKUP_REMOTE_ENABLED", False, warnings
        )
        remote = None
        remote_error = ""
        if remote_enabled:
            try:
                remote = RemoteConfig.from_values(
                    endpoint=raw("BACKUP_S3_ENDPOINT"),
                    region=raw("BACKUP_S3_REGION"),
                    bucket=raw("BACKUP_S3_BUCKET"),
                    prefix=raw("BACKUP_S3_PREFIX"),
                    addressing=raw("BACKUP_S3_ADDRESSING"),
                    access_key_id=raw("BACKUP_S3_ACCESS_KEY_ID"),
                    secret_access_key=raw("BACKUP_S3_SECRET_ACCESS_KEY"),
                    session_token=raw("BACKUP_S3_SESSION_TOKEN"),
                    timeout_seconds=raw("BACKUP_UPLOAD_TIMEOUT_SECONDS"),
                )
            except RemoteConfigError as exc:
                remote_error = str(exc)

        return cls(
            db_path=db_path,
            backup_dir=backup_dir,
            scheduler_enabled=scheduler_enabled,
            interval_seconds=interval_hours * 3600,
            check_seconds=check_seconds,
            retry_seconds=retry_seconds,
            keep=keep,
            remote=remote,
            remote_enabled=remote_enabled,
            remote_error=remote_error,
            warnings=tuple(warnings),
        )


def _number(raw, name, default, low, high, cast, warnings):
    text = str(raw if raw is not None else "").strip()
    if not text:
        return default
    try:
        value = cast(text)
    except ValueError:
        warnings.append(f"{name} is not a number; using the default {default}")
        return default
    if value != value:  # NaN
        warnings.append(f"{name} is not a number; using the default {default}")
        return default
    if value < low:
        warnings.append(f"{name} is below the minimum {low}; using {low}")
        return low
    if value > high:
        warnings.append(f"{name} is above the maximum {high}; using {high}")
        return high
    return value


def _flag(raw, name, default, warnings):
    text = str(raw if raw is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    if text:
        warnings.append(f"{name} is not a boolean; using the default {default}")
    return default


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------


@contextlib.contextmanager
def file_lock(path: Path, *, wait: float = 0.0, write_pid: bool = False):
    """Exclusive advisory lock released by the OS if the holder dies."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise LockBusy(path.name) from None
                time.sleep(0.05)
        if write_pid:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# Durable state
# --------------------------------------------------------------------------


def _empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "last_local_success_at": None,
        "last_local_file": None,
        "last_local_attempt_at": None,
        "local_failures": 0,
        "last_local_error": None,
        "pending_uploads": [],
        "last_remote_success_at": None,
        "last_remote_file": None,
        "last_remote_key": None,
        "last_remote_sha256": None,
        "last_remote_attempt_at": None,
        "remote_failures": 0,
        "last_remote_error": None,
    }


def format_ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_state(backup_dir: Path) -> dict:
    """Read the persisted state, tolerating a missing, damaged or tampered file."""
    path = Path(backup_dir) / STATE_FILE
    state = _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
    except FileNotFoundError:
        return state
    except (OSError, ValueError) as exc:
        logger.warning("Backup state file unreadable (%s); starting from empty state", exc)
        with contextlib.suppress(OSError):
            os.replace(path, path.with_name(STATE_FILE + ".corrupt"))
        return state

    for key in (
        "last_local_success_at",
        "last_local_attempt_at",
        "last_remote_success_at",
        "last_remote_attempt_at",
    ):
        if parse_ts(data.get(key)) is not None:
            state[key] = data[key]
    for key in ("last_local_file", "last_remote_file"):
        if isinstance(data.get(key), str) and BACKUP_NAME_RE.fullmatch(data[key]):
            state[key] = data[key]
    for key in ("local_failures", "remote_failures"):
        if isinstance(data.get(key), int) and not isinstance(data[key], bool) and data[key] >= 0:
            state[key] = min(data[key], 1_000_000)
    for key in ("last_local_error", "last_remote_error", "last_remote_key"):
        if isinstance(data.get(key), str):
            state[key] = data[key][:500]
    if isinstance(data.get("last_remote_sha256"), str) and re.fullmatch(
        r"[0-9a-f]{64}", data["last_remote_sha256"]
    ):
        state["last_remote_sha256"] = data["last_remote_sha256"]
    pending = data.get("pending_uploads")
    if isinstance(pending, list):
        # Names only, never paths: a tampered entry must not escape backup_dir.
        seen = []
        for name in pending:
            if isinstance(name, str) and BACKUP_NAME_RE.fullmatch(name) and name not in seen:
                seen.append(name)
        state["pending_uploads"] = seen
    return state


def _write_state(backup_dir: Path, state: dict) -> None:
    path = backup_dir / STATE_FILE
    tmp = backup_dir / f".{STATE_FILE}.{os.getpid()}.tmp"
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    _fsync_dir(backup_dir)


def update_state(backup_dir: Path, mutate: Callable[[dict], None]) -> dict:
    """Read-modify-write the state under a lock so concurrent writers cannot lose updates."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(backup_dir / ".state.lock", wait=10):
        state = load_state(backup_dir)
        mutate(state)
        _write_state(backup_dir, state)
    return state


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# --------------------------------------------------------------------------
# Timer arithmetic (pure: tests drive it with a fake clock)
# --------------------------------------------------------------------------


def backoff_seconds(failures: int, retry_seconds: float, interval_seconds: float) -> float:
    if failures <= 0:
        return 0.0
    return min(retry_seconds * (2 ** min(failures - 1, 10)), interval_seconds)


def local_due(state: dict, config: BackupConfig, now: datetime) -> bool:
    """True when a local backup should run: never succeeded, overdue, or clock set back."""
    attempt = parse_ts(state.get("last_local_attempt_at"))
    failures = state.get("local_failures", 0)
    if failures and attempt is not None and attempt <= now:
        wait = backoff_seconds(failures, config.retry_seconds, config.interval_seconds)
        if now < attempt + timedelta(seconds=wait):
            return False
    last = parse_ts(state.get("last_local_success_at"))
    if last is None:
        return True
    if last > now + CLOCK_SKEW_TOLERANCE:
        return True
    return now >= last + timedelta(seconds=config.interval_seconds)


def remote_retry_blocked(state: dict, config: BackupConfig, now: datetime) -> bool:
    attempt = parse_ts(state.get("last_remote_attempt_at"))
    failures = state.get("remote_failures", 0)
    if not failures or attempt is None or attempt > now:
        return False
    wait = backoff_seconds(failures, config.retry_seconds, config.interval_seconds)
    return now < attempt + timedelta(seconds=wait)


def next_local_due_at(state: dict, config: BackupConfig) -> datetime | None:
    last = parse_ts(state.get("last_local_success_at"))
    return None if last is None else last + timedelta(seconds=config.interval_seconds)


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


def _copy_database(source_path: Path, partial: Path) -> None:
    # sqlite3's online backup API reads through the WAL, so rows committed but
    # not yet checkpointed are included and a writer cannot tear the copy.
    source = sqlite3.connect(str(source_path), timeout=30)
    try:
        dest = sqlite3.connect(str(partial))
        try:
            source.backup(dest)
            # Make the snapshot one self-contained file (no -wal/-shm needed).
            dest.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest.close()
    finally:
        source.close()


def _verify_snapshot(partial: Path) -> None:
    conn = sqlite3.connect(f"{partial.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    finally:
        conn.close()
    if rows != [("ok",)]:
        raise BackupError("snapshot failed quick_check; it was discarded")


def create_snapshot(source_path: Path, backup_dir: Path, *, clock: Clock = utcnow) -> Path:
    """Write a verified snapshot as db-YYYYMMDD-HHMMSS.sqlite3; caller holds the backup lock."""
    stamp = clock().astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"db-{stamp}.sqlite3"
    suffix = 1
    while dest.exists():
        suffix += 1
        dest = backup_dir / f"db-{stamp}-{suffix}.sqlite3"
    # An in-progress copy never carries a name the pruner or uploader accepts.
    for stale in backup_dir.glob("db-*.sqlite3.partial"):
        with contextlib.suppress(OSError):
            stale.unlink()
    partial = dest.with_name(dest.name + ".partial")
    try:
        _copy_database(source_path, partial)
        _verify_snapshot(partial)
        with open(partial, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(partial, dest)
    except sqlite3.Error as exc:
        raise BackupError(f"sqlite error while copying the database: {exc}") from None
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()
    _fsync_dir(backup_dir)
    return dest


def prune_backups(backup_dir: Path, keep_path: Path, keep: int, protected=()) -> list[Path]:
    """Delete the oldest snapshots beyond `keep`.

    `keep_path` (the snapshot just written) always survives wherever its name
    sorts, and names in `protected` (backups still waiting for upload) are
    never deleted, even beyond `keep`.
    """
    if keep <= 0:
        return []
    protected = set(protected)
    others = sorted(p for p in backup_dir.glob("db-*.sqlite3") if p != keep_path)
    keep_others = keep - 1
    if keep_others <= 0:
        candidates = others
    elif keep_others < len(others):
        candidates = others[:-keep_others]
    else:
        candidates = []
    removed = []
    for path in candidates:
        if path.name in protected:
            continue
        path.unlink()
        removed.append(path)
    return removed


def run_local_backup(
    config: BackupConfig,
    *,
    keep: int | None = None,
    clock: Clock = utcnow,
    lock_wait: float = 30.0,
) -> Path:
    """One locked, verified, recorded snapshot. Raises BackupError or LockBusy."""
    if not config.db_path.exists():
        raise BackupError(f"Database file not found: {config.db_path}")
    backup_dir = config.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    keep = config.keep if keep is None else keep

    with file_lock(backup_dir / ".backup.lock", wait=lock_wait):
        attempted = clock()
        try:
            dest = create_snapshot(config.db_path, backup_dir, clock=clock)
        except (BackupError, OSError) as exc:
            message = str(exc) if isinstance(exc, BackupError) else f"{type(exc).__name__}: {exc}"
            _record_local_failure(backup_dir, attempted, message)
            raise BackupError(message) from None

        queue = config.remote is not None

        def record(state: dict) -> None:
            state["last_local_success_at"] = format_ts(clock())
            state["last_local_attempt_at"] = format_ts(attempted)
            state["last_local_file"] = dest.name
            state["local_failures"] = 0
            state["last_local_error"] = None
            if queue:
                pending = [n for n in state["pending_uploads"] if n != dest.name]
                pending.append(dest.name)
                if len(pending) > MAX_PENDING_UPLOADS:
                    logger.warning(
                        "More than %d backups are waiting for upload; "
                        "no longer protecting the oldest from pruning",
                        MAX_PENDING_UPLOADS,
                    )
                    pending = pending[-MAX_PENDING_UPLOADS:]
                state["pending_uploads"] = pending

        state = update_state(backup_dir, record)
        prune_backups(backup_dir, dest, keep, protected=state["pending_uploads"])
    return dest


def _record_local_failure(backup_dir: Path, attempted: datetime, message: str) -> None:
    def record(state: dict) -> None:
        state["last_local_attempt_at"] = format_ts(attempted)
        state["local_failures"] = state.get("local_failures", 0) + 1
        state["last_local_error"] = message[:500]

    try:
        update_state(backup_dir, record)
    except (OSError, LockBusy):
        logger.error("Could not record the failed backup attempt in the state file")


# --------------------------------------------------------------------------
# Restore drill
# --------------------------------------------------------------------------


def restore_to_scratch(backup_path: Path, target: Path, *, protected_paths=()) -> dict:
    """Copy a snapshot into a NEW database file and check it.

    Refuses to overwrite anything: the target (and its -wal/-shm companions)
    must not exist, must not be any path in `protected_paths` (the live
    database), and the snapshot itself is opened read-only and immutable.
    """
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise BackupError(f"Backup file not found: {backup_path}")
    target = Path(target)
    resolved_target = target.resolve()
    for protected in protected_paths:
        guarded = Path(protected).resolve()
        if resolved_target in {guarded, Path(str(guarded) + "-wal"), Path(str(guarded) + "-shm")}:
            raise BackupError("Refusing to restore over the live database")
    if resolved_target == backup_path.resolve():
        raise BackupError("Refusing to restore over the backup file itself")
    for companion in (target, Path(str(target) + "-wal"), Path(str(target) + "-shm")):
        if companion.exists():
            raise BackupError(f"Refusing to overwrite existing file: {companion}")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        source = sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
                integrity = [row[0] for row in dest.execute("PRAGMA integrity_check")]
                foreign_key_violations = len(dest.execute("PRAGMA foreign_key_check").fetchall())
                tables = {}
                names = [
                    row[0]
                    for row in dest.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                for name in names:
                    quoted = '"' + name.replace('"', '""') + '"'
                    tables[name] = dest.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            finally:
                dest.close()
        finally:
            source.close()
    except sqlite3.Error as exc:
        for leftover in (target, Path(str(target) + "-wal"), Path(str(target) + "-shm")):
            with contextlib.suppress(OSError):
                leftover.unlink()
        raise BackupError(f"restore into the scratch database failed: {exc}") from None

    return {
        "target": str(target),
        "integrity_ok": integrity == ["ok"],
        "integrity_messages": integrity[:10],
        "foreign_key_violations": foreign_key_violations,
        "tables": tables,
    }


def latest_backup(backup_dir: Path) -> Path | None:
    candidates = sorted(p for p in Path(backup_dir).glob("db-*.sqlite3") if p.is_file())
    return candidates[-1] if candidates else None


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def describe_status(config: BackupConfig, state: dict, now: datetime) -> dict:
    """A secret-free summary. Off-site success is only ever what the state recorded."""
    last_local = parse_ts(state.get("last_local_success_at"))
    next_due = next_local_due_at(state, config)
    if not config.remote_enabled:
        remote_state = "disabled"
    elif config.remote is None:
        remote_state = "misconfigured"
    else:
        remote_state = "enabled"
    return {
        "scheduler_enabled": config.scheduler_enabled,
        "interval_hours": config.interval_seconds / 3600,
        "keep_local": config.keep,
        "last_local_success_at": state.get("last_local_success_at"),
        "last_local_file": state.get("last_local_file"),
        "local_failures": state.get("local_failures", 0),
        "last_local_error": state.get("last_local_error"),
        "local_overdue": last_local is None
        or now > last_local + 2 * timedelta(seconds=config.interval_seconds),
        "next_local_due_at": format_ts(next_due) if next_due else None,
        "remote": remote_state,
        "remote_config_error": config.remote_error or None,
        "pending_uploads": list(state.get("pending_uploads", [])),
        "last_remote_success_at": state.get("last_remote_success_at"),
        "last_remote_file": state.get("last_remote_file"),
        "last_remote_key": state.get("last_remote_key"),
        "last_remote_sha256": state.get("last_remote_sha256"),
        "remote_failures": state.get("remote_failures", 0),
        "last_remote_error": state.get("last_remote_error"),
    }
