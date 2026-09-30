from datetime import date, datetime, timezone

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.football.models import Player, Position, Season
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.scoring.models import ScoringFormula


@pytest.fixture
def published_seasons(db):
    cache.clear()
    formula = ScoringFormula.objects.create(version="home-test", name="Home test", config={}, checksum_sha256="test")
    seasons = {}
    for year, label, public, published in (
        (2024, "Older", True, True),
        (2026, "Current", True, True),
        (2025, "Private", False, True),
        (2023, "Unpublished", True, False),
    ):
        season = Season.objects.create(
            name=f"{year}/{str(year + 1)[-2:]}", slug=f"{year}-{str(year + 1)[-2:]}",
            starts_on=date(year, 7, 1), ends_on=date(year + 1, 6, 30),
            is_current=year == 2026, is_published=published,
        )
        seasons[label] = season
        cutoff = datetime(year, 9, 1, tzinfo=timezone.utc)
        snapshot = RankingSnapshot.objects.create(
            season=season, formula=formula, cutoff_at=cutoff, published_at=cutoff, is_public=public,
        )
        for position in (Position.FWD, Position.MID, Position.DEF, Position.GK):
            player = Player.objects.create(
                provider="mock", provider_id=f"{year}-{position}", name=f"{label} {position}", primary_position=position,
            )
            RankingEntry.objects.create(snapshot=snapshot, player=player, position=position, rank=1, score=80, minutes=900)
    yield seasons
    cache.clear()


def test_home_defaults_to_current_public_season(client, published_seasons):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert response.context["snapshot"].season == published_seasons["Current"]
    html = response.content.decode()
    assert '<option value="2026-27" selected>2026/27</option>' in html
    assert [season.slug for season in response.context["seasons"]] == ["2026-27", "2024-25"]
    for position in (Position.FWD, Position.MID, Position.DEF, Position.GK):
        assert f"Current {position}" in html
        assert f"Older {position}" not in html


def test_home_switches_all_roles_and_retains_season_in_ranking_links(client, published_seasons):
    response = client.get(reverse("home"), {"season": "2024-25"})
    assert response.status_code == 200
    assert response.context["snapshot"].season == published_seasons["Older"]
    html = response.content.decode()
    assert '<option value="2024-25" selected>2024/25</option>' in html
    for position in (Position.FWD, Position.MID, Position.DEF, Position.GK):
        assert f"Older {position}" in html
        assert f"Current {position}" not in html
    for position in ("attackers", "midfielders", "defenders", "goalkeepers"):
        assert f'href="{reverse("ranking", args=[position])}?season=2024-25"' in html
    published_seasons["Current"].refresh_from_db()
    assert published_seasons["Current"].is_current


@pytest.mark.parametrize("slug", ["unknown", "2025-26", "2023-24"])
def test_home_rejects_unknown_or_unpublished_seasons(client, published_seasons, slug):
    assert client.get(reverse("home"), {"season": slug}).status_code == 404


@pytest.mark.parametrize("queries", [(None, "2024-25"), ("2024-25", None)])
def test_home_cache_keeps_season_queries_separate(client, published_seasons, queries):
    for slug in queries * 2:
        response = client.get(reverse("home"), {"season": slug} if slug else {})
        html = response.content.decode()
        label, other = ("Older", "Current") if slug else ("Current", "Older")
        assert f"{label} FWD" in html
        assert f"{other} FWD" not in html


@pytest.mark.django_db
def test_home_without_rankings_preserves_empty_state(client):
    cache.clear()
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert "Current Merit Rankings" in response.content.decode()
    assert 'id="home-season"' not in response.content.decode()
    cache.clear()


def test_home_preserves_umami_script_and_ranking_navigation(client, published_seasons):
    html = client.get(reverse("home")).content.decode()
    assert html.count('src="https://analytics.tuloss.com/script.js"') == 1
    assert '<script defer src="https://analytics.tuloss.com/script.js" data-website-id="d97186f8-a328-4f43-8e48-25aae11006df">' in html
    assert f'href="{reverse("ranking_index")}">Ranking</a>' in html
