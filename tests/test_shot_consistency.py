from datetime import date,datetime,timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock,patch
import pytest
from django.core.management import call_command
from django.test import override_settings
from apps.football.models import Competition,CompetitionSeason,Fixture,Player,PlayerFixture,PlayerFixtureMetric,Season,Team
from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.providers.base import ProviderFixtureBundle,ProviderMetric,ProviderParticipation,ProviderPlayer,ProviderRequestLimitReached
from apps.ingestion.services.shot_consistency import inconsistent_shots,refresh_shot_conversion

pytestmark=pytest.mark.django_db


@override_settings(API_FOOTBALL_KEY="test")
@pytest.mark.parametrize("shots,goals,valid",[(0,1,False),(2,3,False),(4,1,True),(0,0,True)])
def test_provider_keeps_counts_but_quarantines_impossible_conversion(shots,goals,valid):
    metrics={item.key:item for item in ApiFootballProvider(Mock())._metrics({"shots":{"total":shots},"goals":{"total":goals}})}
    assert metrics["goals"].value==goals and metrics["shots"].value==shots
    assert metrics["shots"].available
    assert metrics["shots"].numerator==(Decimal(goals) if valid else None)
    assert metrics["shots"].denominator==(Decimal(shots) if valid else None)


@pytest.fixture
def inconsistent_period():
    season=Season.objects.create(name="2026/27",slug="2026-27",starts_on=date(2026,8,1),ends_on=date(2027,5,31))
    comp=Competition.objects.create(provider="api_football",provider_id="2",name="UCL",slug="ucl",competition_type="UCL")
    edition=CompetitionSeason.objects.create(competition=comp,season=season,provider_season_id="2:2026")
    home=Team.objects.create(provider="api_football",provider_id="10",name="Home",slug="home")
    away=Team.objects.create(provider="api_football",provider_id="20",name="Away",slug="away")
    player=Player.objects.create(provider="api_football",provider_id="110",name="Player",slug="player")
    fixture=Fixture.objects.create(provider="api_football",provider_id="1",competition_season=edition,home_team=home,away_team=away,
        starts_at=datetime(2026,9,1,tzinfo=timezone.utc),status="FINISHED",home_score=1,away_score=0)
    pf=PlayerFixture.objects.create(fixture=fixture,player=player,team=home,opponent=away,minutes=90)
    goal=PlayerFixtureMetric.objects.create(player_fixture=pf,metric_key="goals",value=1)
    shot=PlayerFixtureMetric.objects.create(player_fixture=pf,metric_key="shots",value=0,numerator=1,denominator=0)
    return season,goal,shot


@override_settings(API_FOOTBALL_KEY="test")
@pytest.mark.parametrize("fresh_shots,expected",[(0,"quarantined"),(4,"corrected"),(None,"quarantined")])
def test_refresh_is_bounded_audited_and_preserves_goal_count(inconsistent_period,fresh_shots,expected):
    season,goal,shot=inconsistent_period
    provider=ApiFootballProvider(Mock())
    def request(path,params=None):
        provider._before_request()
        return {"response":{"requests":{"limit_day":7500,"current":100}}}
    def batch(fixtures):
        provider._before_request()
        metric=ProviderMetric("shots",Decimal(fresh_shots)) if fresh_shots is not None else None
        participation=ProviderParticipation(ProviderPlayer("110","Player"),"10","20","FWD",True,90,(metric,) if metric else ())
        return [ProviderFixtureBundle(fixtures[0],(participation,),{"fixture":{"id":"1"},"shots":fresh_shots})]
    with patch.object(provider,"_request",side_effect=request),patch.object(provider,"get_fixture_batch",side_effect=batch):
        result=refresh_shot_conversion(provider,season)
    assert result[expected]==1 and result["calls"]==2
    shot.refresh_from_db(); goal.refresh_from_db()
    assert goal.value==1
    assert shot.numerator==(Decimal("1") if expected=="corrected" else None)
    assert shot.denominator==(Decimal("4") if expected=="corrected" else None)
    assert inconsistent_shots(season).count()==0
    assert RawProviderPayload.objects.filter(resource_type="shot_consistency_refresh").count()==1
    assert RawProviderPayload.objects.get(resource_type="shot_ratio_repair").payload["goals_unchanged"] is True
    with patch.object(provider,"_request") as request:
        assert refresh_shot_conversion(provider,season)["calls"]==0
        request.assert_not_called()


@override_settings(API_FOOTBALL_KEY="test")
def test_quota_reserve_blocks_refresh_before_batch(inconsistent_period):
    season,_,shot=inconsistent_period
    provider=ApiFootballProvider(Mock())
    with patch.object(provider,"_request",return_value={"response":{"requests":{"limit_day":7500,"current":7000}}}),patch.object(provider,"get_fixture_batch") as batch:
        with pytest.raises(ProviderRequestLimitReached): refresh_shot_conversion(provider,season)
        batch.assert_not_called()
    shot.refresh_from_db()
    assert shot.numerator==1 and shot.denominator==0


def test_command_preview_makes_no_provider_calls(inconsistent_period):
    season,_,_=inconsistent_period
    output=StringIO()
    with patch("apps.ingestion.management.commands.refresh_shot_conversion.get_provider") as provider:
        call_command("refresh_shot_conversion",season=season.slug,stdout=output)
    provider.assert_not_called()
    assert '"provider_calls": 0' in output.getvalue()
