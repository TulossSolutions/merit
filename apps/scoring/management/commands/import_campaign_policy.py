import json
from django.core.management.base import BaseCommand, CommandError
from apps.football.models import CampaignPolicy
from apps.scoring.services.campaigns import validate_campaign_policy


class Command(BaseCommand):
    help="Import an immutable campaign/context policy supplied as version and config JSON."

    def add_arguments(self,parser): parser.add_argument("path")

    def handle(self,*args,**options):
        with open(options["path"],encoding="utf8") as handle: data=json.load(handle)
        if set(data)!={"version","config"} or not isinstance(data["version"],str) or not data["version"]:
            raise CommandError("Policy file must contain a nonempty version and config.")
        checksum=validate_campaign_policy(data["config"])
        existing=CampaignPolicy.objects.filter(version=data["version"]).first()
        if existing:
            if existing.checksum_sha256!=checksum: raise CommandError("Policy version already exists with a different checksum.")
            self.stdout.write("Policy already imported with matching checksum"); return
        CampaignPolicy.objects.create(version=data["version"],config=data["config"])
        self.stdout.write(f"Imported campaign policy {data['version']}")
