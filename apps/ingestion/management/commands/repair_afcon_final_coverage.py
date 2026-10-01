"""Restore retained AFCON-final player statistics and publish an additive correction."""
from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.services import application_lock
from apps.football.models import CompetitionSeason, Fixture, Player, Position, Season
from apps.ingestion.models import PlayerPositionProfile, RawProviderPayload
from apps.ingestion.providers import get_provider
from apps.ingestion.services.outcomes import reviewed_fixture_bundle
from apps.ingestion.services.sync import ingest_fixture_bundle
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.scoring.models import ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo


FIXTURE_ID = "1508003"
EDITION_ID = "6:2025"
SEASON_SLUG = "2025-26"
FORMULA_VERSION = "1.6"
CORRECTION_CUTOFF = datetime(2026, 7, 20, tzinfo=timezone.utc)
EXPECTED_SOURCE_ROWS = 51
EXPECTED_APPEARANCES = 33
EXPECTED_MINUTE_DELTAS = {"13": 0, "31": -14}


def validated_bundle():
    provider = get_provider()
    if provider.provider_name != "api_football":
        raise ValidationError("AFCON final repair requires API-Football identities")
    edition = CompetitionSeason.objects.select_related("competition", "season").get(
        provider_season_id=EDITION_ID, competition__provider=provider.provider_name)
    if edition.season.slug != SEASON_SLUG:
        raise ValidationError("AFCON final edition belongs to an unexpected award season")
    review = RawProviderPayload.objects.filter(provider=provider.provider_name,
        resource_type="trophy_fixture_review", provider_resource_id=FIXTURE_ID).order_by("-pk").first()
    if review is None:
        raise ValidationError("Reviewed AFCON final outcome is missing")
    bundle = reviewed_fixture_bundle(edition, review, provider)
    summaries = bundle.raw_payload["operator_review"]["player_stats"]
    if (sum(row["source_squad_rows"] for row in summaries) != EXPECTED_SOURCE_ROWS
            or sum(row["normalized_appearances"] for row in summaries) != EXPECTED_APPEARANCES
            or {row["team_id"]: row["minute_delta"] for row in summaries} != EXPECTED_MINUTE_DELTAS):
        raise ValidationError("Retained AFCON final player-stat evidence changed from the reviewed source")
    identities = {str(row.player.id) for row in bundle.participations}
    players = {row.provider_id: row for row in Player.objects.filter(
        provider=provider.provider_name, provider_id__in=identities)}
    if set(players) != identities:
        raise ValidationError("AFCON final contains unresolved player identities")
    profiles = PlayerPositionProfile.objects.filter(player__in=players.values()).select_related("source_payload")
    valid = {row.player.provider_id for row in profiles if row.position != Position.UNKNOWN
        and row.source_payload_id and not row.reason}
    if valid != identities:
        raise ValidationError("AFCON final contains players without verified profile positions")
    return edition, review, bundle


class Command(BaseCommand):
    help = "Restore retained player statistics for the reviewed 2025 AFCON final; no API calls."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            edition, review, bundle = validated_bundle()
            if not options["apply"]:
                self.stdout.write(
                    f"Would restore {EXPECTED_APPEARANCES} appearances for AFCON fixture {FIXTURE_ID} "
                    f"and publish {SEASON_SLUG} at {CORRECTION_CUTOFF.isoformat()}; API calls: 0")
                return
            with application_lock("season_backfill") as acquired:
                if not acquired:
                    raise CommandError("Archive backfill or weekly replay owns the shared lock")
                with transaction.atomic():
                    season = Season.objects.get(slug=SEASON_SLUG)
                    formula = ScoringFormula.objects.get(version=FORMULA_VERSION, is_active=True)
                    fixture = Fixture.objects.get(provider="api_football", provider_id=FIXTURE_ID)
                    existing = RankingSnapshot.objects.filter(season=season, formula=formula,
                        cutoff_at=CORRECTION_CUTOFF, is_public=True).first()
                    if existing:
                        if not fixture.stats_ingested_at or fixture.playerfixture_set.filter(minutes__gt=0).count() != EXPECTED_APPEARANCES:
                            raise ValidationError("Correction snapshot exists without the verified AFCON appearances")
                        self.stdout.write(f"AFCON final coverage correction already published as snapshot {existing.pk}; API calls: 0")
                        return
                    raw_exists = any((row.payload.get("operator_review") or {}).get("record") == review.pk
                        and (row.payload.get("players") or {}).get("response")
                        for row in RawProviderPayload.objects.filter(provider="api_football", resource_type="fixture",
                            provider_resource_id=FIXTURE_ID).order_by("-pk")[:5])
                    fixture = ingest_fixture_bundle(bundle, edition, "api_football",
                        "local:operator-reviewed-fixture-player-restoration", retain_raw=not raw_exists)
                    if (not fixture.stats_ingested_at
                            or fixture.playerfixture_set.filter(minutes__gt=0).count() != EXPECTED_APPEARANCES):
                        raise ValidationError("AFCON final player-stat ingestion did not produce complete team coverage")
                    rebuild_elo(season)
                    recompute_scores(season, formula, CORRECTION_CUTOFF)
                    snapshot = publish(season, formula, CORRECTION_CUTOFF, allow_unavailable=True)
                    if any(gap.get("competition") == "Africa Cup of Nations" and gap.get("stage") == "Final"
                            for gap in snapshot.coverage_summary.get("gaps", [])):
                        raise ValidationError("Corrected snapshot still reports the AFCON final as unavailable")
            self.stdout.write(self.style.SUCCESS(
                f"Restored AFCON final fixture {FIXTURE_ID}; published snapshot {snapshot.pk}; API calls: 0"))
        except (ValidationError, CompetitionSeason.DoesNotExist, Fixture.DoesNotExist,
                Player.DoesNotExist, Season.DoesNotExist, ScoringFormula.DoesNotExist) as exc:
            raise CommandError(f"AFCON final coverage repair blocked: {exc}") from exc
