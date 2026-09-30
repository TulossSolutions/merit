from django.db import connection
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_page
from apps.football.models import Position, Season
from apps.rankings.services.queries import entries, latest_snapshot, present_movement
from apps.scoring.models import ScoringFormula

@cache_page(300)
def home(request):
    seasons = Season.objects.filter(is_published=True, rankingsnapshot__is_public=True).distinct().order_by("-starts_on")
    season_slug = request.GET.get("season")
    season = get_object_or_404(seasons, slug=season_slug) if season_slug else None
    snapshot=latest_snapshot(season); groups={p:list(entries(snapshot,p,5)) for p in (Position.FWD,Position.MID,Position.DEF,Position.GK)}
    present_movement([row for group in groups.values() for row in group],snapshot)
    return render(request,"core/home.html",{"snapshot":snapshot,"groups":groups,"seasons":seasons,"page_title":"Merit — No votes. Just performance."})
def health(request):
    with connection.cursor() as cursor: cursor.execute("SELECT 1"); cursor.fetchone()
    return JsonResponse({"status":"ok"})
def methodology(request): return render(request,"core/methodology.html",{"formula":ScoringFormula.objects.filter(is_active=True).first(),"page_title":"Methodology"})
def roadmap(request): return render(request,"core/roadmap.html",{"page_title":"Roadmap"})
def manifesto(request): return render(request,"core/manifesto.html",{"page_title":"Manifesto"})
def changelog(request): return render(request,"core/changelog.html",{"formulas":ScoringFormula.objects.order_by("-created_at"),"page_title":"Formula changelog"})
@cache_page(3600)
def season_archive(request,slug):
    from django.shortcuts import get_object_or_404
    season=get_object_or_404(Season,slug=slug); snapshot=latest_snapshot(season)
    return render(request,"core/season.html",{"season":season,"snapshot":snapshot,"page_title":f"{season.name} archive"})
def robots(request): return HttpResponse("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n",content_type="text/plain")
