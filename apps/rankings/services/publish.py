import logging
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from apps.football.models import PlayerFixture, Position
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.scoring.models import PlayerSeasonScore
from apps.scoring.services.formulas import validate_formula
from apps.scoring.services.quality import check_quality
from .coverage import snapshot_coverage

logger=logging.getLogger(__name__)

@transaction.atomic
def publish(season,formula,cutoff,force=False,*,allow_unavailable=False):
    if validate_formula(formula.config)!=formula.checksum_sha256: raise ValueError("Formula checksum does not match its immutable configuration")
    fatal=[issue for issue in check_quality(season,cutoff,allow_unavailable=allow_unavailable) if issue[0]=="ERROR"]
    if fatal: raise ValueError(f"Fatal data-quality errors block publication: {fatal}")
    existing=RankingSnapshot.objects.filter(season=season,formula=formula,cutoff_at=cutoff).first()
    if existing and not force: raise ValueError("Snapshot already exists; published snapshots are immutable")
    if existing and force: existing.entries.all().delete(); existing.delete()
    scores=list(PlayerSeasonScore.objects.filter(season=season,formula=formula,as_of=cutoff,eligible=True,final_score__isnull=False).select_related("player").order_by("position","-final_score","-performance_score","-minutes","player_id"))
    if not scores: raise ValueError("No eligible scores exist for the exact cutoff")
    previous=RankingSnapshot.objects.filter(season=season,is_public=True,cutoff_at__lt=cutoff).order_by("-cutoff_at").first(); old={(e.position,e.player_id):e.rank for e in previous.entries.all()} if previous else {}
    snapshot=RankingSnapshot.objects.create(season=season,formula=formula,published_at=timezone.now(),cutoff_at=cutoff,is_public=False,
        coverage_summary=snapshot_coverage(season,cutoff,allow_unavailable))
    by_position={p:[] for p in (Position.GK,Position.DEF,Position.MID,Position.FWD)}
    for score in scores: by_position[score.position].append(score)
    entries=[]
    for position,position_scores in by_position.items():
        for rank,score in enumerate(position_scores,start=1):
            team=PlayerFixture.objects.filter(player=score.player,fixture__starts_at__lte=cutoff).exclude(fixture__competition_season__competition__participant_type="NATIONAL").order_by("-fixture__starts_at","-fixture_id").values_list("team_id",flat=True).first(); prior=old.get((position,score.player_id))
            entries.append(RankingEntry(snapshot=snapshot,player=score.player,team_id=team,position=position,rank=rank,score=score.final_score,previous_rank=prior,movement=prior-rank if prior else None,minutes=score.minutes,metric_breakdown=score.metric_breakdown,context_summary=score.context_summary))
    RankingEntry.objects.bulk_create(entries); RankingSnapshot.objects.filter(pk=snapshot.pk).update(is_public=True); snapshot.is_public=True
    if not season.is_published:
        season.is_published=True; season.save(update_fields=["is_published","updated_at"])
    try:
        cache.delete_pattern(f"*{season.slug}*")
    except AttributeError:
        cache.clear()
    logger.info("ranking_publication_complete snapshot=%s season=%s entries=%s",snapshot.pk,season.slug,len(entries)); return snapshot
