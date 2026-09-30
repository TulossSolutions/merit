from unittest.mock import Mock, patch
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import json
import httpx
import pytest
from django.core.management import call_command
from django.test import override_settings
from apps.football.models import Competition, CompetitionSeason, Fixture, Player, PlayerFixture, PlayerFixtureMetric, Position, Season, Team
from apps.ingestion.providers.positions import normalize_position
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.rankings.views import HEADLINES

def test_position_priority_and_unknown():
    assert normalize_position("goalkeeper", "ST", Position.FWD) == Position.GK
    assert normalize_position(None, "CB", None) == Position.DEF
    assert normalize_position(None, None, None) == Position.UNKNOWN
    assert normalize_position("G") == Position.GK
    assert normalize_position("D") == Position.DEF
    assert normalize_position("M") == Position.MID
    assert normalize_position("F") == Position.FWD

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_position_list_headlines_match_non_null_api_football_metrics():
    stats = {
        "goals": {"total": 1, "assists": 1, "conceded": 0, "saves": 3},
        "shots": {"total": 4, "on": 2},
        "passes": {"total": 40, "key": 2, "accuracy": "80%"},
        "tackles": {"total": 3, "interceptions": 2},
        "duels": {"total": 8, "won": 5},
        "dribbles": {"attempts": 3, "success": 2},
        "penalty": {"saved": 1},
    }
    normalized = {metric.key: metric for metric in ApiFootballProvider()._metrics(stats)}
    formulas = []
    for path in Path("scoring_formulas").glob("*.json"):
        with path.open(encoding="utf8") as handle:
            formulas.append(json.load(handle))
    formula = max(formulas, key=lambda item: tuple(int(part) for part in item["version"].split(".")))
    derived_sources = {"clean_sheet": "goals_conceded"}
    for position, headline_keys in HEADLINES.items():
        definitions = {metric["key"]: metric for metric in formula["positions"][position]["metrics"]}
        for headline_key in headline_keys:
            assert headline_key in definitions, f"{position} headline {headline_key!r} is not defined by formula v{formula['version']}"
            definition = definitions[headline_key]
            source = derived_sources.get(definition["source"], definition["source"])
            assert source in normalized, f"{position} headline {headline_key!r} expects unavailable API-Football source {source!r}"
            assert normalized[source].value is not None, f"{position} headline {headline_key!r} normalized to a missing value"
            if definition["aggregation"] == "RATE":
                assert normalized[source].numerator is not None, f"{position} headline {headline_key!r} is missing its rate numerator"
                assert normalized[source].denominator is not None, f"{position} headline {headline_key!r} is missing its rate denominator"

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
@patch("apps.ingestion.providers.api_football.time.sleep")
def test_retries_429_and_5xx(sleep):
    responses = [Mock(status_code=429, headers={}), Mock(status_code=503, headers={}), Mock(status_code=200, headers={}, json=lambda: {"response": [], "errors": []})]
    for response in responses: response.raise_for_status = Mock()
    client = Mock(); client.get.side_effect = responses
    assert ApiFootballProvider(client)._request("fixtures") == {"response": [], "errors": []}
    assert client.get.call_count == 3
    assert client.get.call_args.kwargs["headers"] == {"x-apisports-key": "secret"}

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_no_retry_on_ordinary_4xx():
    request = httpx.Request("GET", "https://example.test")
    client = Mock(); client.get.return_value = httpx.Response(404, request=request)
    with pytest.raises(httpx.HTTPStatusError): ApiFootballProvider(client)._request("missing")
    assert client.get.call_count == 1

