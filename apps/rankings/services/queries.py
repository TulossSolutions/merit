from apps.football.models import Season
from apps.rankings.models import RankingSnapshot

def latest_snapshot(season=None):
    season=season or Season.objects.filter(is_current=True,is_published=True).first()
    return RankingSnapshot.objects.filter(season=season,is_public=True).select_related("season","formula").order_by("-cutoff_at","-published_at","-pk").first() if season else None

def entries(snapshot,position,limit=None):
    qs=snapshot.entries.filter(position=position).select_related("player","team").order_by("rank") if snapshot else []
    return qs[:limit] if limit else qs
