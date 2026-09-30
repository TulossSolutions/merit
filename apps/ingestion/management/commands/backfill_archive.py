from django.core.management.base import BaseCommand,CommandError
from apps.core.services import application_lock
from apps.ingestion.providers import get_provider
from apps.ingestion.services.archive import ArchiveBackfill


class Command(BaseCommand):
    help="Batch backfill the verified club/national archive, catch up the current season and publish new versioned snapshots."

    def add_arguments(self,parser):
        parser.add_argument("--daily-call-budget",type=int,default=7000)
        parser.add_argument("--reserve",type=int,default=500)
        parser.add_argument("--request-interval",type=float,default=0.25)
        parser.add_argument("--max-runtime",type=int,default=21600)
        parser.add_argument("--first-year",type=int,default=2015)
        parser.add_argument("--prepare-only",action="store_true")

    def handle(self,*args,**options):
        if options["daily_call_budget"]<1 or options["reserve"]<0 or options["max_runtime"]<1 or options["request_interval"]<0:
            raise CommandError("Invalid budget, reserve, pace or runtime")
        provider=get_provider()
        if provider.provider_name!="api_football": raise CommandError("Archive batches require API-Football")
        with application_lock("season_backfill") as acquired:
            if not acquired: raise CommandError("A backfill is already running")
            job=ArchiveBackfill(provider,budget=options["daily_call_budget"],reserve=options["reserve"],interval=options["request_interval"],
                max_runtime=options["max_runtime"],first_year=options["first_year"],report=lambda message,**kwargs:self.stdout.write(message))
            result=job.run(prepare_only=options["prepare_only"])
            self.stdout.write(f"Archive {result['status']}; API calls used: {provider.requests_made}")
