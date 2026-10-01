from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
import json

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings

from apps.football.models import (CampaignPolicy, Competition, CompetitionSeason, Fixture, Player,
    PlayerTeamSeason, Season, Team, WinningCampaign)
from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.services.outcomes import (retain_fixture_review, retain_outcome_review,
    reviewed_fixture_bundle, reviewed_outcome, winner_fixture_ids)
from apps.ingestion.services.participation import (derive_participation, participation_evidence,
    payload_checksum, retain_participation, retain_reviewed_participation,
    validate_reviewed_participation)
from apps.ingestion.services.sync import ingest_fixture_bundle
from apps.scoring.services.campaigns import campaign_contribution


pytestmark = pytest.mark.django_db


def setup_edition(provider_season_id="2:2017"):
    season = Season.objects.create(name="2017/18", slug="2017-18", starts_on=date(2017, 8, 1),
        ends_on=date(2018, 5, 31))
    competition = Competition.objects.create(provider="api_football",
        provider_id=provider_season_id.split(":")[0], name="Reviewed Cup", slug="reviewed-cup",
        competition_type="UCL", format="CUP", is_tracked=True)
    policy = CampaignPolicy.objects.create(version="review-test", config={
        "stage_weights": {"final": "1.75"}, "stage_aliases": {"final": "final"},
        "performance_stage_weights": {"final": "1.10"}, "title_points": "5"})
    edition = CompetitionSeason.objects.create(competition=competition, season=season,
        provider_season_id=provider_season_id, starts_on=season.starts_on, ends_on=season.ends_on)
    winner = Team.objects.create(provider="api_football", provider_id="13", name="Winner", slug="winner")
    opponent = Team.objects.create(provider="api_football", provider_id="31", name="Other", slug="other")
    return season, edition, policy, winner, opponent


def source_row(fixture_id="1", league=2, year=2017, status="FT", duration=90):
    starters = [{"player": {"id": value, "name": f"Player {value}"}} for value in range(100, 111)]
    bench = [{"player": {"id": 111, "name": "Player 111"}}]
    return {"fixture": {"id": int(fixture_id), "date": "2018-05-26T19:00:00+00:00",
            "status": {"short": status, "elapsed": duration}},
        "league": {"id": league, "season": year, "round": "Final"},
        "teams": {"home": {"id": 13, "name": "Winner", "winner": status != "WO"},
            "away": {"id": 31, "name": "Other", "winner": False}},
        "goals": {"home": 1, "away": 0},
        "score": {"fulltime": {"home": 0 if status == "WO" else 1, "away": 0},
            "extratime": {"home": 1, "away": 0}},
        "lineups": [{"team": {"id": 13}, "startXI": starters, "substitutes": bench}],
        "events": [{"time": {"elapsed": 60}, "team": {"id": 13},
            "player": {"id": 100}, "assist": {"id": 111}, "type": "subst", "detail": "Substitution 1"}],
        "statistics": [{"team": {"id": 13}, "statistics": [{"type": "Red Cards", "value": 0}]}]}


def raw_payload(resource, fixture_id, row):
    payload = {"fixture": row, "players": {"response": [{"team": {"id": 13}, "players": []}]}}
    return RawProviderPayload.objects.create(provider="api_football", resource_type=resource,
        provider_resource_id=fixture_id, request_path="test", payload=payload,
        payload_sha256=payload_checksum(payload), http_status=200)


def test_verified_participation_unblocks_trophy_without_inventing_performance_rows():
    season, edition, policy, winner, opponent = setup_edition()
    player = Player.objects.create(provider="api_football", provider_id="100", name="Player 100", slug="p100")
    PlayerTeamSeason.objects.create(player=player, team=winner, season=season, competition_season=edition)
    fixture = Fixture.objects.create(provider="api_football", provider_id="1", competition_season=edition,
        home_team=winner, away_team=opponent, starts_at=datetime(2018, 5, 26, 19, tzinfo=timezone.utc),
        status="FINISHED", stage_name="Final", home_score=1, away_score=0, available_minutes=90)
    source = raw_payload("fixture", "1", source_row())
    evidence = retain_participation(fixture, winner, source)
    campaign = WinningCampaign.objects.create(edition=edition, winner=winner, policy=policy,
        awarded_at=datetime(2018, 5, 26, 22, tzinfo=timezone.utc), verified_at=datetime.now(timezone.utc),
        evidence="verified", expected_matches=1, is_complete=True)
    result = campaign_contribution(campaign, player, campaign.awarded_at)
    assert abs(result["contribution"] - Decimal("60") / Decimal("90")) < Decimal("0.0000000001")
    assert result["breakdown"]["matches"][0]["participation_evidence"]["record"] == evidence.pk
    fixture.refresh_from_db()
    assert fixture.stats_ingested_at is None and fixture.playerfixture_set.count() == 0


