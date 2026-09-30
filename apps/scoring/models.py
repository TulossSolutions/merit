from django.core.exceptions import ValidationError
from django.db import models
from apps.football.models import Player, Position, Season

class ScoringFormula(models.Model):
    version=models.CharField(max_length=30,unique=True); name=models.CharField(max_length=150); config=models.JSONField(); checksum_sha256=models.CharField(max_length=64); is_active=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True); activated_at=models.DateTimeField(blank=True,null=True); notes=models.TextField(blank=True)
    def save(self,*args,**kwargs):
        if self.pk and self.snapshots.exists():
            old=ScoringFormula.objects.get(pk=self.pk)
            if old.config != self.config or old.checksum_sha256 != self.checksum_sha256 or old.version != self.version: raise ValidationError("A published scoring formula is immutable.")
        super().save(*args,**kwargs)
    def __str__(self): return f"v{self.version}"

class PlayerSeasonScore(models.Model):
    achievement_score=models.DecimalField(max_digits=8,decimal_places=4,blank=True,null=True)
    achievement_breakdown=models.JSONField(default=dict)
    season=models.ForeignKey(Season,on_delete=models.CASCADE); player=models.ForeignKey(Player,on_delete=models.CASCADE); formula=models.ForeignKey(ScoringFormula,on_delete=models.PROTECT); position=models.CharField(max_length=10,choices=Position.choices); as_of=models.DateTimeField(); eligible=models.BooleanField(); minutes=models.PositiveIntegerField(); appearances=models.PositiveIntegerField(); performance_score=models.DecimalField(max_digits=8,decimal_places=4,blank=True,null=True); availability_score=models.DecimalField(max_digits=8,decimal_places=4,blank=True,null=True); final_score=models.DecimalField(max_digits=8,decimal_places=4,blank=True,null=True); metric_breakdown=models.JSONField(default=dict); coverage_breakdown=models.JSONField(default=dict); context_summary=models.JSONField(default=dict); calculated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["season","player","formula","as_of"],name="uniq_player_season_score")]
        indexes=[models.Index(fields=["season","formula","as_of","position"])]