@override_settings(API_FOOTBALL_KEY="super-secret", API_FOOTBALL_BASE_URL="https://example.test")
@patch("apps.ingestion.providers.api_football.time.sleep")
def test_token_is_not_logged_during_retry(sleep, caplog):
    request=httpx.Request("GET","https://example.test")
    client=Mock(); client.get.return_value=httpx.Response(429,request=request)
    with pytest.raises(httpx.HTTPStatusError): ApiFootballProvider(client)._request("fixtures")
    assert "super-secret" not in caplog.text

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_http_200_provider_errors_are_rejected():
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:{"errors":{"fixture":"invalid"},"response":[]})
    with pytest.raises(ValueError,match="response errors"): ApiFootballProvider(client)._request("fixtures")

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_free_plan_quota_stops_before_another_request():
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={"x-ratelimit-requests-remaining":"0"},raise_for_status=Mock(),json=lambda:{"errors":[],"response":[]})
    provider=ApiFootballProvider(client)
    provider._request("fixtures")
    with pytest.raises(RuntimeError,match="quota exhausted"): provider._request("fixtures/players")
    assert client.get.call_count == 1

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_first_page_does_not_send_unsupported_page_parameter():
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:{"errors":[],"response":[],"paging":{"current":1,"total":1}})
    ApiFootballProvider(client)._all("leagues")
    assert client.get.call_args.kwargs["params"] == {}

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_non_iso3_country_code_is_not_persisted_as_iso3():
    payload={"errors":[],"response":[{"league":{"id":39,"name":"Premier League","type":"League"},"country":{"code":"GB-ENG"}}],"paging":{"current":1,"total":1}}
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:payload)
    assert ApiFootballProvider(client).list_competitions()[0].country_code is None

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_fixture_list_metadata_avoids_duplicate_fixture_request():
    fixture_payload={"fixture":{"id":10,"date":"2024-08-10T12:00:00+00:00","status":{"short":"FT"}},"league":{"id":39,"season":2024,"round":"Regular Season - 1"},"teams":{"home":{"id":1,"name":"Home"},"away":{"id":2,"name":"Away"}},"goals":{"home":1,"away":0}}
    players_payload={"errors":[],"response":[],"paging":{"current":1,"total":1}}
    client=Mock(); client.get.return_value=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:players_payload)
    provider=ApiFootballProvider(client); fixture=provider._normalize_fixture_meta(fixture_payload)
    bundle=provider.get_fixture_details(fixture)
    assert bundle.fixture.id == "10"
    assert client.get.call_count == 1
    assert client.get.call_args.kwargs["params"] == {"fixture":"10"}

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_configured_request_budget_is_a_hard_ceiling():
    response=Mock(status_code=200,headers={},raise_for_status=Mock(),json=lambda:{"errors":[],"response":[]})
    client=Mock(); client.get.return_value=response
    provider=ApiFootballProvider(client); provider.configure_request_limits(max_requests=1)
    provider._request("fixtures")
    with pytest.raises(ProviderRequestLimitReached,match="budget"):
        provider._request("fixtures/players")
    assert provider.requests_made == 1
    assert client.get.call_count == 1

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_verified_team_score_fills_missing_player_goals_with_zero():
    fixture={"fixture":{"id":10,"date":"2024-08-10T12:00:00+00:00","status":{"short":"FT"}},"league":{"id":39,"season":2024},"teams":{"home":{"id":1,"name":"Home"},"away":{"id":2,"name":"Away"}},"goals":{"home":1,"away":0}}
    def player(player_id, goals):
        return {"player":{"id":player_id,"name":str(player_id)},"statistics":[{"games":{"minutes":90,"position":"F"},"goals":{"total":goals}}]}
    payload={"fixture":fixture,"players":{"response":[{"team":{"id":1},"players":[player(1,1),player(2,None)]},{"team":{"id":2},"players":[player(3,None)]}]}}
    rows=ApiFootballProvider().normalize_fixture(payload).participations
    assert [(row.player.id, next(metric.value for metric in row.metrics if metric.key=="goals")) for row in rows]==[("1",1),("2",0),("3",0)]

@override_settings(API_FOOTBALL_KEY="secret", API_FOOTBALL_BASE_URL="https://example.test")
def test_unreconciled_team_score_does_not_infer_missing_goals():
    fixture={"fixture":{"id":10,"date":"2024-08-10T12:00:00+00:00","status":{"short":"FT"}},"league":{"id":39,"season":2024},"teams":{"home":{"id":1,"name":"Home"},"away":{"id":2,"name":"Away"}},"goals":{"home":2,"away":0}}
    payload={"fixture":fixture,"players":{"response":[{"team":{"id":1},"players":[{"player":{"id":1,"name":"One"},"statistics":[{"games":{"minutes":90,"position":"F"},"goals":{"total":1}}]},{"player":{"id":2,"name":"Two"},"statistics":[{"games":{"minutes":90,"position":"F"},"goals":{"total":None}}]}]}]}}
    rows=ApiFootballProvider().normalize_fixture(payload).participations
    assert not any(metric.key=="goals" for metric in rows[1].metrics)

@pytest.mark.django_db
def test_historical_goal_repair_is_previewable_conservative_and_idempotent():
    season=Season.objects.create(name="2024/25",slug="2024-25",starts_on=date(2024,8,1),ends_on=date(2025,5,31))
    comp=Competition.objects.create(provider="api_football",provider_id="39",name="League",slug="league",competition_type="DOMESTIC_LEAGUE",is_tracked=True)
    cs=CompetitionSeason.objects.create(competition=comp,season=season)
    home=Team.objects.create(provider="api_football",provider_id="1",name="Home",slug="home")
    away=Team.objects.create(provider="api_football",provider_id="2",name="Away",slug="away")
    fixture=Fixture.objects.create(provider="api_football",provider_id="10",competition_season=cs,home_team=home,away_team=away,starts_at=datetime(2024,8,10,tzinfo=timezone.utc),status=Fixture.Status.FINISHED,home_score=1,away_score=2,stats_ingested_at=datetime(2024,8,10,tzinfo=timezone.utc))
    rows=[]
    for index, team in enumerate((home,home,away,away),start=1):
        player=Player.objects.create(provider="api_football",provider_id=str(index),name=f"Player {index}",slug=f"player-{index}")
        rows.append(PlayerFixture.objects.create(fixture=fixture,player=player,team=team,opponent=away if team==home else home,position=Position.FWD,minutes=90))
    PlayerFixtureMetric.objects.create(player_fixture=rows[0],metric_key="goals",value=Decimal("1"))
    PlayerFixtureMetric.objects.create(player_fixture=rows[2],metric_key="goals",value=Decimal("1"))
    call_command("repair_missing_goals",season=season.slug)
    assert not PlayerFixtureMetric.objects.filter(player_fixture=rows[1],metric_key="goals").exists()
    call_command("repair_missing_goals",season=season.slug,apply=True)
    call_command("repair_missing_goals",season=season.slug,apply=True)
    assert list(PlayerFixtureMetric.objects.filter(player_fixture=rows[1],metric_key="goals").values_list("value",flat=True))==[Decimal("0")]
    assert not PlayerFixtureMetric.objects.filter(player_fixture=rows[3],metric_key="goals").exists()
