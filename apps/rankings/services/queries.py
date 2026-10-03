from apps.football.models import Season
from django.db.models import Q
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


def present_category_notices(rows, snapshot):
    """Keep a category-change notice for three forward, public ranking updates.

    Read only the publication history available at the selected snapshot. Replay
    publications behind the current cutoff and same-cutoff revisions do not age
    a notice. A correction at the current cutoff can start a new notice.
    """
    rows = list(rows)
    for row in rows:
        row.category_change_notice = False
        row.category_change_update = None
    if not snapshot or not rows:
        return rows
    publications = list(RankingSnapshot.objects.filter(
        season_id=snapshot.season_id, is_public=True, cutoff_at__lte=snapshot.cutoff_at,
    ).filter(Q(published_at__lt=snapshot.published_at) | Q(published_at=snapshot.published_at, pk__lte=snapshot.pk))
        .order_by("published_at", "pk").values("pk", "cutoff_at", "formula__is_active"))
    # Build one forward publication timeline, shared by all displayed players.
    timeline = []
    for publication in publications:
        if timeline and publication["cutoff_at"] < timeline[-1]["cutoff_at"]:
            continue
        if timeline and publication["cutoff_at"] == timeline[-1]["cutoff_at"]:
            # Match the existing active-formula preference for same-date revisions.
            if timeline[-1]["formula__is_active"] and not publication["formula__is_active"]:
                continue
        timeline.append(publication)
    from apps.rankings.models import RankingEntry
    positions = {}
    for entry in RankingEntry.objects.filter(snapshot_id__in=[item["pk"] for item in timeline],
            player_id__in=[row.player_id for row in rows]).values("snapshot_id", "player_id", "position"):
        positions.setdefault(entry["snapshot_id"], {})[entry["player_id"]] = entry["position"]
    prior = {}
    notices = {}
    update = 0
    cutoff = None
    for publication in timeline:
        if publication["cutoff_at"] != cutoff:
            update += 1
            cutoff = publication["cutoff_at"]
        current = positions.get(publication["pk"], {})
        for player_id, position in current.items():
            if player_id in prior and prior[player_id] != position:
                notices[player_id] = (update, position)
        prior = current
    for row in rows:
        notice = notices.get(row.player_id)
        if notice and notice[1] == row.position and update - notice[0] < 3:
            row.category_change_notice = True
            row.category_change_update = update - notice[0] + 1
    return rows
