from apps.football.models import Season
from apps.rankings.models import RankingSnapshot

def latest_snapshot(season=None):
    season=season or Season.objects.filter(is_current=True,is_published=True).first()
    return RankingSnapshot.objects.filter(season=season,is_public=True).select_related("season","formula").order_by("-cutoff_at","-formula__is_active","-published_at","-pk").first() if season else None

def entries(snapshot,position,limit=None):
    qs=snapshot.entries.filter(position=position).exclude(player__provider_id="0").select_related("player","team").order_by("rank") if snapshot else []
    return qs[:limit] if limit else qs

def previous_snapshot(snapshot):
    if snapshot is None: return None
    candidates=RankingSnapshot.objects.filter(season=snapshot.season,is_public=True,cutoff_at__lt=snapshot.cutoff_at)
    weekly=candidates.filter(formula=snapshot.formula,coverage_summary__weekly_replay__kind="weekly")
    return weekly.order_by("-cutoff_at","-published_at","-pk").first() or candidates.order_by("-cutoff_at","-published_at","-pk").first()

def present_movement(rows,snapshot):
    """Derive display-only movement from persisted ranks; never edit frozen entries."""
    rows=list(rows)
    previous=previous_snapshot(snapshot)
    prior={item["player_id"]:item for item in previous.entries.filter(player_id__in=[row.player_id for row in rows])
        .values("player_id","position","rank")} if previous else {}
    for row in rows:
        old=prior.get(row.player_id)
        row.display_previous_rank=None
        row.display_movement=None
        if previous is None: row.movement_state="no_baseline"
        elif old is None: row.movement_state="new_entry"
        elif old["position"]!=row.position: row.movement_state="new_category"
        else:
            row.movement_state="existing"
            row.display_previous_rank=old["rank"]
            row.display_movement=old["rank"]-row.rank
        row.movement_baseline_at=previous.cutoff_at if previous else None
    return rows
