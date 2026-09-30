from datetime import date,datetime,timezone
from unittest.mock import Mock,patch
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.football.award_periods import award_year,ensure_award_period,fixtures_for_award_period
from apps.football.models import Competition,CompetitionSeason,Fixture,PlayerFixtureMetric,Team,WinningCampaign
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.services.archive import ArchiveBackfill,CLUB_IDS,final_fixture,stage_category
from apps.ingestion.services.sync import ingest_fixture_bundle
from apps.ingestion.models import RawProviderPayload
from apps.scoring.services.achievements import season_achievement

pytestmark=pytest.mark.django_db


def metadata(value=1,league=39,year=2024,played="2024-08-10T12:00:00+00:00",stage="Regular Season - 1",status="FT",elapsed=90):
    return {"fixture":{"id":value,"date":played,"status":{"short":status,"elapsed":elapsed}},
        "league":{"id":league,"season":year,"round":stage},"teams":{"home":{"id":10,"name":"Home","winner":True},
        "away":{"id":20,"name":"Away","winner":False}},"goals":{"home":1,"away":0}}


def embedded_players():
    return [{"team":{"id":team},"players":[{"player":{"id":team+100,"name":f"Player {team}"},
        "statistics":[{"games":{"position":"F","minutes":90,"substitute":False},"goals":{"total":1 if team==10 else 0}}]}]} for team in (10,20)]


@override_settings(API_FOOTBALL_KEY="test")
def test_batch_reuses_same_normalizer_and_orders_response_chronologically():
    one=metadata(1); two=metadata(2)
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:{"response":[{**two,"players":embedded_players()},{**one,"players":embedded_players()}]})
    provider=ApiFootballProvider(client)
    result=provider.get_fixture_batch([provider._normalize_fixture_meta(one),provider._normalize_fixture_meta(two)])
    assert [bundle.fixture.id for bundle in result]==["1","2"]
    assert len(result[0].participations)==2
    assert client.get.call_args.kwargs["params"]=={"ids":"1-2"} and client.get.call_count==1


@override_settings(API_FOOTBALL_KEY="test")
@pytest.mark.parametrize("size",[0,21])
def test_batch_rejects_invalid_size_before_request(size):
    client=Mock(); provider=ApiFootballProvider(client)
    with pytest.raises(ValueError): provider.get_fixture_batch([provider._normalize_fixture_meta(metadata(index+1)) for index in range(size)])
    client.get.assert_not_called()


@override_settings(API_FOOTBALL_KEY="test")
def test_partial_batch_response_is_not_silently_accepted():
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:{"response":[metadata(1)]})
    provider=ApiFootballProvider(client)
    with pytest.raises(ValueError,match="does not match"):
        provider.get_fixture_batch([provider._normalize_fixture_meta(metadata(1)),provider._normalize_fixture_meta(metadata(2))])


@pytest.mark.parametrize("name,expected",[("Semi-finals","semi"),("Final","final"),("League A - Quarter-finals","quarter"),
    ("Qualifying Play-offs Path D - Semi-finals","qualifier"),("Group A - 1","group"),("Round of 32","playoff"),
    ("Play-offs A/B","qualifier"),("Play-offs B/C","qualifier"),("Play-offs C/D","qualifier"),
    ("Preliminary Round 1","qualifier"),("Preliminary Round 2","qualifier"),("Relegation Play-out","qualifier")])
def test_stage_contract_is_exact_and_qualification_not_title_final(name,expected):
    assert stage_category(name)==expected
    assert final_fixture([metadata(stage="Semi-finals")]) is None
    with pytest.raises(ValidationError): stage_category("Unannounced mystery stage")


@pytest.mark.parametrize("played,year",[(date(2026,7,19),2025),(date(2026,8,1),2026),(date(2022,12,18),2022),(date(2024,2,10),2023)])
def test_award_period_uses_actual_dates_not_provider_edition_label(played,year):
    assert award_year(played)==year


@override_settings(API_FOOTBALL_KEY="test")
def test_national_edition_can_supply_multiple_performance_periods_without_duplicate_players():
    title_period=ensure_award_period(2021); earlier_period=ensure_award_period(2020)
    comp=Competition.objects.create(provider="api_football",provider_id="5",name="UEFA Nations League",slug="nations",competition_type="CUP",participant_type="NATIONAL",is_tracked=True)
    edition=CompetitionSeason.objects.create(competition=comp,season=title_period,provider_season_id="5:2020")
    provider=ApiFootballProvider(Mock())
    for value,played in [(1,"2020-09-01T12:00:00+00:00"),(2,"2021-10-10T12:00:00+00:00")]:
        bundle=provider.normalize_fixture({"fixture":metadata(value,5,2020,played,"Group Stage - 1"),"players":{"response":embedded_players()}})
        ingest_fixture_bundle(bundle,edition,"api_football")
    assert fixtures_for_award_period(earlier_period).count()==1 and fixtures_for_award_period(title_period).count()==1
    assert edition.fixture_set.count()==2
    assert edition.fixture_set.first().playerfixture_set.first().player.__class__.objects.count()==2


