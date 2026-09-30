import json
from datetime import datetime, timedelta, timezone
import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from apps.football.models import Position, Season
from apps.scoring.models import PlayerSeasonScore, ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo
from apps.rankings.services.publish import publish
from apps.scoring.services.formulas import validate_formula
from apps.rankings.models import RankingEntry, RankingSnapshot

pytestmark = pytest.mark.django_db

def test_methodology_links_to_github():
    response = Client().get("/methodology/")
    assert response.status_code == 200
    assert b'href="https://github.com/TulossSolutions/performanceawards"' in response.content
    assert b"View source on GitHub" in response.content

@pytest.fixture
def pipeline():
    call_command("seed_demo_data", verbosity=0)
    with open("scoring_formulas/v1_2.json", encoding="utf8") as handle: config = json.load(handle)
    formula = ScoringFormula.objects.create(version="1.2", name="V1.2", config=config, checksum_sha256=validate_formula(config), is_active=True)
    season = Season.objects.get(is_current=True)
    cutoff = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
    assert rebuild_elo(season) == 24
    scores = recompute_scores(season, formula, cutoff)
    return publish(season, formula, cutoff), scores

def test_full_pipeline_has_four_cohorts_and_breakdown(pipeline):
    snapshot, scores = pipeline
    snapshot.season.refresh_from_db()
    assert snapshot.season.is_published is True
    assert set(snapshot.entries.values_list("position", flat=True)) == {Position.GK, Position.DEF, Position.MID, Position.FWD}
    assert all(score.metric_breakdown for score in scores)
    defender = snapshot.entries.filter(position=Position.DEF).first()
    assert defender.metric_breakdown["tackles_per90"]["active"] is True
    assert defender.metric_breakdown["tackles_per90"]["raw_value"] is not None

def test_public_pages_and_htmx(pipeline):
    client = Client()
    urls = ["/", "/rankings/attackers/", "/rankings/midfielders/", "/rankings/defenders/", "/rankings/goalkeepers/", "/players/demo-player-1/", "/compare/?a=demo-player-1&b=demo-player-2", "/methodology/", "/roadmap/", "/manifesto/", "/methodology/changelog/", "/seasons/2026-27/", "/healthz/", "/sitemap.xml"]
    assert all(client.get(url).status_code == 200 for url in urls)
    fragment = client.get("/rankings/attackers/", HTTP_HX_REQUEST="true")
    assert b"<html" not in fragment.content
    assert b'hx-push-url="true"' in fragment.content
    defenders = client.get("/rankings/defenders/")
    assert b"Tackles / 90" in defenders.content
    assert b"Tackles won / 90" not in defenders.content

    manifesto = client.get("/manifesto/")
    assert b"Most football arguments start the same way." in manifesto.content
    assert b"truth outruns narrative" in manifesto.content
    assert b"<strong>Merit</strong>" in manifesto.content
    assert b"Merit: <em>No votes. Just performance.</em>" in manifesto.content
    manifesto_body = manifesto.content.split(b'<article class="prose">', 1)[1].split(b"</article>", 1)[0]
    assert manifesto_body.count(b"<p>") == 14
    roadmap = client.get("/roadmap/")
    assert roadmap.content.count(b"list-disc") == 4
    player = client.get("/players/demo-player-1/")
    assert b"Season ranking position over time" in player.content
    assert b"<polyline" in player.content

def test_home_shows_ranking_movement_next_to_score(pipeline):
    snapshot,_=pipeline
    entry=snapshot.entries.filter(position=Position.FWD).first()
    previous=RankingSnapshot.objects.create(season=snapshot.season,formula=snapshot.formula,published_at=snapshot.cutoff_at-timedelta(days=7),
        cutoff_at=snapshot.cutoff_at-timedelta(days=7),is_public=True)
    RankingEntry.objects.create(snapshot=previous,player=entry.player,team=entry.team,position=entry.position,rank=entry.rank+2,
        score=entry.score,minutes=entry.minutes)
    cache.clear()
    content=Client().get("/").content
    assert b'images/merit_logo_full.png' in content
    assert b'images/merit_logo_favicon.png' in content
    assert b'dist/app.css?v=merit-20260923' in content
    nav = content.split(b'<nav aria-label="Main navigation">', 1)[1].split(b'</nav>', 1)[0]
    assert all(label not in nav for label in (b"Attackers", b"Midfielders", b"Defenders", b"Goalkeepers"))
    assert all(label in nav for label in (b"Compare", b"Methodology", b"Roadmap", b"Manifesto"))
    assert b"No votes. Just performance." in content
    assert b'<meta name="description" content="No votes. Just performance.">' in content
    assert "Merit — No votes. Just performance.".encode() in content
    assert b'aria-label="Up 2 places"' in content

def test_position_tabs_highlight_current_filter(pipeline):
    client=Client()
    for slug in ("attackers","midfielders","defenders","goalkeepers"):
        response=client.get(f"/rankings/{slug}/",HTTP_HX_REQUEST="true")
        assert f"hx-get=\"/rankings/{slug}/?season=2026-27\"".encode() in response.content
        assert response.content.count(b'aria-current="page"')==1

def test_inactive_existing_goal_rate_is_displayed_but_zero_is_dash(pipeline):
    snapshot,_=pipeline
    entry=snapshot.entries.filter(position=Position.FWD).first()
    breakdown=entry.metric_breakdown
    breakdown["goals_per90"].update(active=False,raw_value=1.25,percentile=None,effective_weight=0)
    entry.metric_breakdown=breakdown
    entry.save(update_fields=["metric_breakdown"])
    score=PlayerSeasonScore.objects.get(season=snapshot.season,player=entry.player,as_of=snapshot.cutoff_at)
    score.metric_breakdown=breakdown
    score.save(update_fields=["metric_breakdown"])
    cache.clear()
    assert b"1.25" in Client().get("/rankings/attackers/").content
    assert b"1.25" in Client().get(f"/players/{entry.player.slug}/").content
    breakdown["goals_per90"]["raw_value"]=0
    entry.metric_breakdown=breakdown
    entry.save(update_fields=["metric_breakdown"])
    cache.clear()
    assert b"1.25" not in Client().get("/rankings/attackers/").content

def test_player_history_chart_spans_more_than_twelve_snapshots(pipeline):
    snapshot,_=pipeline
    first=snapshot.entries.filter(position=Position.FWD).first()
    for day in range(1,14):
        cutoff=snapshot.cutoff_at+timedelta(days=day)
        later=RankingSnapshot.objects.create(season=snapshot.season,formula=snapshot.formula,published_at=cutoff,cutoff_at=cutoff,is_public=True)
        RankingEntry.objects.create(snapshot=later,player=first.player,team=first.team,position=first.position,rank=first.rank,score=first.score,previous_rank=first.rank,movement=0,minutes=first.minutes)
    response=Client().get(f"/players/{first.player.slug}/")
    assert response.status_code==200
    assert b"Season ranking history" in response.content
    assert b"Average opponent Elo</abbr>" in response.content
    assert b"Effective weight</abbr>" in response.content
    assert b'viewBox="0 0 1000 320"' in response.content
    assert response.content.count(b"<circle ")==14
    assert b"No published history yet." not in response.content

def test_publish_is_immutable_without_force(pipeline):
    snapshot, _ = pipeline
    with pytest.raises(ValueError): publish(snapshot.season, snapshot.formula, snapshot.cutoff_at)
