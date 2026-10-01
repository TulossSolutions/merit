from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.football.models import Player, Position, Season
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.rankings.services.queries import latest_snapshot
from apps.scoring.models import PlayerSeasonScore, ScoringFormula


def make_snapshot(season, formula, cutoff, *, public=True, published=None):
    return RankingSnapshot.objects.create(
        season=season, formula=formula, cutoff_at=cutoff, published_at=published or cutoff, is_public=public,
    )


def make_entry(snapshot, player, *, rank=1, score=80, achievements=None, position=Position.FWD):
    context = {"average_opponent_elo": 1515.5, "average_context_factor": 1.1, "domestic_minutes": 1800, "ucl_minutes": 200}
    if achievements is not None:
        context["achievements"] = achievements
    return RankingEntry.objects.create(
        snapshot=snapshot, player=player, position=position, rank=rank, score=score, minutes=2000,
        metric_breakdown={"test_metric": {"label": "Frozen season metric", "raw_value": 1.25, "percentile": 75,
            "base_weight": 1, "effective_weight": 1, "active": True}}, context_summary=context,
    )


@pytest.fixture
def player_seasons(db):
    cache.clear()
    old = Season.objects.create(name="2024/25", slug="2024-25", starts_on=date(2024, 8, 1), ends_on=date(2025, 5, 31), is_published=True)
    current = Season.objects.create(name="2026/27", slug="2026-27", starts_on=date(2026, 8, 1), ends_on=date(2027, 5, 31), is_published=True, is_current=True)
    private = Season.objects.create(name="2025/26", slug="2025-26", starts_on=date(2025, 8, 1), ends_on=date(2026, 5, 31), is_published=True)
    unpublished = Season.objects.create(name="2023/24", slug="2023-24", starts_on=date(2023, 8, 1), ends_on=date(2024, 5, 31))
    formula = ScoringFormula.objects.create(version="player-test", name="Player test", config={}, checksum_sha256="test")
    older_formula = ScoringFormula.objects.create(version="older-test", name="Older formula", config={}, checksum_sha256="older")
    player = Player.objects.create(provider="mock", provider_id="seasonal", name="Seasonal Player", primary_position=Position.FWD)
    retired = Player.objects.create(provider="mock", provider_id="retired", name="Historical Player", primary_position=Position.DEF)
    old_cutoff = datetime(2025, 7, 6, tzinfo=timezone.utc)
    current_cutoff = datetime(2026, 9, 29, tzinfo=timezone.utc)
    early_snapshot = make_snapshot(old, formula, old_cutoff - timedelta(days=7))
    previous = make_entry(early_snapshot, player, rank=4, position=Position.DEF)
    same_cutoff = make_snapshot(old, older_formula, old_cutoff)
    make_entry(same_cutoff, player, rank=3, position=Position.DEF)
    old_snapshot = make_snapshot(old, formula, old_cutoff, published=old_cutoff + timedelta(days=1))
    achievements = {"enabled": True, "score": "36.375", "points": "3.6375", "points_cap": "10", "weight": "0.05",
        "campaigns": [{"competition": "Champions League", "team": "Past Champions", "contribution_percent": "72.75",
            "title_points": "5", "earned_points": "3.6375", "policy_version": "test-policy"}]}
    old_entry = make_entry(old_snapshot, player, rank=2, score=81.5, achievements=achievements, position=Position.DEF)
    retired_entry = make_entry(old_snapshot, retired, rank=7, achievements=achievements, position=Position.DEF)
    current_snapshot = make_snapshot(current, formula, current_cutoff)
    current_entry = make_entry(current_snapshot, player, rank=9, score=70, achievements={"enabled": True, "score": "0", "campaigns": []})
    private_snapshot = make_snapshot(private, formula, datetime(2026, 7, 1, tzinfo=timezone.utc), public=False)
    make_entry(private_snapshot, player, rank=99)
    unpublished_snapshot = make_snapshot(unpublished, formula, datetime(2024, 7, 1, tzinfo=timezone.utc))
    make_entry(unpublished_snapshot, player, rank=98)
    # These later/private calculations must never replace the frozen public breakdown.
    make_snapshot(current, formula, current_cutoff + timedelta(days=1), public=False)
    for season, cutoff, eligible in ((old, old_cutoff, True), (current, current_cutoff + timedelta(days=1), False)):
        PlayerSeasonScore.objects.create(
            season=season, player=player, formula=formula, position=Position.FWD, as_of=cutoff, eligible=eligible,
            minutes=9876, appearances=99, final_score=99, metric_breakdown={"secret": {"label": "Unpublished calculation"}},
            context_summary={"average_opponent_elo": 9999, "achievements": {"enabled": True, "campaigns": []}},
        )
    yield dict(player=player, retired=retired, old=old, current=current, old_entry=old_entry,
        current_entry=current_entry, retired_entry=retired_entry, previous=previous, formula=formula)
    cache.clear()


