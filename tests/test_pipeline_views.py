import json
from datetime import date, datetime, timedelta, timezone
import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from apps.football.models import Player, Position, Season
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
    for score in scores:
        detail=score.context_summary["competition_minutes"]
        assert sum(item["minutes"] for item in detail)==score.minutes
        entry=snapshot.entries.filter(player=score.player,position=score.position).first()
        if entry: assert entry.context_summary["competition_minutes"]==detail
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

def test_provider_id_zero_players_are_excluded_from_public_lists(pipeline):
    snapshot,_=pipeline
    hidden=Player.objects.create(provider="api_football",provider_id="0",name="Unverified Placeholder",primary_position=Position.FWD)
    RankingEntry.objects.create(snapshot=snapshot,player=hidden,position=Position.FWD,rank=999,score=1,minutes=90)
    cache.clear()
    client=Client()
    for url in ("/", "/rankings/attackers/", "/compare/", "/players/search/?q=Unverified", "/sitemap.xml"):
        response=client.get(url)
        assert response.status_code==200
        assert b"Unverified Placeholder" not in response.content

def test_compare_season_selector_uses_that_seasons_snapshot_and_player_pool(pipeline):
    current,_=pipeline
    selected=list(current.entries.select_related("player","team").order_by("rank")[:2])
    current_only=current.entries.select_related("player").order_by("rank")[2]
    historical=Season.objects.create(name="2024/25",slug="2024-25",starts_on=date(2024,8,1),
        ends_on=date(2025,5,31),is_published=True)
    cutoff=datetime(2025,7,1,tzinfo=timezone.utc)
    old=RankingSnapshot.objects.create(season=historical,formula=current.formula,cutoff_at=cutoff,
        published_at=cutoff,is_public=True)
    for rank,item in enumerate(selected,1):
        RankingEntry.objects.create(snapshot=old,player=item.player,team=item.team,position=item.position,
            rank=rank,score=item.score,minutes=item.minutes,metric_breakdown=item.metric_breakdown,
            context_summary=item.context_summary)
    params=f"?season=2024-25&a={selected[0].player.slug}&b={selected[1].player.slug}"
    response=Client().get(f"/compare/{params}")
    assert response.status_code==200 and response.context["snapshot"]==old
    assert b'<option value="2024-25" selected>' in response.content
    assert response.content.count(b" selected")>=3
    assert current_only.player.name.encode() not in response.content
    fragment=Client().get(f"/compare/{params}",HTTP_HX_REQUEST="true")
    assert fragment.status_code==200 and b'id="compare-workspace"' in fragment.content
    assert b"<html" not in fragment.content and fragment.context["snapshot"]==old

def test_compare_highlights_higher_frozen_percentiles_and_score_with_ties(pipeline):
    snapshot,_=pipeline
    rows=list(snapshot.entries.select_related("player").order_by("rank")[:2])
    breakdowns=[
        {"attack":{"label":"Attack","raw_value":2,"percentile":90,"effective_weight":0.5},
         "discipline":{"label":"Discipline","raw_value":1,"percentile":60,"effective_weight":0.3},
         "tie":{"label":"Tie","raw_value":5,"percentile":50,"effective_weight":0.2}},
        {"attack":{"label":"Attack","raw_value":3,"percentile":80,"effective_weight":0.5},
         "discipline":{"label":"Discipline","raw_value":0,"percentile":70,"effective_weight":0.3},
         "tie":{"label":"Tie","raw_value":4,"percentile":50,"effective_weight":0.2}},
    ]
    for index,row in enumerate(rows):
        row.score=80-index*10; row.metric_breakdown=breakdowns[index]
        row.save(update_fields=["score","metric_breakdown"])
    params=f"?season={snapshot.season.slug}&a={rows[0].player.slug}&b={rows[1].player.slug}"
    response=Client().get(f"/compare/{params}")
    compared=response.context["rows"]
    assert compared[0].compare_best_score and not compared[1].compare_best_score
    assert compared[0].compare_best_metric_keys=={"attack","tie"}
    assert compared[1].compare_best_metric_keys=={"discipline","tie"}
    assert response.content.count(b'data-best-value="true"')==5
    one=Client().get(f"/compare/?season={snapshot.season.slug}&a={rows[0].player.slug}")
    assert b'data-best-value="true"' not in one.content

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
