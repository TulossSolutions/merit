"""Campaign weights and trophy points below are test inputs, not approved production rules."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, override_settings

from apps.football.models import (CampaignPolicy, Competition, CompetitionSeason, Fixture, Player,
    PlayerFixture, PlayerTeamSeason, Season, Team, TeamEloSnapshot, WinningCampaign)
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.providers.base import ProviderCompetition
from apps.scoring.models import ScoringFormula
from apps.scoring.services.achievements import season_achievement, validate_achievement_config
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.campaigns import campaign_contribution, record_campaign_contribution, performance_context_weight
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.rankings.services.publish import publish
from apps.ingestion.models import RawProviderPayload
from apps.football.award_periods import summer_award_period

pytestmark=pytest.mark.django_db


@pytest.fixture
def campaign():
    season=Season.objects.create(name="2024/25",slug="2024-25",starts_on=date(2024,8,1),ends_on=date(2025,5,31))
    competition=Competition.objects.create(provider="api_football",provider_id="test-cup",name="Test Cup",slug="test-cup",
        competition_type="CUP",format="CUP",participant_type="NATIONAL",scope="GLOBAL",is_tracked=True)
    edition=CompetitionSeason.objects.create(competition=competition,season=season,provider_season_id="test:2025",
        edition_name="Test Cup 2025",starts_on=date(2025,6,1),ends_on=date(2025,6,30))
    winner=Team.objects.create(provider="api_football",provider_id="winner",name="Winner",slug="winner")
    opponent=Team.objects.create(provider="api_football",provider_id="other",name="Other",slug="other")
    player=Player.objects.create(provider="api_football",provider_id="player",name="Player",slug="player",primary_position="DEF")
    PlayerTeamSeason.objects.create(player=player,team=winner,season=season,competition_season=edition)
    policy=CampaignPolicy.objects.create(version="test-1",config={"stage_weights":{"group":"1","semi":"1.5","final":"1.75"},
        "stage_aliases":{"group":"group","semi-final":"semi","final":"final"},
        "performance_stage_weights":{"group":"1","semi":"1.08","final":"1.10"},"title_points":"4"})
    edition.context_policy=policy; edition.save()
    award=datetime(2025,6,30,22,tzinfo=timezone.utc)
    result=WinningCampaign.objects.create(edition=edition,winner=winner,policy=policy,awarded_at=award,
        verified_at=award,evidence="Test outcome evidence",expected_matches=3,is_complete=True)
    for index,(stage,duration,minutes) in enumerate((("Group",90,90),("Semi-final",90,45),("Final",120,120))):
        fixture=Fixture.objects.create(provider="api_football",provider_id=f"fixture-{index}",competition_season=edition,
            home_team=winner,away_team=opponent,starts_at=award-timedelta(days=3-index),status="FINISHED",
            stage_name=stage,available_minutes=duration,home_score=1,away_score=0,stats_ingested_at=award,neutral_venue=True)
        PlayerFixture.objects.create(player=player,fixture=fixture,team=winner,opponent=opponent,position="DEF",minutes=minutes)
    return result,player,award


def test_normalized_context_and_extra_time(campaign):
    result,player,cutoff=campaign
    value=campaign_contribution(result,player,cutoff)
    assert value["contribution"]==Decimal("3.5")/Decimal("4.25")
    assert value["breakdown"]["matches"][-1]["participation"]=="1"
    assert value["breakdown"]["matches"][-1]["available_minutes"]==120


def test_absence_reduces_contribution_and_ignores_other_team_minutes(campaign):
    result,player,cutoff=campaign
    semi=PlayerFixture.objects.get(player=player,fixture__stage_name="Semi-final")
    # Someone else's appearance proves the payload is present when our player missed the match.
    teammate=Player.objects.create(provider="api_football",provider_id="teammate",name="Teammate",slug="teammate")
    PlayerFixture.objects.create(player=teammate,fixture=semi.fixture,team=result.winner,opponent=semi.opponent,position="DEF",minutes=90)
    semi.team=semi.opponent; semi.opponent=result.winner; semi.save()
    assert campaign_contribution(result,player,cutoff)["contribution"]==Decimal("2.75")/Decimal("4.25")


def test_unused_squad_member_has_zero_not_full_credit(campaign):
    result,_,cutoff=campaign
    player=Player.objects.create(provider="api_football",provider_id="bench",name="Bench",slug="bench")
    PlayerTeamSeason.objects.create(player=player,team=result.winner,season=result.edition.season,competition_season=result.edition)
    assert campaign_contribution(result,player,cutoff)["contribution"]==0


@pytest.mark.parametrize("field,value,reason",[("available_minutes",None,"match_duration_missing"),
    ("stage_name","Unknown round","stage_not_configured"),("stats_ingested_at",None,"campaign_player_data_missing"),
    ("status","POSTPONED","campaign_fixture_not_finished")])
def test_missing_evidence_is_unavailable_not_zero(campaign,field,value,reason):
    result,player,cutoff=campaign
    fixture=Fixture.objects.filter(competition_season=result.edition).first()
    setattr(fixture,field,value); fixture.save()
    calculation=campaign_contribution(result,player,cutoff)
    assert calculation["contribution"] is None and calculation["reason"]==reason


def test_unknown_winner_incomplete_campaign_and_future_title(campaign):
    result,player,cutoff=campaign
    assert campaign_contribution(result,player,cutoff-timedelta(days=1))["reason"]=="title_not_yet_awarded"
    result.verified_at=None
    assert campaign_contribution(result,player,cutoff)["reason"]=="winner_unverified"
    result.verified_at=cutoff; result.is_complete=False
    assert campaign_contribution(result,player,cutoff)["reason"]=="campaign_incomplete"
    result.is_complete=True; result.expected_matches=4
    assert campaign_contribution(result,player,cutoff)["reason"]=="campaign_match_count_mismatch"


def test_full_participation_is_capped_at_one(campaign):
    result,player,cutoff=campaign
    PlayerFixture.objects.filter(player=player).update(minutes=130)
    assert campaign_contribution(result,player,cutoff)["contribution"]==1


def test_records_are_idempotent_and_corrections_do_not_rewrite_history(campaign):
    result,player,cutoff=campaign
    first=record_campaign_contribution(result,player,cutoff)
    assert record_campaign_contribution(result,player,cutoff).pk==first.pk
    PlayerFixture.objects.filter(player=player,fixture__stage_name="Semi-final").update(minutes=90)
    corrected=record_campaign_contribution(result,player,cutoff)
    assert corrected.pk!=first.pk and corrected.contribution==1
    first.refresh_from_db()
    assert first.contribution<1
    with pytest.raises(ValidationError): first.save()


def test_policy_immutable_and_stage_matching_exact(campaign):
    result,_,_=campaign
    semi=Fixture.objects.get(competition_season=result.edition,stage_name="Semi-final")
    assert performance_context_weight(semi)==Decimal("1.08")
    result.policy.config["stage_weights"]["semi"]="99"
    with pytest.raises(ValidationError): result.policy.save()


def test_award_period_can_include_summer_edition_without_duplicate_assignment(campaign):
    result,_,_=campaign
    result.edition.full_clean()
    assert result.edition.starts_on>result.edition.season.ends_on
    next_season=Season.objects.create(name="2025/26",slug="2025-26",starts_on=date(2025,8,1),ends_on=date(2026,5,31))
    duplicate=CompetitionSeason(competition=result.edition.competition,season=next_season,provider_season_id=result.edition.provider_season_id)
    with pytest.raises(ValidationError): duplicate.full_clean()


def test_command_records_contribution_without_provider_calls(campaign):
    result,player,cutoff=campaign
    output=StringIO()
    call_command("calculate_campaign_contribution",campaign=result.pk,player=player.slug,as_of=cutoff.isoformat(),stdout=output)
    assert "reason=complete" in output.getvalue()


def test_achievement_uses_configured_points_cap_and_ignores_future_titles(campaign):
    result,player,cutoff=campaign
    config={"enabled":True,"weight":"0.05","points_cap":"2","policy_versions":{"api_football:test-cup":"test-1"}}
    value,details=season_achievement(player,result.edition.season,cutoff,config)
    assert value==100 and len(details["campaigns"])==1
    value,details=season_achievement(player,result.edition.season,cutoff-timedelta(days=1),config)
    assert value==0 and details["campaigns"]==[]


def test_enabling_requires_explicit_scoring_choices():
    with pytest.raises(ValidationError):
        validate_achievement_config({"enabled":True,"weight":0,"points_cap":None,"policy_versions":{}})


def test_unverified_outcomes_block_achievement_scoring(campaign):
    result,player,cutoff=campaign
    result.verified_at=None; result.save()
    with pytest.raises(ValidationError): season_achievement(player,result.edition.season,cutoff,
        {"enabled":True,"weight":"0.05","points_cap":"4","policy_versions":{"api_football:test-cup":"test-1"}})


def test_national_elo_does_not_enter_club_league_strength(campaign):
    result,_,_=campaign
    rebuild_elo(result.edition.season)
    assert TeamEloSnapshot.objects.count()==6
    result.edition.refresh_from_db()
    assert result.edition.league_strength_multiplier==1
    assert TeamEloSnapshot.objects.order_by("fixture__starts_at").first().expected_result==Decimal("0.5")


@override_settings(API_FOOTBALL_KEY="test",API_FOOTBALL_BASE_URL="https://example.test")
def test_other_cups_are_not_classified_as_champions_league():
    payload={"response":[{"league":{"id":1,"name":"World Cup","type":"Cup"},"country":{}},
        {"league":{"id":2,"name":"UEFA Champions League","type":"Cup"},"country":{}}]}
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:payload)
    assert [row.kind for row in ApiFootballProvider(client).list_competitions()]==["CUP","UCL"]


def test_disabled_v1_3_reproduces_v1_2_scores_and_ranks():
    call_command("seed_demo_data",verbosity=0)
    season=Season.objects.get(is_current=True)
    cutoff=datetime(2026,9,30,23,59,59,tzinfo=timezone.utc)
    rebuild_elo(season)
    configs=[json.loads(open(path,encoding="utf8").read()) for path in ("scoring_formulas/v1_2.json","scoring_formulas/v1_3.json")]
    configs[1]["achievements"]={"enabled":False,"weight":0,"points_cap":None,"policy_versions":{}}
    baseline=deepcopy(configs[1]); baseline.pop("achievements")
    for key in ("version","name","notes"): baseline[key]=configs[0][key]
    assert baseline==configs[0]
    scores=[]
    for config in configs:
        formula=ScoringFormula.objects.create(version=config["version"],name=config["name"],config=config,checksum_sha256=validate_formula(config))
        rows=recompute_scores(season,formula,cutoff)
        scores.append([(row.player_id,row.final_score,row.eligible,row.metric_breakdown) for row in rows])
    assert scores[0]==scores[1]
    assert sorted(scores[0],key=lambda row: row[1] or 0)==sorted(scores[1],key=lambda row: row[1] or 0)


def test_achievement_page_stays_hidden_when_disabled():
    # No scoring or provider work is performed while serving the page.
    player=Player.objects.create(provider="mock",provider_id="1",name="Player",slug="no-trophy")
    response=Client().get(f"/players/{player.slug}/")
    assert response.status_code==200
    assert b"Title campaign contribution" not in response.content


@override_settings(CACHES={"default":{"BACKEND":"django.core.cache.backends.locmem.LocMemCache"}})
def test_enabled_scoring_publication_and_player_page_keep_auditable_credit(campaign):
    result,player,cutoff=campaign
    # Large minutes target is not relevant to this test of formula/publication plumbing.
    season=result.edition.season
    season.starts_on=date(2025,6,28); season.ends_on=date(2026,6,28); season.is_current=True; season.save()
    config=json.loads(open("scoring_formulas/v1_3.json",encoding="utf8").read())
    config["achievements"]={"enabled":True,"weight":"0.05","points_cap":"10","policy_versions":{"api_football:test-cup":"test-1"}}
    config["positions"]["DEF"]["metrics"][0]["aggregation"]="COUNT_PER90"
    config["positions"]["DEF"]["metrics"][0]["source"]="duels_won"
    for row in PlayerFixture.objects.filter(player=player):
        row.metrics.create(metric_key="duels_won",value=3)
    formula=ScoringFormula.objects.create(version=config["version"],name=config["name"],config=config,checksum_sha256=validate_formula(config))
    rebuild_elo(season)
    scores=recompute_scores(season,formula,cutoff)
    score=next(row for row in scores if row.player_id==player.pk)
    base=score.performance_score*Decimal("0.92")+score.availability_score*Decimal("0.08")
    assert abs(score.final_score-(base*Decimal("0.95")+score.achievement_score*Decimal("0.05")))<Decimal("0.0001")
    snapshot=publish(season,formula,cutoff)
    entry=RankingEntry.objects.get(snapshot=snapshot,player=player)
    assert entry.team_id is None  # National team is not presented as a club.
    assert entry.context_summary["achievements"]["campaigns"][0]["policy_version"]=="test-1"
    response=Client().get(f"/players/{player.slug}/")
    assert response.status_code==200
    assert b"Title campaign contribution" in response.content and b"82.35%" in response.content
    before=entry.context_summary
    PlayerFixture.objects.filter(player=player).update(minutes=90)
    recompute_scores(season,formula,cutoff+timedelta(days=1))
    entry.refresh_from_db()
    assert entry.context_summary==before
    assert RankingSnapshot.objects.filter(pk=snapshot.pk,is_public=True).exists()


def test_retained_payload_duration_preview_apply_and_penalty_unknown(campaign):
    result,_,_=campaign
    fixtures=list(Fixture.objects.filter(competition_season=result.edition).order_by("pk"))
    Fixture.objects.filter(pk__in=[row.pk for row in fixtures]).update(available_minutes=None)
    for row,status in zip(fixtures,("FT","AET","PEN"),strict=True):
        RawProviderPayload.objects.create(provider="api_football",resource_type="fixture",provider_resource_id=row.provider_id,
            request_path="/fixtures",payload={"fixture":{"fixture":{"status":{"short":status}}}},payload_sha256="test",http_status=200)
    output=StringIO()
    call_command("populate_fixture_durations",season=result.edition.season.slug,stdout=output)
    assert "Would apply 2 durations; 1 unresolved" in output.getvalue()
    assert Fixture.objects.filter(available_minutes__isnull=True).count()==3
    call_command("populate_fixture_durations",season=result.edition.season.slug,apply=True,stdout=StringIO())
    assert list(Fixture.objects.order_by("pk").values_list("available_minutes",flat=True))==[90,120,None]


def test_approved_policy_files_validate_and_import_idempotently():
    for version in ("domestic-title-1.0","ucl-title-1.0","national-major-title-1.0"):
        path=f"campaign_policies/{version}.json"
        call_command("import_campaign_policy",path,stdout=StringIO())
        call_command("import_campaign_policy",path,stdout=StringIO())
        assert CampaignPolicy.objects.filter(version=version).count()==1
    assert CampaignPolicy.objects.get(version="domestic-title-1.0").config["title_points"]=="3"
    assert CampaignPolicy.objects.get(version="ucl-title-1.0").config["title_points"]=="5"


def test_summer_tournament_targets_previous_period_not_next(campaign):
    result,_,_=campaign
    next_season=Season.objects.create(name="2025/26",slug="2025-26",starts_on=date(2025,8,1),ends_on=date(2026,5,31))
    assert summer_award_period(result.edition.starts_on,result.edition.ends_on)==result.edition.season
    result.edition.season=next_season
    with pytest.raises(ValidationError,match="season just ended"): result.edition.full_clean()


def test_missing_ended_edition_outcome_blocks_enabled_scoring(campaign):
    result,player,cutoff=campaign
    result.delete()
    with pytest.raises(ValidationError,match="Missing competition outcome"):
        season_achievement(player,CompetitionSeason.objects.get().season,cutoff,
            {"enabled":True,"weight":"0.05","points_cap":"10","policy_versions":{"api_football:test-cup":"test-1"}})


def test_incomplete_campaign_blocks_scoring_even_for_non_winning_player(campaign):
    result,_,cutoff=campaign
    outsider=Player.objects.create(provider="api_football",provider_id="outsider",name="Outsider",slug="outsider")
    Fixture.objects.filter(competition_season=result.edition).update(available_minutes=None)
    with pytest.raises(ValidationError,match="match_duration_missing"):
        season_achievement(outsider,result.edition.season,cutoff,
            {"enabled":True,"weight":"0.05","points_cap":"10","policy_versions":{"api_football:test-cup":"test-1"}})


def test_reference_sync_preserves_operator_tournament_classification(campaign):
    result,_,_=campaign
    provider=Mock(provider_name="api_football")
    provider.list_competitions.return_value=[ProviderCompetition("test-cup","Renamed Cup","FR","CUP")]
    with patch("apps.ingestion.management.commands.sync_competitions.get_provider",return_value=provider):
        call_command("sync_competitions",stdout=StringIO())
    result.edition.competition.refresh_from_db()
    competition=result.edition.competition
    assert competition.name=="Renamed Cup" and competition.participant_type=="NATIONAL"
    assert competition.scope=="GLOBAL" and competition.is_tracked
