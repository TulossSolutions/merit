import json
from django.core.management.base import BaseCommand,CommandError
from apps.core.services import application_lock,resolve_season
from apps.ingestion.providers import get_provider
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.ingestion.services.shot_consistency import inconsistent_shots,refresh_shot_conversion


class Command(BaseCommand):
    help="Preview or apply the approved bounded shot-conversion refresh; goals and snapshots are preserved."

    def add_arguments(self,parser):
        parser.add_argument("--season",required=True)
        parser.add_argument("--apply",action="store_true")

    def handle(self,*args,**options):
        season=resolve_season(options["season"])
        rows=inconsistent_shots(season)
        if not options["apply"]:
            self.stdout.write(json.dumps({"records":rows.count(),"fixtures":rows.values("player_fixture__fixture_id").distinct().count(),"provider_calls":0}))
            return
        with application_lock("season_backfill") as acquired:
            if not acquired: raise CommandError("A backfill holds the lock; retry the refresh after its checkpointed stop")
            try: result=refresh_shot_conversion(get_provider(),season)
            except (ValueError,ProviderRequestLimitReached) as exc: raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result))
