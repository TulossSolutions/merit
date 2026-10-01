"""Explicit reviewed championship outcomes for exceptional completed editions."""
from copy import deepcopy
from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers.base import ProviderFixtureBundle
from .participation import payload_checksum, payload_checksums


OUTCOME_BASES = {"curtailed_season", "adjudicated_result", "on_pitch_result_pending_appeal"}


def winner_fixture_ids(rows, winner_id):
    return sorted(str(row["fixture"]["id"]) for row in rows
                  if any(str(team["id"]) == str(winner_id) for team in row["teams"].values())
                  and row["fixture"]["status"]["short"] not in ("CANC", "ABD"))


def validate_outcome_review(edition, payload, rows):
    required = {"edition", "winner", "awarded_at", "expected_fixture_ids", "reviewer", "source_urls", "basis"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValidationError("Outcome review must explicitly identify edition, winner, award date, campaign, reviewer and sources")
    if (payload["edition"] != edition.provider_season_id or not isinstance(payload["winner"], str)
            or not payload["winner"].strip() or not isinstance(payload["reviewer"], str)
            or not payload["reviewer"].strip()):
        raise ValidationError("Outcome review edition or reviewer is invalid")
    if payload["basis"] not in OUTCOME_BASES:
        raise ValidationError("Unknown reviewed outcome basis")
    if not isinstance(payload["source_urls"], list) or not payload["source_urls"] or any(
            not isinstance(url, str) or not url.startswith("https://") for url in payload["source_urls"]):
        raise ValidationError("Reviewed outcomes require explicit official source URLs")
    expected = payload["expected_fixture_ids"]
    if not expected or expected != winner_fixture_ids(rows, payload["winner"]):
        raise ValidationError("Reviewed winning campaign does not reconcile with the complete retained manifest")
    try:
        awarded = datetime.fromisoformat(payload["awarded_at"])
    except (TypeError, ValueError) as exc:
        raise ValidationError("Reviewed award date is invalid") from exc
    if timezone.is_naive(awarded):
        raise ValidationError("Reviewed award date must be timezone-aware")
    selected = [row for row in rows if str(row["fixture"]["id"]) in expected]
    if any(datetime.fromisoformat(row["fixture"]["date"]) > awarded for row in selected):
        raise ValidationError("Reviewed championship award predates a campaign match")
    return awarded


def reviewed_outcome(edition, rows):
    """An invalid newest review blocks verification instead of reverting to a guessed winner."""
    record = RawProviderPayload.objects.filter(provider=edition.competition.provider,
        resource_type="trophy_outcome_review", provider_resource_id=edition.provider_season_id).order_by("-pk").first()
    if record is None:
        return None
    if record.payload_sha256 not in payload_checksums(record.payload):
        raise ValidationError("Reviewed outcome evidence checksum mismatch")
    validate_outcome_review(edition, record.payload, rows)
    return record


def retain_outcome_review(edition, payload, rows):
    validate_outcome_review(edition, payload, rows)
    checksums = payload_checksums(payload)
    existing = RawProviderPayload.objects.filter(provider=edition.competition.provider,
        resource_type="trophy_outcome_review", provider_resource_id=edition.provider_season_id,
        payload_sha256__in=checksums).first()
    if existing:
        return existing
    return RawProviderPayload.objects.create(provider=edition.competition.provider,
        resource_type="trophy_outcome_review", provider_resource_id=edition.provider_season_id,
        request_path="local:operator-verified-outcome", payload=payload,
        payload_sha256=payload_checksum(payload), http_status=200)


def review_reference(record):
    return {"source": "operator_verified_outcome", "review_record": record.pk,
            "review_sha256": record.payload_sha256, "reviewer": record.payload["reviewer"],
            "source_urls": record.payload["source_urls"], "basis": record.payload["basis"]}


def validate_fixture_review(edition, payload, source):
    required = {"edition", "provider_fixture_id", "winner", "on_pitch_status", "home_score",
        "away_score", "available_minutes", "reviewer", "source_urls", "basis",
        "source_payload", "source_sha256"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValidationError("Fixture review must explicitly identify its source, outcome and reviewer")
    if (payload["edition"] != edition.provider_season_id or payload["basis"] not in OUTCOME_BASES
            or payload["on_pitch_status"] not in ("FT", "AET", "PEN")
            or payload["available_minutes"] not in (90, 120)
            or payload["source_payload"] != source.pk or payload["source_sha256"] != source.payload_sha256
            or source.provider != edition.competition.provider
            or source.resource_type != "trophy_fixture_source"
            or source.provider_resource_id != payload["provider_fixture_id"]
            or source.payload_sha256 not in payload_checksums(source.payload)):
        raise ValidationError("Reviewed fixture source identity, checksum or on-pitch status is invalid")
    if not isinstance(payload["reviewer"], str) or not payload["reviewer"].strip():
        raise ValidationError("Reviewed fixture needs an explicit reviewer")
    if not isinstance(payload["source_urls"], list) or not payload["source_urls"] or any(
            not isinstance(url, str) or not url.startswith("https://") for url in payload["source_urls"]):
        raise ValidationError("Reviewed fixture needs explicit official source URLs")
    row = source.payload.get("fixture")
    if not isinstance(row, dict):
        raise ValidationError("Reviewed fixture source payload is malformed")
    fixture = row.get("fixture") or {}
    league = row.get("league") or {}
    teams = row.get("teams") or {}
    ids = {str(item.get("id")) for item in teams.values()}
    if (str(fixture.get("id")) != payload["provider_fixture_id"]
            or f"{league.get('id')}:{league.get('season')}" != edition.provider_season_id
            or str(payload["winner"]) not in ids or fixture.get("status", {}).get("short") not in ("WO", "AWD")):
        raise ValidationError("Reviewed fixture does not reconcile with the retained provider record")
    scores = (row.get("score") or {}).get("extratime" if payload["on_pitch_status"] == "AET" else "fulltime") or {}
    if scores.get("home") != payload["home_score"] or scores.get("away") != payload["away_score"]:
        raise ValidationError("Reviewed on-pitch score does not reconcile with retained score evidence")
    home_id = str((teams.get("home") or {}).get("id"))
    score_winner = home_id if payload["home_score"] > payload["away_score"] else str(
        (teams.get("away") or {}).get("id")) if payload["away_score"] > payload["home_score"] else None
    if score_winner != payload["winner"]:
        raise ValidationError("Reviewed winner does not match the on-pitch score")
    return row


def retain_fixture_review(edition, payload, source):
    validate_fixture_review(edition, payload, source)
    checksums = payload_checksums(payload)
    existing = RawProviderPayload.objects.filter(provider=edition.competition.provider,
        resource_type="trophy_fixture_review", provider_resource_id=payload["provider_fixture_id"],
        payload_sha256__in=checksums).first()
    if existing:
        return existing
    return RawProviderPayload.objects.create(provider=edition.competition.provider,
        resource_type="trophy_fixture_review", provider_resource_id=payload["provider_fixture_id"],
        request_path="local:operator-reviewed-fixture", payload=payload,
        payload_sha256=payload_checksum(payload), http_status=200)


def reviewed_fixture_bundle(edition, record, provider):
    if (record.resource_type != "trophy_fixture_review"
            or record.payload_sha256 not in payload_checksums(record.payload)):
        raise ValidationError("Reviewed fixture record checksum is invalid")
    source = RawProviderPayload.objects.filter(pk=record.payload.get("source_payload")).first()
    if source is None:
        raise ValidationError("Reviewed fixture source no longer exists")
    row = deepcopy(validate_fixture_review(edition, record.payload, source))
    review = record.payload
    row["fixture"]["status"] = {"long": "Match Finished", "short": review["on_pitch_status"],
        "elapsed": review["available_minutes"]}
    row["goals"] = {"home": review["home_score"], "away": review["away_score"]}
    for side in ("home", "away"):
        row["teams"][side]["winner"] = str(row["teams"][side]["id"]) == review["winner"]
    raw = {"fixture": row, "players": {"response": []}, "operator_review": {
        "record": record.pk, "review_sha256": record.payload_sha256,
        "source_payload": source.pk, "source_sha256": source.payload_sha256}}
    fixture = provider._normalize_fixture_meta(row)
    if fixture.status != "FINISHED" or fixture.available_minutes != review["available_minutes"]:
        raise ValidationError("Reviewed fixture did not normalize to a completed match")
    return ProviderFixtureBundle(fixture, tuple(), raw)
