import dataclasses
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.backup import BackupConfig, BackupError, LockBusy, run_local_backup


class Command(BaseCommand):
    help = (
        "Copy the configured SQLite database to a timestamped file using the "
        "sqlite3 online backup API, verify it, record it in the backup state "
        "(queueing an off-site upload when that is enabled), then prune old backups."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=None,
            help="Directory to write the backup into (default: a backups/ "
            "directory next to the database file).",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=None,
            help="Number of newest backups to keep, always including the "
            "backup just written regardless of its name's sort order and any "
            "backup still waiting for off-site upload; 0 keeps everything "
            "(default: BACKUP_KEEP_LOCAL, 7).",
        )

    def handle(self, *args, **options):
        config = BackupConfig.from_settings()
        if options["output"]:
            config = dataclasses.replace(config, backup_dir=Path(options["output"]))
        if not config.db_path.exists():
            self.stderr.write(self.style.ERROR(f"Database file not found: {config.db_path}"))
            raise CommandError(f"Database file not found: {config.db_path}")
        try:
            dest = run_local_backup(config, keep=options["keep"])
        except LockBusy:
            raise CommandError("Another backup is already running") from None
        except BackupError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(str(dest))
