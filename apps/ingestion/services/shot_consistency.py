"""Bounded P2 refresh: repair shot ratios without touching goals or public snapshots."""
import hashlib
import json
from django.db import transaction
from django.db.models import F
from apps.football.award_periods import fixtures_for_award_period
from apps.football.models import PlayerFixtureMetric
from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers.base import ProviderFixture,ProviderRequestLimitReached,ProviderTeam


def inconsistent_shots(season):
    return PlayerFixtureMetric.objects.filter(player_fixture__fixture__in=fixtures_for_award_period(season),
        player_fixture__fixture__provider="api_football",metric_key="shots",numerator__gt=F("denominator"))


def retain(resource,fixture_id,payload):
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()
    return RawProviderPayload.objects.create(provider="api_football",resource_type=resource,provider_resource_id=fixture_id,
        request_path="/fixtures?ids=shot-consistency",payload=payload,payload_sha256=hashlib.sha256(encoded).hexdigest(),http_status=200)


def refresh_shot_conversion(provider,season):
    if provider.provider_name!="api_football": raise ValueError("Shot consistency refresh requires API-Football")
    rows=list(inconsistent_shots(season).select_related("player_fixture__fixture__competition_season","player_fixture__fixture__home_team",
        "player_fixture__fixture__away_team","player_fixture__player","player_fixture__team").order_by("player_fixture__fixture_id","pk"))
    fixtures={row.player_fixture.fixture_id:row.player_fixture.fixture for row in rows}
    if len(fixtures)>60: raise ValueError("Refresh exceeds the approved maximum of three 20-fixture batches")
    result={"fixtures":len(fixtures),"records":len(rows),"corrected":0,"quarantined":0,"calls":0}
    if not rows: return result
    provider.configure_request_limits(4,0.25)
    status=provider._request("status")["response"]["requests"]
    daily=int(status["limit_day"]); remaining=daily-int(status["current"])
    reserve=500 if daily>100 else 10
    required=(len(fixtures)+19)//20
    if remaining-reserve<required: raise ProviderRequestLimitReached("Insufficient daily quota above reserve for approved shot refresh")
    provider.configure_request_limits(min(4,1+required),6.1 if daily<=100 else 0.25)
    metadata=[]
    for fixture in fixtures.values():
        metadata.append(ProviderFixture(fixture.provider_id,fixture.competition_season.provider_season_id,
            ProviderTeam(fixture.home_team.provider_id,fixture.home_team.name),ProviderTeam(fixture.away_team.provider_id,fixture.away_team.name),
            fixture.starts_at,fixture.status,fixture.home_score,fixture.away_score,fixture.stage_name,fixture.round_name))
    for offset in range(0,len(metadata),20):
        bundles=provider.get_fixture_batch(metadata[offset:offset+20])
        for bundle in bundles:
            matching=[row for row in rows if row.player_fixture.fixture.provider_id==bundle.fixture.id]
            refreshed={(row.player.id,row.team_id):row for row in bundle.participations}
            with transaction.atomic():
                evidence=retain("shot_consistency_refresh",bundle.fixture.id,bundle.raw_payload)
                changes=[]
                for row in matching:
                    participation=refreshed.get((row.player_fixture.player.provider_id,row.player_fixture.team.provider_id))
                    shot=next((item for item in participation.metrics if item.key=="shots"),None) if participation else None
                    before={"value":str(row.value),"numerator":str(row.numerator),"denominator":str(row.denominator)}
                    # Existing goals are authoritative for this repair and are never rewritten.
                    goal=PlayerFixtureMetric.objects.filter(player_fixture=row.player_fixture,metric_key="goals",is_available=True).first()
                    numerator=goal.value if goal is not None and goal.value is not None else row.numerator
                    valid=shot is not None and shot.available and shot.value is not None and shot.value>=0 and numerator is not None and 0<=numerator<=shot.value
                    if shot is not None and shot.available and shot.value is not None: row.value=shot.value
                    row.numerator=numerator if valid else None
                    row.denominator=shot.value if valid else None
                    row.save(update_fields=["value","numerator","denominator","updated_at"])
                    result["corrected" if valid else "quarantined"]+=1
                    changes.append({"metric":row.pk,"before":before,"after":{"value":str(row.value),"numerator":str(row.numerator),"denominator":str(row.denominator)},"action":"corrected" if valid else "quarantined"})
                retain("shot_ratio_repair",bundle.fixture.id,{"refresh_payload_sha256":evidence.payload_sha256,"changes":changes,"goals_unchanged":True})
    result["calls"]=provider.requests_made
    return result
