import struct
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

import test_player_seasons
from apps.core.context_processors import DEFAULT_META_DESCRIPTION, metadata
from apps.football.models import Player

player_seasons = test_player_seasons.player_seasons
pytestmark = pytest.mark.django_db


def tags(response):
    assert response.status_code == 200
    head = BeautifulSoup(response.content, "html.parser").head
    assert head is not None
    result = {}
    for tag in head.select("meta[property^='og:']"):
        assert tag["property"] not in result, "Duplicate Open Graph field"
        result[tag["property"]] = tag["content"]
    description = head.select("meta[name='description']")
    canonical = head.select("link[rel='canonical']")
    assert len(description) == len(canonical) == 1
    assert description[0]["content"] == result["og:description"]
    assert canonical[0]["href"] == result["og:url"]
    assert head.title.string == result["og:title"]
    return result


@pytest.mark.parametrize("url", [
    "/", "/rankings/attackers/", "/rankings/midfielders/", "/rankings/defenders/",
    "/rankings/goalkeepers/", "/players/seasonal-player/", "/compare/",
    "/methodology/", "/roadmap/", "/manifesto/", "/methodology/changelog/", "/seasons/2024-25/",
])
def test_full_pages_have_complete_website_metadata(client, player_seasons, url):
    result = tags(client.get(url, secure=True))
    assert set(result) == {
        "og:title", "og:type", "og:url", "og:description", "og:site_name", "og:locale",
        "og:image", "og:image:url", "og:image:secure_url", "og:image:type",
        "og:image:width", "og:image:height", "og:image:alt",
    }
    assert all(result.values())
    assert result["og:type"] == "website"
    assert result["og:site_name"] == "Merit"
    assert result["og:locale"] == "en_US"
    assert result["og:url"] == f"https://testserver{url}"
    assert result["og:image"] == "https://testserver/static/images/merit_logo_full.png"
    assert result["og:image:url"] == result["og:image:secure_url"] == result["og:image"]
    assert result["og:image:type"] == "image/png"
    assert "Merit logo" in result["og:image:alt"]
    assert len(result["og:description"]) > 80
    image = (settings.BASE_DIR / "static/images/merit_logo_full.png").read_bytes()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", image[16:24])
    assert (int(result["og:image:width"]), int(result["og:image:height"])) == (width, height)


def test_production_prefix_and_proxy_scheme_are_preserved(client, player_seasons):
    with override_settings(STATIC_URL="/merit/static/", FORCE_SCRIPT_NAME="/merit",
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https")):
        cache.clear()
        response = client.get("/rankings/defenders/", {"season": "2024-25"},
            SCRIPT_NAME="/merit", HTTP_HOST="testserver", HTTP_X_FORWARDED_PROTO="https")
        result = tags(response)
        assert result["og:url"] == "https://testserver/merit/rankings/defenders/?season=2024-25"
        assert result["og:image"] == "https://testserver/merit/static/images/merit_logo_full.png"
        assert result["og:image:secure_url"] == result["og:image"]


def test_local_http_does_not_claim_an_https_image(client, player_seasons):
    result = tags(client.get("/players/seasonal-player/"))
    assert result["og:image"] == "http://testserver/static/images/merit_logo_full.png"
    assert "og:image:secure_url" not in result


def test_descriptions_follow_selected_season_and_players(client, player_seasons):
    home = tags(client.get("/", {"season": "2024-25"}))
    ranking = tags(client.get("/rankings/defenders/", {"season": "2024-25"}))
    player = tags(client.get("/players/seasonal-player/", {"season": "2024-25"}))
    current = tags(client.get("/players/seasonal-player/"))
    compared = tags(client.get("/compare/", {
        "season": "2024-25", "a": player_seasons["player"].slug, "b": player_seasons["retired"].slug,
    }))
    for result in (home, ranking, player, compared):
        assert "2024/25" in result["og:description"]
        assert "2026/27" not in result["og:description"]
        assert "season=2024-25" in result["og:url"]
    assert "defender" in ranking["og:description"]
    assert "Seasonal Player" in player["og:description"]
    assert "2026/27" in current["og:description"]
    assert "Seasonal Player" in compared["og:description"]
    assert "Historical Player" in compared["og:description"]
    assert "&a=" in compared["og:url"] and "&b=" in compared["og:url"]
    assert len({result["og:description"] for result in (home, ranking, player, current, compared)}) == 5


def test_static_page_descriptions_are_distinct(client):
    urls = ["/methodology/", "/roadmap/", "/manifesto/", "/methodology/changelog/"]
    descriptions = [tags(client.get(url))["og:description"] for url in urls]
    assert len(set(descriptions)) == len(urls)
    assert "trophy" in descriptions[0]
    assert "planned improvements" in descriptions[1]
    assert "manifesto" in descriptions[2]
    assert "versioned" in descriptions[3]


def test_empty_home_and_unranked_player_have_honest_fallbacks(client):
    cache.clear()
    assert tags(client.get("/"))["og:description"] == DEFAULT_META_DESCRIPTION
    player = Player.objects.create(provider="mock", provider_id="seo-unranked", name="Unranked Player")
    result = tags(client.get(reverse("player_detail", args=[player.slug])))
    assert "Unranked Player" in result["og:description"]
    assert "available season rankings" in result["og:description"]
    assert "positional rank," not in result["og:description"]


def test_player_metadata_is_attribute_escaped_and_read_only(client, player_seasons):
    player = player_seasons["player"]
    player.name = 'Player "><script>alert(1)</script> & Friends'
    player.save(update_fields=["name"])
    with patch("apps.ingestion.providers.api_football.ApiFootballProvider._request",
            side_effect=AssertionError("No API calls")), CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("player_detail", args=[player.slug]))
    result = tags(response)
    assert result["og:title"] == player.name
    assert player.name in result["og:description"]
    head = BeautifulSoup(response.content, "html.parser").head
    assert not head.find("script", string="alert(1)")
    assert b"&quot;&gt;&lt;script&gt;" in response.content
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)


def test_metadata_context_processor_does_not_query_database():
    with CaptureQueriesContext(connection) as queries:
        result = metadata(RequestFactory().get("/players/example/", secure=True))
    assert not queries
    assert result["social_image"]["url"] == "https://testserver/static/images/merit_logo_full.png"


@pytest.mark.parametrize("url", ["/rankings/attackers/", "/compare/"])
def test_htmx_fragments_remain_headless(client, player_seasons, url):
    response = client.get(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b'property="og:' not in response.content
    assert b"<head>" not in response.content
