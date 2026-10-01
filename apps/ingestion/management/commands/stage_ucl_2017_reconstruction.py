from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from apps.core.services import application_lock
from apps.ingestion.providers.uefa_official import UefaOfficialClient
from apps.ingestion.services.ucl_2017_reconstruction import collect_reconstruction


class Command(BaseCommand):
    help = "Validate and stage UEFA evidence with optional rendered EnPelotas exports for 29 missing 2017/18 UCL fixtures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--enpelotas-dir",
            help="Directory containing rendered EnPelotas exports named by UEFA match id (for example 2021683.html).",
        )
        parser.add_argument(
            "--stage",
            action="store_true",
            help="Retain raw evidence and normalized proposals. Without this option the command is read-only.",
        )

    def handle(self, *args, **options):
        with application_lock("stage_ucl_2017_reconstruction") as acquired:
            if not acquired:
                raise CommandError("The 2017/18 UCL reconstruction is already running")
            try:
                result = collect_reconstruction(
                    UefaOfficialClient(),
                    stage=options["stage"],
                    enpelotas_directory=options["enpelotas_dir"],
                )
            except ValidationError as exc:
                raise CommandError(str(exc)) from exc
        action = "Staged" if result["staged"] else "Validated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {result['fixtures']} fixtures and {result['player_rows']} player rows; "
            f"status={result['status']}; covered sources={len(result['covered_formula_sources'])}; "
            f"missing sources={','.join(result['missing_formula_sources']) or 'none'}; "
            f"partial sources={','.join(result['partial_formula_sources']) or 'none'}."
        ))
