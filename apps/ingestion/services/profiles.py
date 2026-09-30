"""Quota-bounded, cached profile positions. No calls are made by public views."""
from datetime import timedelta
import hashlib
import json

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.football.models import Player, Position
from apps.ingestion.models import PlayerPositionProfile, ProviderSyncState, RawProviderPayload
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.ingestion.providers.positions import normalize_position


class ProfileCatalogue:
    def __init__(self, provider, *, now=None, expired=lambda: False, report=print):
        self.provider=provider
        self.now=now or timezone.now()
        self.expired=expired
        self.report=report
        self.state,_=ProviderSyncState.objects.get_or_create(provider=provider.provider_name,sync_key="player-profile-catalogue")
        self.index={}

    def remember(self, raw):
        for row in raw.payload["response"]:
            player=row["player"]
            self.index[str(player["id"])]=(player.get("position") or "",raw.pk,raw.received_at)

    def sync(self):
        meta=dict(self.state.metadata)
        fresh=self.state.last_success_at and self.state.last_success_at>=self.now-timedelta(days=7)
        if meta.get("status")=="complete" and not fresh:
            meta={}
        if not meta:
            meta={"generation":self.now.isoformat(),"next_page":1,"total_pages":1,"status":"running"}
        prefix=meta["generation"]+":"
        for raw in RawProviderPayload.objects.filter(provider=self.provider.provider_name,resource_type="player_profile_page",
            provider_resource_id__startswith=prefix).order_by("pk").iterator(chunk_size=50):
            self.remember(raw)
        self.state.metadata=meta
        self.state.last_attempt_at=self.now
        self.state.last_error=""
        self.state.save(update_fields=["metadata","last_attempt_at","last_error","updated_at"])
        if meta["status"]=="complete":
            return self.apply_profiles()
        try:
            while meta["next_page"]<=meta["total_pages"]:
                if self.expired(): raise TimeoutError("Profile catalogue reached its checkpoint time limit")
                page=meta["next_page"]
                payload=self.provider.get_profile_page(page)
                encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
                with transaction.atomic():
                    raw=RawProviderPayload.objects.create(provider=self.provider.provider_name,resource_type="player_profile_page",
                        provider_resource_id=f"{prefix}{page}",request_path=f"/players/profiles?page={page}",payload=payload,
                        payload_sha256=hashlib.sha256(encoded).hexdigest(),http_status=200)
                    meta.update(status="running",next_page=page+1,total_pages=payload["paging"]["total"])
                    self.state.metadata=meta
                    self.state.save(update_fields=["metadata","updated_at"])
                self.remember(raw)
                if page==1 or page%50==0 or page==meta["total_pages"]:
                    self.report(f"Player profiles: cached page {page}/{meta['total_pages']}",flush=True)
            meta.update(status="complete",profiles=len(self.index))
            self.state.metadata=meta
            self.state.last_success_at=timezone.now()
            self.state.save(update_fields=["metadata","last_success_at","updated_at"])
            return self.apply_profiles()
        except Exception as exc:
            meta["status"]="checkpointed" if isinstance(exc,(ProviderRequestLimitReached,TimeoutError)) else "failed"
            self.state.metadata=meta
            self.state.last_error=str(exc)
            self.state.save(update_fields=["metadata","last_error","updated_at"])
            raise

    @transaction.atomic
    def apply_profiles(self):
        if self.state.metadata.get("status")!="complete":
            raise ValueError("Profile catalogue is incomplete; absent players cannot be classified")
        generation=self.state.metadata["generation"]
        players=list(Player.objects.filter(provider=self.provider.provider_name,playerfixture__isnull=False)
            .filter(Q(position_profile__isnull=True)|Q(position_profile__checked_at__lt=generation)).distinct())
        profiles=[]
        for player in players:
            raw_position,payload_id,checked_at=self.index.get(player.provider_id,("",None,self.state.last_success_at))
            position=normalize_position(raw_position)
            reason="" if position!=Position.UNKNOWN else "profile_not_found" if payload_id is None else "profile_position_unavailable"
            profiles.append(PlayerPositionProfile(player=player,position=position,provider_position=raw_position,
                source_payload_id=payload_id,checked_at=checked_at,reason=reason))
            player.primary_position=position
        if profiles:
            PlayerPositionProfile.objects.bulk_create(profiles,batch_size=500,update_conflicts=True,unique_fields=["player"],
                update_fields=["position","provider_position","source_payload","checked_at","reason"])
            Player.objects.bulk_update(players,["primary_position"],batch_size=500)
        return {"applied":len(profiles),"unavailable":sum(profile.position==Position.UNKNOWN for profile in profiles),
            "pages":self.state.metadata["total_pages"],"cached":True}
