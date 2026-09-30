"""Publish the approved corrections from local evidence, without provider requests."""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.core.services import application_lock
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.scoring.models import ScoringFormula
from apps.scoring.services.calculate import recompute_scores


class Command(BaseCommand):
    help="Publish v1.5 corrections at each published season's latest cutoff; preserve all previous snapshots. No API calls."

    def handle(self, *args, **options):
        formula=ScoringFormula.objects.get(version="1.5")
        if formula.config.get("position_source")!="api_football_profile_with_reviewed_fallback":
            raise CommandError("Correction publication requires the reviewed-position formula")
        with application_lock("season_backfill") as acquired:
            if not acquired: raise CommandError("A backfill is running; publish corrections at a saved checkpoint")
            latest={}
            for snapshot in RankingSnapshot.objects.filter(is_public=True).select_related("season").order_by(
                "-season__starts_on","-cutoff_at","-published_at","-pk"):
                latest.setdefault(snapshot.season_id,snapshot)
            for old in latest.values():
                if RankingSnapshot.objects.filter(season=old.season,formula=formula,cutoff_at=old.cutoff_at,is_public=True).exists():
                    self.stdout.write(f"{old.season.slug}: v1.5 already published")
                    continue
                try:
                    # Reuse retained Elo and verified campaign evidence. No fixture or provider changes.
                    with transaction.atomic():
                        recompute_scores(old.season,formula,old.cutoff_at)
                        snapshot=publish(old.season,formula,old.cutoff_at,
                            allow_unavailable=old.coverage_summary.get("policy")=="covered_matches")
                except (ValueError,ValidationError) as exc:
                    raise CommandError(f"{old.season.slug}: correction publication blocked: {exc}") from exc
                self.stdout.write(f"{old.season.slug}: published corrected snapshot {snapshot.pk} ({snapshot.entries.count()} entries)")
