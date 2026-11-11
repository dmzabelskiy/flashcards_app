from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.backup import BackupConfig, BackupError, latest_backup, restore_to_scratch


class Command(BaseCommand):
    help = (
        "Restore drill: copy a backup snapshot into a NEW scratch database and "
        "run integrity_check, foreign_key_check and per-table row counts. Never "
        "overwrites an existing file or the configured database."
    )

    def add_arguments(self, parser):
        parser.add_argument("target", help="Path of the new scratch database (must not exist).")
        parser.add_argument(
            "--backup",
            default=None,
            help="Snapshot file to restore (default: the newest local snapshot). "
            "Use this for a file downloaded from the off-site bucket.",
        )

    def handle(self, *args, **options):
        config = BackupConfig.from_settings()
        if options["backup"]:
            backup = Path(options["backup"])
        else:
            backup = latest_backup(config.backup_dir)
            if backup is None:
                raise CommandError(f"No snapshots found in {config.backup_dir}")
        try:
            report = restore_to_scratch(
                backup,
                Path(options["target"]),
                protected_paths=[config.db_path],
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"restored {backup} -> {report['target']}")
        self.stdout.write(f"integrity_check: {'ok' if report['integrity_ok'] else 'FAILED'}")
        if not report["integrity_ok"]:
            for message in report["integrity_messages"]:
                self.stdout.write(f"  {message}")
        self.stdout.write(f"foreign_key_check violations: {report['foreign_key_violations']}")
        for name, count in report["tables"].items():
            self.stdout.write(f"  {name}: {count}")
        if not report["integrity_ok"]:
            raise CommandError("integrity_check failed; do not use this snapshot")
