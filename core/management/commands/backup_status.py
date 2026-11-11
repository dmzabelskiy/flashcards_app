import json

from django.core.management.base import BaseCommand, CommandError

from core.backup import BackupConfig, describe_status, load_state, utcnow


class Command(BaseCommand):
    help = (
        "Show the persisted backup state: last local snapshot, pending and last "
        "successful off-site upload. Never prints credentials."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the local backup is overdue (more than two "
            "intervals) or the off-site destination is enabled but failing.",
        )

    def handle(self, *args, **options):
        config = BackupConfig.from_settings()
        status = describe_status(config, load_state(config.backup_dir), utcnow())
        if options["json"]:
            self.stdout.write(json.dumps(status, indent=2, sort_keys=True))
        else:
            for key, value in status.items():
                self.stdout.write(f"{key}: {value}")
        problems = []
        if status["local_overdue"]:
            problems.append("local backup is overdue")
        if config.remote_enabled and (config.remote is None or status["remote_failures"]):
            problems.append("off-site upload is not working")
        if options["check"] and problems:
            raise CommandError("; ".join(problems))
