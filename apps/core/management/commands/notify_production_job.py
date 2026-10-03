import os

from django.core.management.base import BaseCommand

from apps.core.alerts import JOBS, notify_job


class Command(BaseCommand):
    help = "Email recorded production job failures and rejected publications; no provider requests."

    def add_arguments(self, parser):
        parser.add_argument("--job", choices=tuple(JOBS), required=True)

    def handle(self, *args, **options):
        sent = notify_job(options["job"], os.getenv("SERVICE_RESULT", "success"))
        self.stdout.write(f"Production alerts accepted by mail transport: {sent}")
