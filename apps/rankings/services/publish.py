import logging
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from apps.football.models import PlayerFixture, Position
from apps.ingestion.services.positions import PROFILE_SOURCES
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.scoring.models import PlayerSeasonScore
from apps.scoring.services.formulas import validate_formula
from apps.scoring.services.quality import check_quality
from .coverage import snapshot_coverage
from .queries import previous_snapshot

logger=logging.getLogger(__name__)

@transaction.atomic
def publish(season,formula,cutoff,force=False,*,allow_unavailable=False,weekly_replay=None):
    if validate_formula(formula.config)!=formula.checksum_sha256: raise ValueError("Formula checksum does not match its immutable configuration")
    fatal=[issue for issue in check_quality(season,cutoff,allow_unavailable=allow_unavailable) if issue[0]=="ERROR"]
    if fatal: raise ValueError(f"Fatal data-quality errors block publication: {fatal}")
    existing=RankingSnapshot.objects.filter(season=season,formula=formula,cutoff_at=cutoff).first()
    if existing and not force: raise ValueError("Snapshot already exists; published snapshots are immutable")
    if existing and force: existing.entries.all().delete(); existing.delete()
    scores=list(PlayerSeasonScore.objects.filter(season=season,formula=formula,as_of=cutoff,eligible=True,final_score__isnull=False).select_related("player").order_by("position","-final_score","-performance_score","-minutes","player_id"))
    if not scores: raise ValueError("No eligible scores exist for the exact cutoff")
    previous=previous_snapshot(RankingSnapshot(season=season,formula=formula,cutoff_at=cutoff)); old={(e.position,e.player_id):e.rank for e in previous.entries.all()} if previous else {}
    coverage=snapshot_coverage(season,cutoff,allow_unavailable)
    if weekly_replay is not None: coverage["weekly_replay"]=weekly_replay
    if formula.config.get("position_source") in PROFILE_SOURCES:
        unknown=list(PlayerSeasonScore.objects.filter(season=season,formula=formula,as_of=cutoff,position=Position.UNKNOWN).values_list("minutes",flat=True))
        coverage["position_profiles"]={"source":"api_football.players/profiles.position","unavailable_players":len(unknown),
            "unavailable_minutes":sum(unknown),"policy":"current_profile_all_imported_seasons"}
        if formula.config["position_source"]=="api_football_profile_with_reviewed_fallback":
            coverage["position_profiles"]["policy"]="profile_first_reviewed_fallback_all_imported_seasons"
            coverage["position_profiles"]["reviewed_players"]=PlayerSeasonScore.objects.filter(
                season=season,formula=formula,as_of=cutoff,context_summary__award_position__source="manual_reviewed_position").count()
        if formula.config.get("position_overrides"):
            coverage["position_profiles"]["policy"]="owner_override_profile_first_reviewed_fallback_all_imported_seasons"
            coverage["position_profiles"]["overridden_players"]=PlayerSeasonScore.objects.filter(
                season=season,formula=formula,as_of=cutoff,context_summary__award_position__source="manual_position_override").count()
    snapshot=RankingSnapshot.objects.create(season=season,formula=formula,published_at=timezone.now(),cutoff_at=cutoff,is_public=False,
        coverage_summary=coverage)
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
