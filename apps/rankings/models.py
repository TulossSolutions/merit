from django.core.exceptions import ValidationError
from django.db import models
from apps.football.models import Player, Position, Season, Team
from apps.scoring.models import ScoringFormula

class RankingSnapshot(models.Model):
    coverage_summary=models.JSONField(default=dict)
    season=models.ForeignKey(Season,on_delete=models.PROTECT); formula=models.ForeignKey(ScoringFormula,on_delete=models.PROTECT,related_name="snapshots"); published_at=models.DateTimeField(); cutoff_at=models.DateTimeField(); is_public=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["season","formula","cutoff_at"],name="uniq_ranking_snapshot")]
    def save(self,*args,**kwargs):
        if self.pk and RankingSnapshot.objects.filter(pk=self.pk,is_public=True).exists(): raise ValidationError("Published snapshots are immutable.")
        super().save(*args,**kwargs)

class RankingEntry(models.Model):
    snapshot=models.ForeignKey(RankingSnapshot,on_delete=models.CASCADE,related_name="entries"); player=models.ForeignKey(Player,on_delete=models.PROTECT,related_name="ranking_entries"); team=models.ForeignKey(Team,on_delete=models.PROTECT,blank=True,null=True); position=models.CharField(max_length=10,choices=Position.choices); rank=models.PositiveSmallIntegerField(); score=models.DecimalField(max_digits=8,decimal_places=4); previous_rank=models.PositiveSmallIntegerField(blank=True,null=True); movement=models.SmallIntegerField(blank=True,null=True); minutes=models.PositiveIntegerField(); metric_breakdown=models.JSONField(default=dict); context_summary=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["snapshot","position","rank"],name="uniq_snapshot_position_rank"),models.UniqueConstraint(fields=["snapshot","player"],name="uniq_snapshot_player")]
        indexes=[models.Index(fields=["snapshot","position","rank"]),models.Index(fields=["snapshot","player"])]
