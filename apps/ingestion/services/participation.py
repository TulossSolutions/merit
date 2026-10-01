"""Verified campaign minutes, deliberately separate from performance statistics."""
import hashlib
import json

from django.core.exceptions import ValidationError

from apps.ingestion.models import RawProviderPayload


def payload_checksum(payload, *, ensure_ascii=False):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=ensure_ascii).encode()).hexdigest()


def payload_checksums(payload):
    # Older fixture retention used ensure_ascii=True; archive evidence uses False.
    return {payload_checksum(payload, ensure_ascii=value) for value in (True, False)}


def derive_participation(data, team_id, duration, corroboration=None):
    """Reject ambiguous identities, substitutions and incomplete dismissal evidence."""
    if duration not in (90, 120):
        raise ValidationError("Verified match duration must be 90 or 120 minutes")
    team_id = str(team_id)
    lineups = [row for row in data.get("lineups", [])
               if str(row.get("team", {}).get("id")) == team_id]
    if len(lineups) != 1:
        raise ValidationError("A complete, unique winning-team lineup is required")
    lineup = lineups[0]
    starters = lineup.get("startXI", [])
    bench = lineup.get("substitutes", [])
    identities = [row.get("player", {}).get("id") for row in starters + bench]
    if (len(starters) != 11 or any(not isinstance(pid, int) or pid < 1 for pid in identities)
            or len(identities) != len(set(identities))):
        raise ValidationError("Lineup identities must be unique, nonzero; eleven starters required")
    names = {str(row["player"]["id"]): row["player"].get("name", "") for row in starters + bench}
    starter_ids = {str(row["player"]["id"]) for row in starters}
    bench_ids = {str(row["player"]["id"]) for row in bench}
    red_totals = [item.get("value") for row in data.get("statistics", [])
                  if str(row.get("team", {}).get("id")) == team_id
                  for item in row.get("statistics", []) if item.get("type") == "Red Cards"]
    red_count = red_totals[0] if len(red_totals) == 1 else None
    if red_count is None and corroboration:
        if not corroboration.get("reviewer") or not corroboration.get("source_url", "").startswith("https://"):
            raise ValidationError("Independent dismissal evidence needs a reviewer and source URL")
        red_count = corroboration.get("red_cards")
    if isinstance(red_count, bool) or not isinstance(red_count, int) or not 0 <= red_count <= 11:
        raise ValidationError("Independent red-card count is missing; incomplete events cannot prove absence")
    active = {pid: 0 for pid in starter_ids}
    minutes = {}
    incoming_ids = set()
    dismissals = 0
    last_minute = -1
    red_details = {"Red Card", "Second Yellow card", "Yellow-Red Card"}
    for event in data.get("events", []):
        if str(event.get("team", {}).get("id")) != team_id:
            continue
        substitution = event.get("type") == "subst"
        dismissal = event.get("type") == "Card" and event.get("detail") in red_details
        if not substitution and not dismissal:
            continue
        minute = event.get("time", {}).get("elapsed")
        if isinstance(minute, bool) or not isinstance(minute, int) or minute < last_minute or minute < 0:
            raise ValidationError("Participation timeline has invalid or unordered minutes")
        last_minute = minute
        minute = min(duration, minute)  # Stoppage time never adds campaign participation.
        outgoing = str(event.get("player", {}).get("id"))
        if outgoing not in active:
            raise ValidationError("An outgoing or dismissed player is not on the pitch")
        minutes[outgoing] = minute - active.pop(outgoing)
        if substitution:
            incoming = str(event.get("assist", {}).get("id"))
            if incoming not in bench_ids or incoming in incoming_ids or incoming in active:
                raise ValidationError("An incoming player is missing, duplicated or not on the bench")
            incoming_ids.add(incoming)
            active[incoming] = minute
        else:
            dismissals += 1
    if dismissals != red_count:
        raise ValidationError("Dismissal timeline does not reconcile with the independent red-card count")
    minutes.update({pid: duration - start for pid, start in active.items()})
    return [{"provider_id": pid, "name": names[pid], "minutes": value, "started": pid in starter_ids}
            for pid, value in sorted(minutes.items(), key=lambda item: int(item[0]))]


def retain_participation(fixture, team, source, corroboration=None):
    if (source.provider != fixture.provider or source.resource_type not in ("fixture", "trophy_fixture_source")
            or source.provider_resource_id != fixture.provider_id
            or source.payload_sha256 not in payload_checksums(source.payload)):
        raise ValidationError("Participation source identity or checksum is invalid")
    if team.pk not in (fixture.home_team_id, fixture.away_team_id):
        raise ValidationError("Participation team is not in the fixture")
    players = derive_participation(source.payload["fixture"], team.provider_id,
                                  fixture.available_minutes, corroboration)
    payload = {"fixture": fixture.provider_id, "team": team.provider_id,
               "duration": fixture.available_minutes, "players": players,
               "source_payload": source.pk, "source_sha256": source.payload_sha256,
               "method": "validated_lineups_and_events", "corroboration": corroboration}
    digest = payload_checksum(payload)
    existing = RawProviderPayload.objects.filter(provider=fixture.provider,
        resource_type="campaign_participation", provider_resource_id=f"{fixture.provider_id}:{team.provider_id}",
        payload_sha256__in=payload_checksums(payload)).first()
    if existing:
        return existing
    return RawProviderPayload.objects.create(provider=fixture.provider, resource_type="campaign_participation",
        provider_resource_id=f"{fixture.provider_id}:{team.provider_id}", request_path="local:verified-participation",
        payload=payload, payload_sha256=digest, http_status=200)


def participation_evidence(fixtures, team):
    """Revalidate append-only evidence; never trust a mere completeness flag."""
    missing = {fixture.provider_id: fixture for fixture in fixtures if not fixture.stats_ingested_at}
    keys = [f"{pid}:{team.provider_id}" for pid in missing]
    candidates = {}
    for record in RawProviderPayload.objects.filter(provider=team.provider,
            resource_type="campaign_participation", provider_resource_id__in=keys).order_by("-pk"):
        candidates.setdefault(record.payload.get("fixture"), record)
    sources = RawProviderPayload.objects.in_bulk([row.payload.get("source_payload") for row in candidates.values()])
    result = {}
    for pid, record in candidates.items():
        fixture = missing.get(pid)
        data = record.payload
        source = sources.get(data.get("source_payload"))
        if (fixture is None or source is None or data.get("team") != team.provider_id
                or data.get("duration") != fixture.available_minutes
                or data.get("method") != "validated_lineups_and_events"
                or record.payload_sha256 not in payload_checksums(data)
                or source.payload_sha256 != data.get("source_sha256")
                or source.payload_sha256 not in payload_checksums(source.payload)
                or source.provider_resource_id != fixture.provider_id or source.provider != fixture.provider
                or source.resource_type not in ("fixture", "trophy_fixture_source")):
            raise ValidationError("Verified participation evidence identity or checksum mismatch")
        expected = derive_participation(source.payload["fixture"], team.provider_id,
                                        fixture.available_minutes, data.get("corroboration"))
        if data.get("players") != expected:
            raise ValidationError("Verified participation minutes do not match the retained source")
        result[fixture.pk] = {"players": expected, "input_sha256": record.payload_sha256,
                              "source_sha256": source.payload_sha256, "record": record.pk}
    return result