def test_participation_revalidates_source_and_rejects_ambiguous_evidence():
    _, edition, _, winner, opponent = setup_edition()
    fixture = Fixture.objects.create(provider="api_football", provider_id="1", competition_season=edition,
        home_team=winner, away_team=opponent, starts_at=datetime(2018, 5, 26, 19, tzinfo=timezone.utc),
        status="FINISHED", stage_name="Final", available_minutes=90)
    source = raw_payload("fixture", "1", source_row())
    retain_participation(fixture, winner, source)
    RawProviderPayload.objects.filter(pk=source.pk).update(payload={"fixture": {}})
    with pytest.raises(ValidationError, match="checksum mismatch"):
        participation_evidence([fixture], winner)
    row = source_row()
    row["lineups"].append(row["lineups"][0])
    with pytest.raises(ValidationError, match="unique winning-team lineup"):
        derive_participation(row, "13", 90)


def reviewed_source(players=None):
    players = players or [{"provider_id": str(value), "name": f"Player {value}",
        "minutes": 90, "started": True} for value in range(100, 111)]
    return {"players": players, "reviewer": "reviewer",
        "source_urls": ["https://official.example/match", "https://archive.example/match"],
        "retrieved_at": "2026-10-01", "red_cards": 0,
        "normalization": {"minute_source": "rsssf", "stoppage_time": "clamped_to_match_duration",
            "notes": "No source discrepancy."}}


def test_reviewed_external_participation_is_auditable_and_not_performance_data():
    season, edition, policy, winner, opponent = setup_edition()
    player = Player.objects.create(provider="api_football", provider_id="100", name="Player 100", slug="p100")
    PlayerTeamSeason.objects.create(player=player, team=winner, season=season, competition_season=edition)
    fixture = Fixture.objects.create(provider="api_football", provider_id="1", competition_season=edition,
        home_team=winner, away_team=opponent, starts_at=datetime(2018, 5, 26, 19, tzinfo=timezone.utc),
        status="FINISHED", stage_name="Final", home_score=1, away_score=0, available_minutes=90)
    evidence = retain_reviewed_participation(fixture, winner, reviewed_source())
    campaign = WinningCampaign.objects.create(edition=edition, winner=winner, policy=policy,
        awarded_at=datetime(2018, 5, 26, 22, tzinfo=timezone.utc), verified_at=datetime.now(timezone.utc),
        evidence="verified", expected_matches=1, is_complete=True)
    result = campaign_contribution(campaign, player, campaign.awarded_at)
    assert result["contribution"] == Decimal("1")
    assert result["breakdown"]["matches"][0]["participation_evidence"]["record"] == evidence.pk
    source = RawProviderPayload.objects.get(pk=evidence.payload["source_payload"])
    assert source.resource_type == "trophy_participation_review"
    assert fixture.playerfixture_set.count() == 0 and fixture.stats_ingested_at is None


def test_reviewed_external_participation_rejects_invalid_team_minutes_and_source_tampering():
    _, edition, _, winner, opponent = setup_edition()
    fixture = Fixture.objects.create(provider="api_football", provider_id="1", competition_season=edition,
        home_team=winner, away_team=opponent, starts_at=datetime(2018, 5, 26, 19, tzinfo=timezone.utc),
        status="FINISHED", stage_name="Final", available_minutes=90)
    review = reviewed_source()
    review["players"][0]["minutes"] = 89
    with pytest.raises(ValidationError, match="team-minute total"):
        retain_reviewed_participation(fixture, winner, review)
    evidence = retain_reviewed_participation(fixture, winner, reviewed_source())
    RawProviderPayload.objects.filter(pk=evidence.payload["source_payload"]).update(payload={"fixture": "changed"})
    with pytest.raises(ValidationError, match="checksum mismatch"):
        participation_evidence([fixture], winner)


