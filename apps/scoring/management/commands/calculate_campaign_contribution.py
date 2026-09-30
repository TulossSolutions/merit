from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from apps.football.models import Player, WinningCampaign
from apps.scoring.services.campaigns import record_campaign_contribution


class Command(BaseCommand):
    help="Calculate and record a player's verified title-campaign participation without changing ranking scores."

    def add_arguments(self,parser):
        parser.add_argument("--campaign",type=int,required=True)
        parser.add_argument("--player",required=True,help="Player slug")
        parser.add_argument("--as-of",required=True)

    def handle(self,*args,**options):
        cutoff=parse_datetime(options["as_of"])
        if not cutoff or not cutoff.tzinfo: raise CommandError("--as-of must be a timezone-aware ISO datetime")
        try:
            campaign=WinningCampaign.objects.select_related("edition","policy","winner").get(pk=options["campaign"])
            player=Player.objects.get(slug=options["player"])
        except (WinningCampaign.DoesNotExist,Player.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        record=record_campaign_contribution(campaign,player,cutoff)
        self.stdout.write(f"campaign_contribution={record.contribution if record.contribution is not None else 'unavailable'} reason={record.reason or 'complete'} record={record.pk}")
