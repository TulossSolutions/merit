from django.db import models
from apps.football.models import Player, Position

class RawProviderPayload(models.Model):
    provider=models.CharField(max_length=30); resource_type=models.CharField(max_length=50); provider_resource_id=models.CharField(max_length=100); request_path=models.CharField(max_length=500); payload=models.JSONField(); payload_sha256=models.CharField(max_length=64); received_at=models.DateTimeField(auto_now_add=True); http_status=models.PositiveSmallIntegerField()
    class Meta: indexes=[models.Index(fields=["provider","resource_type","provider_resource_id"]),models.Index(fields=["received_at"])]

class ProviderSyncState(models.Model):
    provider=models.CharField(max_length=30); sync_key=models.CharField(max_length=150); cursor=models.CharField(max_length=500,blank=True,null=True); last_success_at=models.DateTimeField(blank=True,null=True); last_attempt_at=models.DateTimeField(blank=True,null=True); last_error=models.TextField(blank=True,null=True); metadata=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True,null=True); updated_at=models.DateTimeField(auto_now=True,null=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["provider","sync_key"],name="uniq_provider_sync_key")]

class PlayerPositionProfile(models.Model):
    player=models.OneToOneField(Player,on_delete=models.CASCADE,related_name="position_profile")
    position=models.CharField(max_length=10,choices=Position.choices,default=Position.UNKNOWN)
    provider_position=models.CharField(max_length=100,blank=True)
    source_payload=models.ForeignKey(RawProviderPayload,on_delete=models.PROTECT,blank=True,null=True)
    checked_at=models.DateTimeField()
    reason=models.CharField(max_length=100,blank=True)

class StatsBombBacktestPayload(models.Model):
    resource_type=models.CharField(max_length=30); source_path=models.CharField(max_length=500,unique=True); payload=models.JSONField(); payload_sha256=models.CharField(max_length=64); imported_at=models.DateTimeField(auto_now=True)
    class Meta: indexes=[models.Index(fields=["resource_type"])]