def test_versioned_algeria_team_sheets_reconcile_to_six_complete_matches():
    _, edition, _, winner, opponent = setup_edition("6:2019")
    winner.provider_id = "1532"
    winner.save(update_fields=["provider_id"])
    config = json.loads((settings.BASE_DIR / "trophy_reviews" / "v1.json").read_text(encoding="utf8"))
    reviews = [item for item in config["participation"] if "reviewed_source" in item]
    assert {item["fixture"] for item in reviews} == {
        "117955", "117966", "117994", "118018", "118049", "118060"}
    for item in reviews:
        fixture = Fixture.objects.create(provider="api_football", provider_id=item["fixture"],
            competition_season=edition, home_team=winner, away_team=opponent,
            starts_at=datetime(2018, 1, 1, 19, tzinfo=timezone.utc), status="FINISHED",
            stage_name="Qualification", available_minutes=90)
        source = {"fixture": item["fixture"], "team": item["team"], "duration": 90,
            **item["reviewed_source"]}
        players = validate_reviewed_participation(fixture, winner, source)
        assert sum(row["minutes"] for row in players) == 990
        assert sum(row["started"] for row in players) == 11


@override_settings(API_FOOTBALL_KEY="test")
def test_pending_appeal_review_preserves_on_pitch_result_but_not_player_stats():
    _, edition, _, winner, _ = setup_edition("6:2025")
    edition.competition.provider_id = "6"
    edition.competition.save(update_fields=["provider_id"])
    row = source_row("1508003", 6, 2025, "WO", 120)
    source = raw_payload("trophy_fixture_source", "1508003", row)
    fixture_payload = {"edition": "6:2025", "provider_fixture_id": "1508003", "winner": "13",
        "on_pitch_status": "AET", "home_score": 1, "away_score": 0, "available_minutes": 120,
        "reviewer": "owner", "source_urls": ["https://www.cafonline.com/final"],
        "basis": "on_pitch_result_pending_appeal", "source_payload": source.pk,
        "source_sha256": source.payload_sha256}
    review = retain_fixture_review(edition, fixture_payload, source)
    provider = ApiFootballProvider()
    fixture = ingest_fixture_bundle(reviewed_fixture_bundle(edition, review, provider), edition,
        "api_football", "local:review")
    assert (fixture.status, fixture.home_score, fixture.away_score, fixture.available_minutes) == (
        "FINISHED", 1, 0, 120)
    assert fixture.stats_ingested_at is None and fixture.playerfixture_set.count() == 0
    outcome = {"edition": "6:2025", "winner": "13", "awarded_at": "2026-01-18T22:00:00+00:00",
        "expected_fixture_ids": ["1508003"], "reviewer": "owner",
        "source_urls": ["https://www.cafonline.com/final"], "basis": "on_pitch_result_pending_appeal"}
    record = retain_outcome_review(edition, outcome, [row])
    assert reviewed_outcome(edition, [row]).pk == record.pk


def test_reviewed_campaign_ids_include_administrative_results_but_not_cancelled_matches():
    rows = [source_row("1", status="WO"), source_row("2", status="AWD"), source_row("3", status="CANC")]
    assert winner_fixture_ids(rows, "13") == ["1", "2"]


def test_official_champion_review_requires_the_complete_winner_campaign():
    _, edition, _, _, _ = setup_edition("61:2025")
    rows = [source_row("1", league=61, year=2025), source_row("2", league=61, year=2025)]
    rows[1]["fixture"]["date"] = "2026-05-17T19:00:00+00:00"
    outcome = {"edition": "61:2025", "winner": "13",
        "awarded_at": "2026-05-17T22:00:00+00:00", "expected_fixture_ids": ["1", "2"],
        "reviewer": "reviewer", "source_urls": ["https://ligue1.com/official-champion"],
        "basis": "official_champion"}
    record = retain_outcome_review(edition, outcome, rows)
    assert record.payload["expected_fixture_ids"] == ["1", "2"]


def test_trophy_review_command_is_dry_run_by_default():
    output = StringIO()
    call_command("apply_trophy_reviews", stdout=output)
    assert "Would apply trophy review v1.0" in output.getvalue()
    assert RawProviderPayload.objects.count() == 0
