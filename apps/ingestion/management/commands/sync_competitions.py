from django.core.management.base import BaseCommand
from django.utils.text import slugify
from apps.football.models import Competition
from apps.ingestion.providers import get_provider
class Command(BaseCommand):
    def handle(self,*a,**o):
        count=0; provider=get_provider()
        for item in provider.list_competitions():
            competition,created=Competition.objects.get_or_create(provider=provider.provider_name,provider_id=item.id,defaults={"name":item.name,"slug":slugify(item.name),"country_code":item.country_code,"competition_type":item.kind,"format":"LEAGUE" if item.kind=="DOMESTIC_LEAGUE" else "CUP" if item.kind in ("UCL","CUP") else "UNKNOWN"})
            if not created:
                competition.name=item.name; competition.country_code=item.country_code
                competition.save(update_fields=["name","country_code","updated_at"])
            count+=1
        self.stdout.write(self.style.SUCCESS(f"Synchronized {count} competitions; enable tracked competitions in Admin"))