@override_settings(API_FOOTBALL_KEY="test")
def test_empty_player_payload_is_retained_but_not_marked_ingested():
    period=ensure_award_period(2024)
    comp=Competition.objects.create(provider="api_football",provider_id="39",name="PL",slug="pl",competition_type="DOMESTIC_LEAGUE")
    edition=CompetitionSeason.objects.create(competition=comp,season=period,provider_season_id="39:2024")
    provider=ApiFootballProvider(Mock())
    fixture=ingest_fixture_bundle(provider.normalize_fixture({"fixture":metadata(),"players":{"response":[]}}),edition,"api_football")
    assert fixture.stats_ingested_at is None and fixture.player_data_unavailable_at is not None
    assert RawProviderPayload.objects.filter(resource_type="fixture").exists()
    corrected=ingest_fixture_bundle(provider.normalize_fixture({"fixture":metadata(),"players":{"response":embedded_players()}}),edition,"api_football")
    assert corrected.pk==fixture.pk and corrected.stats_ingested_at and corrected.player_data_unavailable_at is None
    assert PlayerFixtureMetric.objects.filter(metric_key="goals").count()==2


@override_settings(API_FOOTBALL_KEY="test")
@pytest.mark.parametrize("elapsed,expected",[(90,90),(120,120),(None,None)])
def test_penalty_duration_needs_elapsed_evidence(elapsed,expected):
    assert ApiFootballProvider(Mock())._normalize_fixture_meta(metadata(status="PEN",elapsed=elapsed)).available_minutes==expected


class FakeArchiveProvider(ApiFootballProvider):
    def __init__(self,daily=7500,current=0):
        super().__init__(Mock()); self.daily=daily; self.current=current
    def _request(self,path,params=None):
        self._before_request()
        if path=="status": return {"response":{"requests":{"limit_day":self.daily,"current":self.current}}}
        if path=="leagues":
            return {"response":[{"league":{"id":int(value),"name":"UEFA Champions League" if value=="2" else f"League {value}"},
                "seasons":[{"year":2024,"start":"2024-08-01","end":"2025-05-31","coverage":{"fixtures":{"statistics_players":True}}}]} for value in sorted(CLUB_IDS)]}
        if path=="fixtures":
            value=int(params["league"])
            return {"response":[metadata(value,value,stage="Final" if value==2 else "Regular Season - 1")]}
        raise AssertionError(path)


@override_settings(API_FOOTBALL_KEY="test")
@patch("apps.ingestion.providers.api_football.time.sleep")
def test_archive_preparation_is_resumable_cached_and_does_not_publish(_):
    now=datetime(2026,9,30,tzinfo=timezone.utc)
    first=FakeArchiveProvider()
    job=ArchiveBackfill(first,budget=100,now=now,report=Mock())
    assert job.run(prepare_only=True)["status"]=="prepared"
    assert CompetitionSeason.objects.count()==6 and Fixture.objects.count()==0
    assert not CompetitionSeason.objects.filter(context_policy__isnull=False).exists()  # Club performance context stays unchanged.
    assert job.priorities()[:3]==[2024,2026,2025]
    second=FakeArchiveProvider()
    ArchiveBackfill(second,budget=100,now=now,report=Mock()).run(prepare_only=True)
    assert second.requests_made==1  # Subscription status only: manifests and catalog reused.


@override_settings(API_FOOTBALL_KEY="test")
@patch("apps.ingestion.providers.api_football.time.sleep")
def test_paid_and_free_budgets_respect_reserve_and_pacing(_):
    paid=FakeArchiveProvider(current=6900)
    ArchiveBackfill(paid,report=Mock()).limits()
    assert paid.request_budget==101 and paid.min_request_interval>=0.25
    free=FakeArchiveProvider(daily=100,current=50)
    ArchiveBackfill(free,report=Mock()).limits()
    assert free.request_budget==41 and free.min_request_interval>=6.1


@override_settings(API_FOOTBALL_KEY="test")
def test_exhausted_quota_checkpoints_without_importing():
    result=ArchiveBackfill(FakeArchiveProvider(current=7499),report=Mock()).run()
    assert result["status"]=="checkpointed" and Fixture.objects.count()==0


