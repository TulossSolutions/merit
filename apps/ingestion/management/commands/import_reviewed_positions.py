from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand,CommandError
from apps.core.services import application_lock
from apps.ingestion.services.reviews import import_corrections

class Command(BaseCommand):
    help="Apply an explicitly reviewed correction table and approved provider identity aliases. No API requests."
    def add_arguments(self,parser):
        parser.add_argument("path")
        parser.add_argument("--alias",action="append",default=[],help="Explicit old:canonical provider ID mapping")
    def handle(self,*args,**options):
        try:
            aliases=[]
            for value in options["alias"]:
                pair=value.split(":")
                if len(pair)!=2 or not all(item.isdigit() for item in pair): raise ValidationError("Aliases require old:canonical numeric IDs")
                aliases.append(tuple(pair))
            with application_lock("season_backfill") as acquired:
                if not acquired: raise CommandError("A backfill is running; apply reviews at a saved checkpoint")
                result=import_corrections(options["path"],aliases)
            self.stdout.write(str(result))
        except (ValidationError,ValueError) as exc: raise CommandError(str(exc)) from exc
