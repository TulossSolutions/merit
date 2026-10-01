"""Publish explicitly approved position exceptions without replacing history."""
import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.services import application_lock
from apps.football.award_periods import fixtures_for_award_period
from apps.football.models import Player,PlayerFixture
from apps.ingestion.services.positions import award_positions
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.scoring.models import ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.formulas import validate_formula


class Command(BaseCommand):
    help="Publish approved per-player position overrides at affected seasons' latest public cutoffs. No API calls or Elo rebuild."

    def add_arguments(self,parser):
        parser.add_argument("--formula",default="1.6")

    def handle(self,*args,**options):
        version=options["formula"]
        # A version is a name, never a caller-supplied filesystem path.
        if not isinstance(version,str) or not version.replace(".","",1).isdigit() or version.count(".")!=1:
            raise CommandError("Formula version must use major.minor format")
        path=settings.BASE_DIR/"scoring_formulas"/f"v{version.replace('.', '_')}.json"
        try:
            config=json.loads(path.read_text(encoding="utf8"))
            checksum=validate_formula(config)
            if config["version"]!=version: raise ValueError("Formula file version does not match requested version")
            overrides=config.get("position_overrides",{})
            if not overrides: raise ValueError("Formula has no approved position overrides")
            players=[]
            for identity,review in overrides.items():
                source=settings.BASE_DIR/review["source_path"]
                digest=hashlib.sha256(source.read_text(encoding="utf8").encode()).hexdigest()
                if digest!=review["source_sha256"]: raise ValueError("Position override approval source checksum mismatch")
                provider,_,provider_id=identity.partition(":")
                players.append(Player.objects.get(provider=provider,provider_id=provider_id))
            with application_lock("season_backfill") as acquired:
                if not acquired: raise CommandError("Backfill/replay is running; publish at a saved checkpoint")
                latest={}
                for old in RankingSnapshot.objects.filter(is_public=True).select_related("season").order_by(
                    "-season__starts_on","-cutoff_at","-published_at","-pk"):
                    latest.setdefault(old.season_id,old)
                affected=[old for old in latest.values() if PlayerFixture.objects.filter(
                    player__in=players,fixture__in=fixtures_for_award_period(old.season),
                    fixture__competition_season__competition__is_tracked=True,fixture__status="FINISHED",
                    fixture__stats_ingested_at__isnull=False,fixture__starts_at__lte=old.cutoff_at).exists()]
                if not affected: raise ValueError("No affected published seasons exist")
                messages=[]
                with transaction.atomic():
                    formula,created=ScoringFormula.objects.get_or_create(version=version,defaults={
                        "name":config["name"],"config":config,"checksum_sha256":checksum,"notes":config.get("notes","")})
                    if not created and (formula.checksum_sha256!=checksum or formula.config!=config):
                        raise ValueError("Immutable formula already exists with different rules")
                    positions=award_positions([player.pk for player in players],reviewed=True,overrides=overrides)
                    for old in affected:
                        if RankingSnapshot.objects.filter(season=old.season,formula=formula,cutoff_at=old.cutoff_at,is_public=True).exists():
                            messages.append(f"{old.season.slug}: v{version} already published")
                            continue
                        recompute_scores(old.season,formula,old.cutoff_at)
                        snapshot=publish(old.season,formula,old.cutoff_at,
                            allow_unavailable=old.coverage_summary.get("policy")=="covered_matches")
                        messages.append(f"{old.season.slug}: published corrected snapshot {snapshot.pk} ({snapshot.entries.count()} entries)")
                    for player in players:
                        Player.objects.filter(pk=player.pk).update(primary_position=positions[player.pk]["position"],updated_at=timezone.now())
                    ScoringFormula.objects.filter(is_active=True).exclude(pk=formula.pk).update(is_active=False)
                    ScoringFormula.objects.filter(pk=formula.pk,is_active=False).update(is_active=True,activated_at=timezone.now())
                # Page-cache keys are hashed and do not contain the season slug.
                # Clear after successful publication so the new category is visible immediately.
                cache.clear()
                for message in messages: self.stdout.write(message)
                self.stdout.write(f"Activated v{version}; updated {len(players)} approved player position(s); no API requests.")
        except (OSError,ValueError,ValidationError,Player.DoesNotExist) as exc:
            raise CommandError(f"Position correction publication blocked: {exc}") from exc
