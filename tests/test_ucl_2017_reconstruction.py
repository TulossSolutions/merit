from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from django.core.exceptions import ValidationError

from apps.football.models import (
    Competition,
    CompetitionSeason,
    Fixture,
    Player,
    PlayerTeamSeason,
    Position,
    Season,
    Team,
)
from apps.ingestion.models import FixtureStatReconstruction, RawProviderPayload
from apps.ingestion.services.ucl_2017_reconstruction import (
    FORMULA_SOURCES,
    TEAM_TOTAL_FIELDS,
    collect_reconstruction,
    normalize_official_evidence,
)


def _stat_row(player_id, team_id, values):
    return {
        "playerId": player_id,
        "teamId": team_id,
        "statistics": [{"name": key, "value": str(value)} for key, value in values.items()],
    }


def official_sample():
    home_id, away_id = "50051", "50139"
    match = {
        "id": "2021701",
        "kickOffTime": {"date": "2018-04-03"},
        "homeTeam": {"id": home_id, "internationalName": "Home"},
        "awayTeam": {"id": away_id, "internationalName": "Away"},
        "score": {"regular": {"home": 1, "away": 0}, "total": {"home": 1, "away": 0}},
    }
    lineups = {"matchId": "2021701"}
    player_stats = []
    team_stats = []
    for side, team_id in (("homeTeam", home_id), ("awayTeam", away_id)):
        field = []
        totals = {field_name: 0 for field_name in TEAM_TOTAL_FIELDS.values()}
        for index in range(11):
            player_id = f"{team_id}-{index}"
            field.append({
                "jerseyNumber": index + 1,
                "player": {
                    "id": player_id,
                    "internationalName": f"Player {player_id}",
                    "birthDate": f"1990-01-{index + 1:02d}",
                    "fieldPosition": "GOALKEEPER" if index == 0 else "MIDFIELDER",
                },
            })
            values = {source: 0 for source in TEAM_TOTAL_FIELDS}
            values["minutes_played_official"] = 90
            if index == 0 and team_id == home_id:
                values.update({"goals": 1, "assists": 1, "attempts": 2, "attempts_on_target": 1,
                               "dribbling": 1, "passes_attempted": 10, "passes_completed": 9,
                               "tackles": 1, "clearance_completed": 1, "recovered_ball": 2,
                               "saves": 1, "saves_on_penalty": 0, "passes_long_attempted": 2,
                               "passes_long_completed": 1})
            if index == 0 and team_id == away_id:
                values.update({"attempts": 1, "passes_attempted": 8, "passes_completed": 6,
                               "goals_conceded": 1, "passes_long_attempted": 1})
            player_stats.append(_stat_row(player_id, team_id, values))
            for player_field, team_field in TEAM_TOTAL_FIELDS.items():
                totals[team_field] += values[player_field]
        totals["goals"] = 1 if team_id == home_id else 0
        team_stats.append({
            "teamId": team_id,
            "statistics": [{"name": key, "value": str(value)} for key, value in totals.items()],
        })
        lineups[side] = {"team": {"id": team_id}, "field": field, "bench": []}
    return match, {
        "lineups": lineups,
        "player_statistics": player_stats,
        "team_statistics": team_stats,
        "player_profiles": [],
    }


def test_normalizer_maps_only_verified_sources_and_reconciles_totals():
    match, evidence = official_sample()
    result = normalize_official_evidence(match, evidence)
    assert len(result["participants"]) == 22
    assert set(result["verification"]["missing_formula_sources"]) == {
        "aerial_duels", "duels", "errors_leading_to_goal", "interceptions", "key_passes",
    }
    assert set(result["verification"]["covered_formula_sources"]) | set(result["verification"]["missing_formula_sources"]) == FORMULA_SOURCES
    metrics = {row["key"]: row for row in result["participants"][0]["metrics"]}
    assert metrics["shots"] == {
        "key": "shots", "value": "2", "numerator": "1", "denominator": "2",
        "source_type_id": "uefa_official:2021701:attempts",
    }
    assert metrics["passes"]["numerator"] == "9"
    assert "key_passes" not in metrics


