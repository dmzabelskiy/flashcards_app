from django.core.management.base import BaseCommand, CommandError

from core.backup_scheduler import main


class Command(BaseCommand):
    help = (
        "Run the backup timer: a verified snapshot every BACKUP_INTERVAL_HOURS "
        "plus the opt-in off-site upload. The container entrypoint runs "
        "`python -m core.backup_scheduler` instead, which skips loading Django's apps."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Do one check (backup if due, upload pending) and exit.",
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
            help="Exit when this process id no longer exists.",
        )

    def handle(self, *args, **options):
        argv = []
        if options["once"]:
            argv.append("--once")
        if options["force"]:
            argv.append("--force")
        if options["parent_pid"] is not None:
            argv += ["--parent-pid", str(options["parent_pid"])]
        if main(argv):
            raise CommandError("backup scheduler reported a failure")