def test_player_defaults_to_current_public_ranking(client, player_seasons):
    response = client.get(reverse("player_detail", args=[player_seasons["player"].slug]))
    assert response.status_code == 200
    assert response.context["entry"] == player_seasons["current_entry"]
    assert [season.slug for season in response.context["seasons"]] == ["2026-27", "2024-25"]
    html = response.content.decode()
    assert '<option value="2026-27" selected>2026/27</option>' in html
    assert "No verified titles earned by this player at this cutoff." in html
    assert "Past Champions" not in html
    assert "Unpublished calculation" not in html
    assert "9876 minutes" not in html


def test_selected_season_shows_frozen_contributions_breakdown_and_history(client, player_seasons):
    response = client.get(reverse("player_detail", args=[player_seasons["player"].slug]), {"season": "2024-25"})
    assert response.status_code == 200
    assert response.context["entry"] == player_seasons["old_entry"]
    assert response.context["score"]["metric_breakdown"] == player_seasons["old_entry"].metric_breakdown
    assert response.context["score"]["appearances"] is None
    assert all(point["item"].snapshot.season_id == player_seasons["old"].pk for point in response.context["chart_points"])
    assert len(response.context["chart_points"]) == 2
    assert [point["item"].rank for point in response.context["chart_points"]] == [4, 2]
    html = response.content.decode()
    assert '<option value="2024-25" selected>2024/25</option>' in html
    assert "Title campaign contribution" in html and "72.75%" in html and "Past Champions" in html
    assert "Frozen season metric" in html and "1515.5" in html
    assert "Unpublished calculation" not in html
    assert "Defender" in html
    assert "Season history" in html and "Season ranking history" in html


def test_season_history_uses_latest_public_snapshot_per_season(client, player_seasons):
    response = client.get(reverse("player_detail", args=[player_seasons["player"].slug]))
    assert response.context["season_history"] == [player_seasons["current_entry"], player_seasons["old_entry"]]
    assert len(response.context["chart_points"]) == 1
    html = response.content.decode()
    assert '?season=2024-25"' in html and '?season=2026-27"' in html
    assert 'value="2025-26"' not in html and 'value="2023-24"' not in html


def test_active_formula_wins_same_cutoff_even_when_legacy_replay_published_later(client, player_seasons):
    formula = player_seasons["formula"]
    formula.is_active = True
    formula.save(update_fields=["is_active"])
    current_entry = player_seasons["current_entry"]
    legacy_formula = ScoringFormula.objects.exclude(pk=formula.pk).first()
    legacy = make_snapshot(
        player_seasons["current"], legacy_formula, current_entry.snapshot.cutoff_at,
        published=current_entry.snapshot.published_at + timedelta(days=1),
    )
    make_entry(legacy, current_entry.player, rank=1, score=99, position=Position.MID)

    assert latest_snapshot(player_seasons["current"]) == current_entry.snapshot
    response = client.get(reverse("player_detail", args=[current_entry.player.slug]), {"season": "2026-27"})
    assert response.context["entry"] == current_entry
    assert response.context["entry"].position == Position.FWD
    assert b"Attacker" in response.content


