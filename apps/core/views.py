from django.db import connection
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_page
from apps.football.models import Position, Season
from apps.rankings.services.queries import entries, latest_snapshot, present_movement, present_category_notices
from apps.scoring.models import ScoringFormula

@cache_page(300)
def home(request):
    seasons = Season.objects.filter(is_published=True, rankingsnapshot__is_public=True).distinct().order_by("-starts_on")
    season_slug = request.GET.get("season")
    season = get_object_or_404(seasons, slug=season_slug) if season_slug else None
    snapshot=latest_snapshot(season); groups={p:list(entries(snapshot,p,5)) for p in (Position.FWD,Position.MID,Position.DEF,Position.GK)}
    present_movement([row for group in groups.values() for row in group],snapshot)
    present_category_notices([row for group in groups.values() for row in group],snapshot)
    description=(f"Discover Merit's {snapshot.season.name} football leaders by position. "
        "Compare weekly rankings, player scores and season performance. No votes. Just performance.") if snapshot else None
    return render(request,"core/home.html",{"snapshot":snapshot,"groups":groups,"seasons":seasons,"page_title":"Merit — No votes. Just performance.","page_description":description})
def health(request):
    with connection.cursor() as cursor: cursor.execute("SELECT 1"); cursor.fetchone()
    return JsonResponse({"status":"ok"})
def methodology(request): return render(request,"core/methodology.html",{"formula":ScoringFormula.objects.filter(is_active=True).first(),"page_title":"Methodology","page_description":"Understand Merit's football ranking methodology: positional metrics, opponent strength, match context and trophy contribution, with transparent scoring rules."})
def roadmap(request): return render(request,"core/roadmap.html",{"page_title":"Roadmap","page_description":"Explore Merit's public roadmap: completed features and planned improvements to transparent football rankings, data coverage and player analysis."})
def manifesto(request): return render(request,"core/manifesto.html",{"page_title":"Manifesto","page_description":"Read the Merit manifesto: a transparent standard for football performance, where work matters and recognition is earned. No votes. Just performance."})
def changelog(request): return render(request,"core/changelog.html",{"formulas":ScoringFormula.objects.order_by("-created_at"),"page_title":"Formula changelog","page_description":"Review Merit's football scoring formula history, versioned rules and changes. Trace how the methodology evolves while published rankings remain auditable."})
@cache_page(3600)
def season_archive(request,slug):
    from django.shortcuts import get_object_or_404
    season=get_object_or_404(Season,slug=slug); snapshot=latest_snapshot(season)
    return render(request,"core/season.html",{"season":season,"snapshot":snapshot,"page_title":f"{season.name} archive","page_description":f"Explore Merit's {season.name} football season archive. Find published positional rankings and follow the transparent scoring methodology."})
def robots(request): return HttpResponse("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n",content_type="text/plain")
