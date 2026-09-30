import json
from django.core.management.base import BaseCommand,CommandError
from apps.core.services import application_lock
from apps.rankings.services.weekly_replay import WeeklyReplay


class Command(BaseCommand):
    help="Resume the local-only weekly replay for 2015/16–2026/27 after archive retrieval completes; preserve public snapshots."

    def add_arguments(self,parser):
        parser.add_argument("--max-runtime",type=int,default=1800)
        parser.add_argument("--max-weeks",type=int,default=20)

    def handle(self,*args,**options):
        if options["max_runtime"]<1 or options["max_weeks"]<1: raise CommandError("Runtime and batch size must be positive")
        with application_lock("season_backfill") as acquired:
            if not acquired:
                self.stdout.write("Weekly replay deferred: archive backfill owns the shared lock. API calls: 0.")
                return
            result=WeeklyReplay(max_runtime=options["max_runtime"],max_weeks=options["max_weeks"],report=self.stdout.write).run()
            self.stdout.write(json.dumps({"status":result["status"],"counts":result["counts"],"api_calls":0,"reason":result.get("reason","")}))