def test_historical_only_player_defaults_to_latest_published_season(client, player_seasons):
    response = client.get(reverse("player_detail", args=[player_seasons["retired"].slug]))
    assert response.status_code == 200
    assert response.context["entry"] == player_seasons["retired_entry"]
    assert response.context["selected_season"] == player_seasons["old"]
    assert '<option value="2024-25" selected>' in response.content.decode()
    assert b"72.75%" in response.content


@pytest.mark.parametrize("slug", ["unknown", "2025-26", "2023-24"])
def test_player_rejects_unpublished_or_unknown_seasons(client, player_seasons, slug):
    assert client.get(reverse("player_detail", args=[player_seasons["player"].slug]), {"season": slug}).status_code == 404


def test_player_cannot_select_season_without_their_public_ranking(client, player_seasons):
    assert client.get(reverse("player_detail", args=[player_seasons["retired"].slug]), {"season": "2026-27"}).status_code == 404


def test_exact_published_calculation_supplies_appearances_only(client, player_seasons):
    entry = player_seasons["current_entry"]
    PlayerSeasonScore.objects.create(
        player=entry.player, season=entry.snapshot.season, formula=entry.snapshot.formula, position=entry.position,
        as_of=entry.snapshot.cutoff_at, eligible=True, minutes=entry.minutes, appearances=12, final_score=entry.score,
        metric_breakdown={"different": {"label": "Do not show mutable metrics"}},
    )
    response = client.get(reverse("player_detail", args=[entry.player.slug]))
    assert response.context["score"]["appearances"] == 12
    assert b"12 appearances" in response.content
    assert b"Do not show mutable metrics" not in response.content


def test_disabled_trophies_and_missing_calculations_remain_supported(client, player_seasons):
    entry = player_seasons["previous"]
    solo = Player.objects.create(provider="mock", provider_id="legacy", name="Legacy Player")
    make_entry(entry.snapshot, solo, rank=10)
    response = client.get(reverse("player_detail", args=[solo.slug]))
    assert response.status_code == 200
    assert b"Title campaign contribution" not in response.content
    assert b"Frozen season metric" in response.content
    assert response.context["score"]["appearances"] is None


def test_player_without_public_rankings_keeps_empty_state(client, player_seasons):
    player = Player.objects.create(provider="mock", provider_id="new", name="Unranked Player")
    response = client.get(reverse("player_detail", args=[player.slug]))
    assert response.status_code == 200
    assert b"No published season rankings yet." in response.content
    assert b"No published history yet." in response.content
    assert b'id="player-season"' not in response.content


def test_home_and_ranking_player_links_preserve_selected_season(client, player_seasons):
    detail = reverse("player_detail", args=[player_seasons["player"].slug])
    for url in (reverse("home"), reverse("ranking", args=["defenders"])):
        response = client.get(url, {"season": "2024-25"})
        assert response.status_code == 200
        assert f'href="{detail}?season=2024-25"'.encode() in response.content


def test_player_display_does_not_recalculate_fetch_or_write(client, player_seasons):
    entry = player_seasons["old_entry"]
    original = (entry.score, entry.metric_breakdown, entry.context_summary)
    with patch("apps.ingestion.providers.api_football.ApiFootballProvider._request", side_effect=AssertionError("No API calls")), \
            patch("apps.scoring.services.calculate.recompute_scores", side_effect=AssertionError("No recalculation")), \
            patch("apps.rankings.services.publish.publish", side_effect=AssertionError("No publication")), \
            CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("player_detail", args=[entry.player.slug]), {"season": "2024-25"})
    assert response.status_code == 200
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)
    entry.refresh_from_db()
    assert (entry.score, entry.metric_breakdown, entry.context_summary) == original
