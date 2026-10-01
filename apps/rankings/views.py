from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404,render
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from apps.football.models import Player, Position, Season
from apps.rankings.models import RankingEntry
from apps.rankings.services.queries import entries,latest_snapshot,present_movement
from apps.scoring.models import PlayerSeasonScore
from apps.ingestion.models import PlayerIdentityAlias

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
        page.object_list=present_movement(page.object_list,snapshot)
        for key in headline_keys:
            metric=next((entry.metric_breakdown.get(key) for entry in page if entry.metric_breakdown.get(key)),None); headline_labels.append(metric.get("label",key) if metric else key.replace("_"," ").title())
        for entry in page: entry.headline_metrics=[entry.metric_breakdown.get(key) for key in headline_keys]
    context={"snapshot":snapshot,"position":position,"position_slug":position_slug,"page":page,"headline_labels":headline_labels,"seasons":Season.objects.filter(is_published=True).order_by("-starts_on"),"page_title":f"{dict(Position.choices)[position]} rankings"}
    template="rankings/partials/ranking_content.html" if request.headers.get("HX-Request")=="true" else "rankings/ranking_page.html"
    return render(request,template,context)
def player_detail(request,slug):
    player=get_object_or_404(Player,slug=slug)
    alias=PlayerIdentityAlias.objects.filter(alias_player=player).select_related("canonical_player").first()
    if alias:
        target=reverse("player_detail",args=[alias.canonical_player.slug])
        if request.GET: target+="?"+request.GET.urlencode()
        return HttpResponsePermanentRedirect(target)
    public_entries=list(RankingEntry.objects.filter(player=player,snapshot__is_public=True,snapshot__season__is_published=True)
        .select_related("snapshot__season","snapshot__formula","team")
        .order_by("-snapshot__season__starts_on","-snapshot__cutoff_at","-snapshot__formula__is_active","-snapshot__published_at","-snapshot_id"))
    latest_by_season={}
    for item in public_entries:
        latest_by_season.setdefault(item.snapshot.season_id,item)
    season_history=list(latest_by_season.values())
    snapshot=latest_snapshot()
    season_slug=request.GET.get("season")
    if season_slug:
        entry=next((item for item in season_history if item.snapshot.season.slug==season_slug),None)
        if entry is None: raise Http404("No published ranking for this player in that season.")
    else:
        entry=next((item for item in public_entries if snapshot and item.snapshot_id==snapshot.pk),None)
        if entry is None and season_history: entry=season_history[0]
    if entry: snapshot=entry.snapshot
    if entry: present_movement([entry],snapshot)
    selected_season=snapshot.season if snapshot else None
    score=None
    if entry:
        appearances=PlayerSeasonScore.objects.filter(player=player,season=snapshot.season,formula=snapshot.formula,
            as_of=snapshot.cutoff_at,position=entry.position,eligible=True,minutes=entry.minutes,
            final_score=entry.score).values_list("appearances",flat=True).first()
        # Display the frozen publication, never a later or different-season calculation.
        score={"season":snapshot.season,"formula":snapshot.formula,"as_of":snapshot.cutoff_at,"eligible":True,
            "minutes":entry.minutes,"appearances":appearances,"metric_breakdown":entry.metric_breakdown,"context_summary":entry.context_summary}
    season_entries=[item for item in public_entries if entry and item.snapshot.season_id==entry.snapshot.season_id]
    weekly=[item for item in season_entries if item.snapshot.formula_id==entry.snapshot.formula_id
        and item.snapshot.coverage_summary.get("weekly_replay",{}).get("kind")=="weekly"] if entry else []
    if weekly: season_entries=weekly+[entry]
    by_cutoff={}
    for item in season_entries: by_cutoff.setdefault(item.snapshot.cutoff_at,item)
    history=[by_cutoff[key] for key in sorted(by_cutoff)]
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
    required_minutes=selected_season.eligibility_minutes(snapshot.cutoff_at.date()) if score else None
    achievements=entry.context_summary.get("achievements",{}) if entry else {}
    return render(request,"players/detail.html",{"player":player,"entry":entry,"score":score,"achievements":achievements,
        "season_history":season_history,"seasons":[item.snapshot.season for item in season_history],"selected_season":selected_season,
        "chart_points":chart_points,"chart_dates":chart_dates,"chart_ranks":chart_ranks,"required_minutes":required_minutes,"page_title":player.name})
def compare(request):
    a=Player.objects.exclude(provider_id="0").filter(slug=request.GET.get("a","")).first(); b=Player.objects.exclude(provider_id="0").filter(slug=request.GET.get("b","")).first(); snapshot=latest_snapshot(); rows=[]
    for player in (a,b): rows.append(RankingEntry.objects.filter(snapshot=snapshot,player=player).select_related("player","team").first() if player and snapshot else None)
    template="players/partials/comparison.html" if request.headers.get("HX-Request")=="true" else "players/compare.html"
    return render(request,template,{"snapshot":snapshot,"a":a,"b":b,"rows":rows,"players":Player.objects.exclude(provider_id="0").filter(ranking_entries__snapshot=snapshot).distinct().order_by("name") if snapshot else Player.objects.none(),"different_positions":a and b and a.primary_position!=b.primary_position,"page_title":"Compare players"})
def player_search(request):
    q=request.GET.get("q","").strip(); qs=Player.objects.none()
    if len(q)>=2:
        qs=Player.objects.exclude(provider_id="0").filter(Q(name__icontains=q)|Q(common_name__icontains=q),canonical_identity__isnull=True); position=request.GET.get("position")
        if position in Position.values: qs=qs.filter(primary_position=position)
    return render(request,"players/partials/search_results.html",{"players":qs.order_by("name")[:10]})