@override_settings(API_FOOTBALL_KEY="test")
def test_nonannual_national_tournament_has_no_unearned_title_in_empty_period():
    comp=Competition.objects.create(provider="api_football",provider_id="1",name="World Cup",slug="world-cup",participant_type="NATIONAL",competition_type="CUP",is_tracked=True)
    season=ensure_award_period(2026)
    from apps.football.models import Player
    player=Player.objects.create(provider="api_football",provider_id="unused",name="Unused",slug="unused")
    value,_=season_achievement(player,season,datetime(2026,9,30,tzinfo=timezone.utc),{"enabled":True,"weight":"0.05","points_cap":"10","policy_versions":{f"api_football:{comp.provider_id}":"national-major-title-2.0"}})
    assert value==Decimal("0") and WinningCampaign.objects.count()==0


@override_settings(API_FOOTBALL_KEY="test")
@pytest.mark.parametrize("played,verified",[(1,False),(2,True)])
def test_league_leader_requires_complete_final_standings(played,verified):
    provider=FakeArchiveProvider(); job=ArchiveBackfill(provider,now=datetime(2026,9,30,tzinfo=timezone.utc),report=Mock())
    period=ensure_award_period(2024)
    comp=Competition.objects.create(provider="api_football",provider_id="39",name="PL",slug="pl",competition_type="DOMESTIC_LEAGUE",is_tracked=True)
    edition=CompetitionSeason.objects.create(competition=comp,season=period,provider_season_id="39:2024",starts_on=date(2024,8,1),ends_on=date(2025,5,31))
    rows=[metadata(value) for value in (1,2)]
    for row in rows: ingest_fixture_bundle(provider.normalize_fixture({"fixture":row,"players":{"response":embedded_players()}}),edition,"api_football")
    aliases={"regular season - 1":"group"}
    policy=job.policy("domestic",aliases)
    job.descriptors=[{"cs":edition,"rows":rows,"family":"domestic","policy":policy,"final":None,"digest":"test"}]
    standings={"response":[{"league":{"standings":[[{"rank":1,"team":{"id":10},"all":{"played":played}},{"rank":2,"team":{"id":20},"all":{"played":played}}]]}}]}
    with patch.object(provider,"_request",return_value=standings): job.verify_winners(2024)
    assert WinningCampaign.objects.exists()==verified
    if verified:
        result=WinningCampaign.objects.get()
        assert result.winner==Team.objects.get(provider_id="10") and result.expected_matches==2 and result.is_complete


@override_settings(API_FOOTBALL_KEY="test")
def test_existing_durations_are_repaired_from_manifest_without_player_calls():
    provider=FakeArchiveProvider(); job=ArchiveBackfill(provider,now=datetime(2026,9,30,tzinfo=timezone.utc),report=Mock())
    period=ensure_award_period(2024)
    comp=Competition.objects.create(provider="api_football",provider_id="39",name="PL",slug="pl",competition_type="DOMESTIC_LEAGUE",is_tracked=True)
    edition=CompetitionSeason.objects.create(competition=comp,season=period,provider_season_id="39:2024")
    row=metadata()
    fixture=ingest_fixture_bundle(provider.normalize_fixture({"fixture":row,"players":{"response":embedded_players()}}),edition,"api_football")
    Fixture.objects.filter(pk=fixture.pk).update(available_minutes=None)
    job.descriptors=[{"cs":edition,"rows":[row],"family":"domestic"}]
    with patch.object(provider,"get_fixture_batch") as batch:
        assert job.ingest_period(2024)["loaded"]==1
        batch.assert_not_called()
    fixture.refresh_from_db()
    assert fixture.available_minutes==90


def test_public_roadmap_marks_support_not_archive_completion():
    from django.test import Client
    response=Client().get("/roadmap/")
    assert response.status_code==200
    assert b"<del>Historical season backfill</del>" in response.content
    assert b"archive loading in progress" in response.content
    assert b"<del>Domestic cup" not in response.content


def test_public_methodology_explains_disabled_and_enabled_trophies():
    from django.test import Client
    from apps.scoring.models import ScoringFormula
    import json
    with open("scoring_formulas/v1_3.json",encoding="utf8") as handle: config=json.load(handle)
    client=Client()
    disabled=client.get("/methodology/")
    assert b"do not become invented points" in disabled.content and b"does not yet include trophies" in disabled.content
    ScoringFormula.objects.create(version="1.3",name="Trophies",config=config,checksum_sha256="test",is_active=True)
    enabled=client.get("/methodology/")
    assert b"includes a 5% achievement component" in enabled.content
    assert b"2026 World Cup belongs to 2025/26" in enabled.content