def test_normalizer_rejects_team_total_mismatch():
    match, evidence = official_sample()
    bad = deepcopy(evidence)
    for item in bad["team_statistics"][0]["statistics"]:
        if item["name"] == "attempts":
            item["value"] = "99"
    with pytest.raises(ValidationError, match="does not reconcile attempts"):
        normalize_official_evidence(match, bad)


def test_normalizer_resolves_historical_uefa_player_id_from_profile():
    match, evidence = official_sample()
    lineup_profile = evidence["lineups"]["homeTeam"]["field"][0]["player"]
    evidence["player_statistics"][0]["playerId"] = "legacy-player-id"
    evidence["player_statistics"][0]["teamId"] = None
    evidence["player_profiles"] = [{
        "id": "legacy-player-id",
        "internationalName": lineup_profile["internationalName"],
        "birthDate": "1989-12-31",
        "fieldPosition": lineup_profile["fieldPosition"],
    }]

    result = normalize_official_evidence(match, evidence)

    assert result["participants"][0]["uefa_player_id"] == "legacy-player-id"
    assert result["participants"][0]["name"] == lineup_profile["internationalName"]


@pytest.mark.django_db
def test_staging_is_partial_auditable_and_idempotent():
    season = Season.objects.create(name="2017/18", slug="2017-18", starts_on=date(2017, 7, 1), ends_on=date(2018, 6, 30))
    competition = Competition.objects.create(
        provider="api_football", provider_id="2", name="UEFA Champions League", slug="ucl",
        competition_type=Competition.Type.UCL, participant_type=Competition.Participants.CLUB,
        format=Competition.Format.CUP, scope=Competition.Scope.CONTINENTAL, is_tracked=True,
    )
    edition = CompetitionSeason.objects.create(competition=competition, season=season, provider_season_id="2:2017")
    home = Team.objects.create(provider="api_football", provider_id="1", name="Home", slug="home")
    away = Team.objects.create(provider="api_football", provider_id="2", name="Away", slug="away")
    fixture = Fixture.objects.create(
        provider="api_football", provider_id="api1", competition_season=edition,
        home_team=home, away_team=away, starts_at=datetime(2018, 4, 3, 19, 45, tzinfo=timezone.utc),
        status=Fixture.Status.FINISHED, home_score=1, away_score=0, stage_name="Quarter-finals",
    )
    match, evidence = official_sample()
    for side, team in (("homeTeam", home), ("awayTeam", away)):
        for item in evidence["lineups"][side]["field"]:
            profile = item["player"]
            player = Player.objects.create(
                provider="api_football", provider_id=f"api-{profile['id']}", name=profile["internationalName"],
                birth_date=date.fromisoformat(profile["birthDate"]), primary_position=Position.MID,
            )
            PlayerTeamSeason.objects.create(player=player, team=team, season=season, competition_season=edition)

    class Client:
        MATCHES_URL = "https://match.uefa.com/v5/matches"
        LINEUPS_URL = "https://match.uefa.com/v5/matches/{match_id}/lineups"
        PLAYER_STATS_URL = "https://matchstats.uefa.com/v1/player-statistics/{match_id}"
        TEAM_STATS_URL = "https://matchstats.uefa.com/v1/team-statistics/{match_id}"
        PLAYERS_URL = "https://comp.uefa.com/v2/players"

        def list_2017_18_ucl_matches(self):
            return {"2021701": deepcopy(match)}

        def get_match_evidence(self, match_id):
            assert match_id == "2021701"
            return deepcopy(evidence)

    result = collect_reconstruction(Client(), fixture_ids=(("api1", "2021701"),), stage=True)
    assert result["status"] == "PARTIAL"
    record = FixtureStatReconstruction.objects.get(fixture=fixture)
    assert record.status == FixtureStatReconstruction.Status.PARTIAL
    assert len(record.normalized_payload["participants"]) == 22
    assert len(record.raw_payload_ids) == 4
    assert RawProviderPayload.objects.count() == 4

    collect_reconstruction(Client(), fixture_ids=(("api1", "2021701"),), stage=True)
    assert FixtureStatReconstruction.objects.count() == 1
    assert RawProviderPayload.objects.count() == 4
