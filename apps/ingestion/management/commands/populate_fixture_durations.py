from django.core.management.base import BaseCommand
from apps.core.services import resolve_season
from apps.football.models import Fixture
from apps.ingestion.models import RawProviderPayload


class Command(BaseCommand):
    help="Preview/apply 90/120-minute durations from retained API-Football status evidence. No API calls."

    def add_arguments(self,parser):
        parser.add_argument("--season",required=True)
        parser.add_argument("--apply",action="store_true")

    def handle(self,*args,**options):
        fixtures=Fixture.objects.filter(competition_season__season=resolve_season(options["season"]),provider="api_football",available_minutes__isnull=True)
        known=unknown=0
        for fixture in fixtures.iterator():
            saved=RawProviderPayload.objects.filter(provider="api_football",resource_type="fixture",provider_resource_id=fixture.provider_id).order_by("-received_at","-pk").first()
            status=((((saved.payload if saved else {}).get("fixture") or {}).get("fixture") or {}).get("status") or {}).get("short")
            duration={"FT":90,"AET":120}.get(status)
            elapsed=((((saved.payload if saved else {}).get("fixture") or {}).get("fixture") or {}).get("status") or {}).get("elapsed")
            if status=="PEN" and elapsed in (90,120): duration=elapsed
            if duration is None:
                unknown+=1; continue
            known+=1
            if options["apply"]:
                Fixture.objects.filter(pk=fixture.pk,available_minutes__isnull=True).update(available_minutes=duration)
        self.stdout.write(f"{'Applied' if options['apply'] else 'Would apply'} {known} durations; {unknown} unresolved. No provider requests made.")
