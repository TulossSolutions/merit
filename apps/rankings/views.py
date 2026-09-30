from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404,render
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from apps.football.models import Player, Position, Season
from apps.rankings.models import RankingEntry
from apps.rankings.services.queries import entries,latest_snapshot
from apps.scoring.models import PlayerSeasonScore

SLUGS={"attackers":Position.FWD,"midfielders":Position.MID,"defenders":Position.DEF,"goalkeepers":Position.GK}
HEADLINES={Position.FWD:["goals_per90","assists_per90","shots_on_target_per90"],Position.MID:["key_passes_per90","interceptions_per90","pass_accuracy"],Position.DEF:["duel_win_rate","interceptions_per90","tackles_per90"],Position.GK:["save_percentage","saves_per90","clean_sheet_rate"]}
def ranking_index(request): return HttpResponseRedirect(reverse("ranking",args=["attackers"]))
@cache_page(300)
@vary_on_headers("HX-Request")
def ranking(request,position_slug):
    position=SLUGS.get(position_slug)
    if not position: return render(request,"404.html",status=404)
    season_slug=request.GET.get("season"); season=get_object_or_404(Season,slug=season_slug,is_published=True) if season_slug else None
    snapshot=latest_snapshot(season); page=Paginator(entries(snapshot,position)[:100],25).get_page(request.GET.get("page",1)) if snapshot else None
    headline_keys=HEADLINES[position]; headline_labels=[]
    if page:
        for key in headline_keys:
            metric=next((entry.metric_breakdown.get(key) for entry in page if entry.metric_breakdown.get(key)),None); headline_labels.append(metric.get("label",key) if metric else key.replace("_"," ").title())
        for entry in page: entry.headline_metrics=[entry.metric_breakdown.get(key) for key in headline_keys]
    context={"snapshot":snapshot,"position":position,"position_slug":position_slug,"page":page,"headline_labels":headline_labels,"seasons":Season.objects.filter(is_published=True).order_by("-starts_on"),"page_title":f"{dict(Position.choices)[position]} rankings"}
    template="rankings/partials/ranking_content.html" if request.headers.get("HX-Request")=="true" else "rankings/ranking_page.html"
    return render(request,template,context)
def player_detail(request,slug):
    player=get_object_or_404(Player,slug=slug); snapshot=latest_snapshot(); entry=RankingEntry.objects.filter(snapshot=snapshot,player=player).select_related("team").first() if snapshot else None
    score=PlayerSeasonScore.objects.filter(player=player).select_related("formula").order_by("-as_of").first()
    history_query=RankingEntry.objects.filter(player=player,snapshot__is_public=True).select_related("snapshot").order_by("snapshot__cutoff_at")
    if snapshot: history_query=history_query.filter(snapshot__season=snapshot.season)
    history=list(history_query)
    chart_points=[]; chart_dates=[]; chart_ranks=[]
    if history:
        low=min(item.rank for item in history); high=max(item.rank for item in history)
        first=history[0].snapshot.cutoff_at; last=history[-1].snapshot.cutoff_at; duration=(last-first).total_seconds()
        for item in history:
            x=516 if not duration else 72+(item.snapshot.cutoff_at-first).total_seconds()/duration*888
            y=150 if low==high else 40+(item.rank-low)/(high-low)*220
            chart_points.append({"x":round(x,1),"y":round(y,1),"item":item})
        chart_ranks=[{"value":rank,"y":round(150 if low==high else 40+(rank-low)/(high-low)*220,1)} for rank in dict.fromkeys((low,round((low+high)/2),high))]
        chart_dates=[chart_points[index] for index in dict.fromkeys((0,len(chart_points)//2,len(chart_points)-1))]
    required_minutes=score.season.eligibility_minutes(score.as_of.date()) if score else None
    achievements=entry.context_summary.get("achievements",{}) if entry else {}
    return render(request,"players/detail.html",{"player":player,"entry":entry,"score":score,"achievements":achievements,"chart_points":chart_points,"chart_dates":chart_dates,"chart_ranks":chart_ranks,"required_minutes":required_minutes,"page_title":player.name})
def compare(request):
    a=Player.objects.filter(slug=request.GET.get("a","")).first(); b=Player.objects.filter(slug=request.GET.get("b","")).first(); snapshot=latest_snapshot(); rows=[]
    for player in (a,b): rows.append(RankingEntry.objects.filter(snapshot=snapshot,player=player).select_related("player","team").first() if player and snapshot else None)
    template="players/partials/comparison.html" if request.headers.get("HX-Request")=="true" else "players/compare.html"
    return render(request,template,{"a":a,"b":b,"rows":rows,"players":Player.objects.filter(ranking_entries__snapshot=snapshot).distinct().order_by("name") if snapshot else Player.objects.none(),"different_positions":a and b and a.primary_position!=b.primary_position,"page_title":"Compare players"})
def player_search(request):
    q=request.GET.get("q","").strip(); qs=Player.objects.none()
    if len(q)>=2:
        qs=Player.objects.filter(Q(name__icontains=q)|Q(common_name__icontains=q)); position=request.GET.get("position")
        if position in Position.values: qs=qs.filter(primary_position=position)
    return render(request,"players/partials/search_results.html",{"players":qs.order_by("name")[:10]})
