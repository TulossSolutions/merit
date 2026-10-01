"""Apply the versioned, owner-approved exceptional trophy evidence."""
from datetime import datetime
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.services import application_lock
from apps.football.models import CampaignPolicy, Competition, CompetitionSeason, Fixture, Team, WinningCampaign
from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers import get_provider
from apps.ingestion.services.outcomes import (retain_fixture_review, retain_outcome_review,
    review_reference, reviewed_fixture_bundle, winner_fixture_ids)
from apps.ingestion.services.participation import (payload_checksums, retain_participation,
    retain_reviewed_participation)
from apps.ingestion.services.sync import ingest_fixture_bundle
from apps.scoring.services.campaigns import prepare_campaign_data


REVIEW_PATH = settings.BASE_DIR / "trophy_reviews" / "v1.json"


def retained_manifest(edition):
    record = RawProviderPayload.objects.filter(provider=edition.competition.provider,
        resource_type="archive_manifest", provider_resource_id=edition.provider_season_id).order_by(
        "-received_at", "-pk").first()
    if (record is None or record.payload_sha256 not in payload_checksums(record.payload)
            or not isinstance(record.payload.get("response"), list)):
        raise ValidationError(f"Verified archive manifest is unavailable: {edition.provider_season_id}")
    return record.payload["response"]


def policy_for(edition):
    if edition.context_policy_id:
        return edition.context_policy
    version = "ucl-title-2.0" if edition.competition.competition_type == Competition.Type.UCL else "domestic-title-2.0"
    return CampaignPolicy.objects.get(version=version)


def refresh_completeness(campaign, rows):
    expected = winner_fixture_ids(rows, campaign.winner.provider_id)
    if not expected:
        raise ValidationError(f"Winning campaign has no retained fixtures: {campaign.edition.provider_season_id}")
    data = prepare_campaign_data(campaign)
    complete = ({row.provider_id for row in data["fixtures"]} == set(expected)
        and all(row.status == Fixture.Status.FINISHED and row.available_minutes
            and row.pk in data["has_players"]
            and (row.stats_ingested_at or row.pk in data["participation_evidence"])
            for row in data["fixtures"]))
    campaign.expected_matches = len(expected)
    campaign.is_complete = complete
    campaign.full_clean()
    campaign.save(update_fields=["expected_matches", "is_complete", "updated_at"])
    return complete


class Command(BaseCommand):
    help = "Apply versioned trophy outcome and participation reviews; never fetch provider data or publish rankings."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            config = json.loads(REVIEW_PATH.read_text(encoding="utf8"))
            if set(config) != {"version", "outcomes", "fixture_reviews", "participation"} or config["version"] != "1.0":
                raise ValidationError("Unsupported trophy review file schema or version")
            counts = {key: len(config[key]) for key in ("outcomes", "fixture_reviews", "participation")}
            if not options["apply"]:
                self.stdout.write(f"Would apply trophy review v1.0: {counts}")
                return
            provider = get_provider()
            if provider.provider_name != "api_football":
                raise ValidationError("Trophy review v1.0 requires API-Football identities")
            with application_lock("season_backfill") as acquired:
                if not acquired:
                    raise CommandError("Archive backfill or weekly replay owns the shared lock")
                with transaction.atomic():
                    touched = set()
                    for item in config["fixture_reviews"]:
                        edition = CompetitionSeason.objects.select_related("competition").get(
                            provider_season_id=item["edition"], competition__provider=provider.provider_name)
                        source = RawProviderPayload.objects.filter(provider=provider.provider_name,
                            resource_type="trophy_fixture_source",
                            provider_resource_id=item["provider_fixture_id"]).order_by("-pk").first()
                        if source is None:
                            raise ValidationError(f"Retained reviewed-fixture source is missing: {item['provider_fixture_id']}")
                        payload = {**item, "source_payload": source.pk, "source_sha256": source.payload_sha256}
                        review = retain_fixture_review(edition, payload, source)
                        bundle = reviewed_fixture_bundle(edition, review, provider)
                        raw_exists = any((row.payload.get("operator_review") or {}).get("record") == review.pk
                            and (not bundle.participations or (row.payload.get("players") or {}).get("response"))
                            for row in RawProviderPayload.objects.filter(provider=provider.provider_name,
                                resource_type="fixture", provider_resource_id=item["provider_fixture_id"]).order_by("-pk")[:5])
                        ingest_fixture_bundle(bundle, edition,
                            provider.provider_name, "local:operator-reviewed-fixture", retain_raw=not raw_exists)
                        touched.add(edition.pk)
                    for item in config["outcomes"]:
                        edition = CompetitionSeason.objects.select_related("competition", "context_policy").get(
                            provider_season_id=item["edition"], competition__provider=provider.provider_name)
                        rows = retained_manifest(edition)
                        payload = {**item, "expected_fixture_ids": winner_fixture_ids(rows, item["winner"])}
                        review = retain_outcome_review(edition, payload, rows)
                        winner = Team.objects.get(provider=provider.provider_name, provider_id=item["winner"])
                        WinningCampaign.objects.update_or_create(edition=edition, defaults={
                            "winner": winner, "policy": policy_for(edition),
                            "awarded_at": datetime.fromisoformat(item["awarded_at"]),
                            "verified_at": timezone.now(), "evidence": json.dumps(review_reference(review), sort_keys=True),
                            "expected_matches": len(payload["expected_fixture_ids"]), "is_complete": False})
                        touched.add(edition.pk)
                    for item in config["participation"]:
                        fixture = Fixture.objects.select_related("competition_season", "home_team", "away_team").get(
                            provider=provider.provider_name, provider_id=item["fixture"])
                        team = Team.objects.get(provider=provider.provider_name, provider_id=item["team"])
                        if "reviewed_source" in item:
                            if set(item) != {"fixture", "team", "reviewed_source"}:
                                raise ValidationError("Reviewed participation item has unsupported fields")
                            retain_reviewed_participation(fixture, team, item["reviewed_source"])
                            touched.add(fixture.competition_season_id)
                            continue
                        source = RawProviderPayload.objects.filter(provider=provider.provider_name,
                            resource_type=item.get("source_resource", "fixture"),
                            provider_resource_id=item["fixture"]).order_by("-pk").first()
                        if source is None:
                            raise ValidationError(f"Retained participation source is missing: {item['fixture']}")
                        retain_participation(fixture, team, source, item["corroboration"])
                        touched.add(fixture.competition_season_id)
                    status = {}
                    for edition in CompetitionSeason.objects.filter(pk__in=touched).select_related("competition"):
                        campaign = WinningCampaign.objects.filter(edition=edition).select_related(
                            "winner", "edition__competition").first()
                        if campaign is None:
                            continue
                        status[edition.provider_season_id] = refresh_completeness(campaign, retained_manifest(edition))
            self.stdout.write(f"Applied trophy review v1.0: {counts}; campaign completeness={status}; API calls: 0")
        except (OSError, json.JSONDecodeError, ValidationError, CompetitionSeason.DoesNotExist,
                Team.DoesNotExist, CampaignPolicy.DoesNotExist, Fixture.DoesNotExist) as exc:
            raise CommandError(f"Trophy review blocked: {exc}") from exc
