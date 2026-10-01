from datetime import date,datetime,timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client,override_settings
from django.utils import timezone as django_timezone

from apps.football.models import Competition,CompetitionSeason,Fixture,PlayerFixtureMetric,Season
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.services.archive import ArchiveBackfill
from apps.ingestion.services.sync import ingest_fixture_bundle
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.scoring.models import PlayerSeasonScore,ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula
from apps.scoring.services.quality import check_quality

pytestmark=pytest.mark.django_db


@pytest.fixture
def covered_period():
    with override_settings(API_FOOTBALL_KEY="test"):
        provider=ApiFootballProvider(Mock())
    season=Season.objects.create(name="2024/25",slug="2024-25",starts_on=date(2024,8,1),ends_on=date(2025,5,31),is_current=True)
    competition=Competition.objects.create(provider="api_football",provider_id="39",name="Premier League",slug="pl",competition_type="DOMESTIC_LEAGUE",participant_type="CLUB",is_tracked=True)
    edition=CompetitionSeason.objects.create(competition=competition,season=season,provider_season_id="39:2024")
    fixtures=[]
    for value,day in [(1,10),(2,11),(3,12)]:
        metadata={"fixture":{"id":value,"date":f"2024-08-{day}T12:00:00+00:00","status":{"short":"FT","elapsed":90}},
            "league":{"id":39,"season":2024,"round":"Regular Season - 1"},"teams":{"home":{"id":10,"name":"Home"},"away":{"id":20,"name":"Away"}},"goals":{"home":1,"away":0}}
        players=[{"team":{"id":team},"players":[{"player":{"id":team+100,"name":f"Player {team}"},"statistics":[{"games":{"position":"F","minutes":90},"goals":{"total":1 if team==10 else 0}}]}]} for team in (10,20)]
        if value==3: players=players[:1]  # Retained partial stats must not enter scores.
        fixtures.append(ingest_fixture_bundle(provider.normalize_fixture({"fixture":metadata,"players":{"response":players}}),edition,"api_football"))
    with open("scoring_formulas/v1_2.json",encoding="utf8") as handle: config=json.load(handle)
    config["version"]="9.9"
    config["achievements"]={"enabled":False,"weight":0,"points_cap":10,"policy_versions":{}}
    formula=ScoringFormula.objects.create(version=config["version"],name="Coverage test",config=config,checksum_sha256=validate_formula(config))
    cutoff=datetime(2024,8,13,23,tzinfo=timezone.utc)
    rebuild_elo(season)
    recompute_scores(season,formula,cutoff)
    cache.clear()
    return provider,season,formula,cutoff,fixtures


def test_strict_publication_still_blocks_known_missing_stats(covered_period):
    _,season,formula,cutoff,_=covered_period
    with pytest.raises(ValueError,match="completed_fixture_without_player_stats"):
        publish(season,formula,cutoff)
    assert RankingSnapshot.objects.count()==0


def test_p1_persists_coverage_and_does_not_score_partial_payloads(covered_period):
    _,season,formula,cutoff,fixtures=covered_period
    assert fixtures[-1].stats_ingested_at is None
    PlayerFixtureMetric.objects.filter(player_fixture__fixture=fixtures[-1],metric_key="goals").update(value=9999)
    recompute_scores(season,formula,cutoff)
    scores=PlayerSeasonScore.objects.filter(season=season)
    assert all(score.minutes==180 and score.appearances==2 for score in scores)
    assert all(score.metric_breakdown["goals_per90"]["raw_value"]<2 for score in scores)
    snapshot=publish(season,formula,cutoff,allow_unavailable=True)
    assert snapshot.coverage_summary=={"policy":"covered_matches","expected":3,"covered":2,"unavailable":1,"pending":0,"percent":66.7,"incomplete":True,
        "gaps":[{"competition":"Premier League","stage":"Regular Season - 1","fixtures":1}]}
    original=dict(snapshot.coverage_summary)
    Fixture.objects.filter(pk=fixtures[-1].pk).update(stats_ingested_at=django_timezone.now(),player_data_unavailable_at=None)
    snapshot.refresh_from_db()
    assert snapshot.coverage_summary==original  # Coverage is historical evidence, not a live count.
    with pytest.raises(ValidationError): snapshot.save()


def test_p1_does_not_waive_unprocessed_fixtures(covered_period):
    _,season,formula,cutoff,fixtures=covered_period
    Fixture.objects.filter(pk=fixtures[-1].pk).update(player_data_unavailable_at=None)
    with pytest.raises(ValueError,match="completed_fixture_without_player_stats"):
        publish(season,formula,cutoff,allow_unavailable=True)


