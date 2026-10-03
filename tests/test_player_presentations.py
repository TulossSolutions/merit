from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import Client

from apps.football.models import Player, Position, Season
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.scoring.models import ScoringFormula
from apps.scoring.services.minutes import competition_minutes, displayed_minutes


def test_exact_competition_minutes_grouping_and_duplicate_appearances():
    competitions = [
        SimpleNamespace(pk=1, name="Premier League", participant_type="CLUB"),
        SimpleNamespace(pk=2, name="Champions League", participant_type="CLUB"),
        SimpleNamespace(pk=3, name="UEFA Nations League", participant_type="NATIONAL")]
    rows = [SimpleNamespace(minutes=minutes, fixture=SimpleNamespace(
        competition_season=SimpleNamespace(competition=competitions[index])))
        for index, minutes in ((0, 180), (0, 123), (1, 90), (2, 91), (2, 0))]
    detail = competition_minutes(rows)
    assert {row["competition"]: row["minutes"] for row in detail} == {
        "Premier League": 303, "Champions League": 90, "UEFA Nations League": 91}
    groups = displayed_minutes({"competition_minutes": detail}, 484)
    assert [group["label"] for group in groups] == ["Club", "National team"]
    assert sum(row["minutes"] for group in groups for row in group["rows"]) == 484


def test_legacy_minutes_remainder_is_honest_and_totals_reconcile():
    groups = displayed_minutes({"domestic_minutes": 303, "ucl_minutes": 90}, 484)
    assert groups[-1]["rows"] == [{"competition": "Other covered competitions", "minutes": 91}]
    assert displayed_minutes({}, 484)[0]["rows"][0]["minutes"] == 484
    assert sum(row["minutes"] for group in displayed_minutes({"domestic_minutes": 900}, 484)
        for row in group["rows"]) == 484


@pytest.fixture
def changed_player(db):
    cache.clear()
    season = Season.objects.create(name="2026/27", slug="2026-27", starts_on=date(2026, 8, 1),
        ends_on=date(2027, 7, 31), is_current=True, is_published=True)
    formula = ScoringFormula.objects.create(version="presentation", config={}, checksum_sha256="test")
    cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
    previous = RankingSnapshot.objects.create(season=season, formula=formula, cutoff_at=cutoff-timedelta(days=7),
        published_at=cutoff-timedelta(days=7), is_public=True)
    current = RankingSnapshot.objects.create(season=season, formula=formula, cutoff_at=cutoff,
        published_at=cutoff, is_public=True)
    player = Player.objects.create(provider="mock", provider_id="any-player", name="Changed Player", primary_position=Position.FWD)
    RankingEntry.objects.create(snapshot=previous, player=player, position=Position.MID, rank=1, score=80, minutes=300)
    entry = RankingEntry.objects.create(snapshot=current, player=player, position=Position.FWD, rank=1, score=80, minutes=484,
        context_summary={"domestic_minutes": 303, "ucl_minutes": 90})
    return player, entry


def test_category_icon_is_by_name_only_on_home_and_preserves_detail_label(changed_player):
    player, entry = changed_player
    client = Client()
    home = client.get("/")
    assert home.status_code == 200
    assert b"NEW category" not in home.content
    assert b"Changed Player</a>\n" in home.content
    assert b'class="category-info"' in home.content
    assert b'href="/methodology/#award-position"' in home.content
    assert b"Award category changed." in home.content
    detail = client.get(f"/players/{player.slug}/")
    assert b"NEW category" in detail.content
    assert b'aria-describedby="category-change-' in detail.content
    assert b'role="tooltip"' in detail.content
    assert b'id="award-position"' in client.get("/methodology/").content
    entry.refresh_from_db()
    assert entry.previous_rank is None and entry.movement is None


def test_minutes_view_uses_only_frozen_details_and_legacy_remainder(changed_player):
    player, entry = changed_player
    client = Client()
    response = client.get(f"/players/{player.slug}/")
    assert b"Other covered competitions" in response.content
    assert b">91</td>" in response.content
    entry.context_summary["competition_minutes"] = [
        {"competition_id": 1, "competition": "Premier League", "participant_type": "CLUB", "minutes": 303},
        {"competition_id": 2, "competition": "Champions League", "participant_type": "CLUB", "minutes": 90},
        {"competition_id": 3, "competition": "UEFA Nations League", "participant_type": "NATIONAL", "minutes": 91}]
    entry.save()
    response = client.get(f"/players/{player.slug}/")
    assert b"UEFA Nations League" in response.content and b"National team" in response.content
    assert b"Other covered competitions" not in response.content
    assert b"Total minutes</th><td>484</td>" in response.content