def test_coverage_is_limited_to_the_snapshot_cutoff(covered_period):
    _,season,formula,_,_=covered_period
    cutoff=datetime(2024,8,11,23,tzinfo=timezone.utc)
    recompute_scores(season,formula,cutoff)
    snapshot=publish(season,formula,cutoff,allow_unavailable=True)
    assert snapshot.coverage_summary["expected"]==2
    assert snapshot.coverage_summary["covered"]==2
    assert not snapshot.coverage_summary["incomplete"] and not snapshot.coverage_summary["gaps"]
    assert b"Incomplete coverage:" not in Client().get("/").content


@pytest.mark.parametrize("error",["minutes","negative_metric","missing_elo"])
def test_p1_keeps_other_data_integrity_guards(covered_period,error):
    _,season,formula,cutoff,fixtures=covered_period
    fixture=fixtures[0]
    if error=="minutes": fixture.playerfixture_set.update(minutes=131)
    elif error=="negative_metric": PlayerFixtureMetric.objects.filter(player_fixture__fixture=fixture,metric_key="goals").update(value=Decimal("-1"))
    else: fixture.teamelosnapshot_set.all().delete()
    with pytest.raises(ValueError,match="Fatal data-quality errors"):
        publish(season,formula,cutoff,allow_unavailable=True)


def test_coverage_warning_on_full_pages_and_htmx_fragments(covered_period):
    _,season,formula,cutoff,_=covered_period
    snapshot=publish(season,formula,cutoff,allow_unavailable=True)
    player=snapshot.entries.first().player
    client=Client()
    routes=[("/",{},b"Read the methodology"),("/rankings/attackers/",{},b"How scores are calculated"),
        ("/rankings/attackers/",{"HTTP_HX_REQUEST":"true"},b"How scores are calculated"),
        (f"/players/{player.slug}/",{},b"Compare this player"),(f"/seasons/{season.slug}/",{},b"Choose a position"),
        (f"/compare/?a={player.slug}",{},b"compare-grid"),(f"/compare/?a={player.slug}",{"HTTP_HX_REQUEST":"true"},b"compare-grid")]
    for route,headers,final_content in routes:
        response=client.get(route,**headers)
        assert response.status_code==200
        assert b"Incomplete coverage: 2 of 3 completed matches (66.7%)" in response.content
        assert b"Trophy credit still requires a complete winning-team campaign" in response.content
        assert response.content.index(b"Incomplete coverage:") > response.content.index(final_content)


def test_legacy_snapshots_do_not_invent_coverage(covered_period):
    _,season,formula,cutoff,fixtures=covered_period
    snapshot=RankingSnapshot.objects.create(season=season,formula=formula,cutoff_at=cutoff,published_at=cutoff,is_public=True)
    assert snapshot.coverage_summary=={}
    assert b"Incomplete coverage:" not in Client().get("/").content
    Fixture.objects.filter(pk=fixtures[-1].pk).update(stats_ingested_at=django_timezone.now(),player_data_unavailable_at=None)
    assert not [issue for issue in check_quality(season,cutoff) if issue[0]=="ERROR"]


def test_archive_p1_still_requires_complete_trophy_evidence(covered_period):
    provider,season,formula,_,_=covered_period
    job=ArchiveBackfill(provider,report=Mock())
    job.descriptors=[{"family":"domestic","cs":SimpleNamespace(season_id=season.pk)} for _ in range(6)]
    summary={"expected":3,"loaded":2,"unavailable":1,"pending":0}
    with patch.object(job,"formula",return_value=formula),patch("apps.ingestion.services.archive.prepare_season_achievements",side_effect=ValidationError("campaign_incomplete")),patch("apps.ingestion.services.archive.recompute_scores") as recompute:
        result=job.publish_period(2024,summary)
    assert result["publication"]=="waiting_for_verified_trophies"
    recompute.assert_not_called()
    assert RankingSnapshot.objects.count()==0


def test_archive_p1_publishes_known_gaps_but_still_stops_pending(covered_period):
    provider,season,formula,_,_=covered_period
    job=ArchiveBackfill(provider,report=Mock())
    job.descriptors=[{"family":"domestic","cs":SimpleNamespace(season_id=season.pk)} for _ in range(6)]
    summary={"expected":3,"loaded":2,"unavailable":1,"pending":0}
    with patch.object(job,"formula",return_value=formula): result=job.publish_period(2024,summary)
    assert result["publication"]=="published" and result["coverage"]["unavailable"]==1
    assert job.publish_period(2024,{**summary,"pending":1})["publication"]=="waiting_for_complete_player_data"
