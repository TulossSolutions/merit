"""Import owner-reviewed corrections and explicit provider aliases, without API calls."""
import hashlib
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.football.models import Player,PlayerFixture,PlayerTeamSeason,Position
from apps.ingestion.models import PlayerIdentityAlias,ReviewedPlayerPosition,PlayerPositionProfile
from apps.scoring.models import PlayerSeasonScore
from .positions import latest_reviews

LABELS={label:position for position,label in Position.choices if position!=Position.UNKNOWN}

def read_corrections(path):
    source=Path(path).read_bytes()
    corrections={}
    for line in source.decode("utf-8-sig").splitlines():
        cells=[cell.strip() for cell in line.split("|")]
        if len(cells)<2 or not cells[1].isdigit(): continue
        if len(cells)<8: raise ValidationError(f"Incomplete correction row for player {cells[1]}")
        provider_id=cells[1]
        if provider_id=="0": continue
        position=LABELS.get(cells[6])
        if position is None: raise ValidationError(f"Unknown correction category for player {provider_id}")
        if provider_id in corrections and corrections[provider_id]!=position:
            raise ValidationError(f"Conflicting position corrections for player {provider_id}")
        corrections[provider_id]=position
    if not corrections: raise ValidationError("No reviewed position corrections found")
    return corrections,hashlib.sha256(source).hexdigest()

def resolve_provider_aliases(provider,ids=None):
    rows=PlayerIdentityAlias.objects.filter(alias_player__provider=provider).select_related("alias_player","canonical_player")
    if ids is not None: rows=rows.filter(alias_player__provider_id__in=ids)
    return {row.alias_player.provider_id:row.canonical_player for row in rows}

@transaction.atomic
def link_identity(old_id,canonical_id,source_path,source_sha256,reviewer,provider="api_football"):
    if old_id=="0" or canonical_id=="0" or old_id==canonical_id:
        raise ValidationError("Invalid provider identity alias")
    old=Player.objects.select_for_update().get(provider=provider,provider_id=old_id)
    canonical=Player.objects.select_for_update().get(provider=provider,provider_id=canonical_id)
    existing=PlayerIdentityAlias.objects.filter(alias_player=old).first()
    if existing:
        if existing.canonical_player_id!=canonical.pk: raise ValidationError("Alias already has a different canonical player")
        return existing,0
    if PlayerIdentityAlias.objects.filter(alias_player=canonical).exists() or PlayerIdentityAlias.objects.filter(canonical_player=old).exists():
        raise ValidationError("Identity alias chains are not supported")
    rows=PlayerFixture.objects.filter(player=old)
    if rows.filter(fixture_id__in=PlayerFixture.objects.filter(player=canonical).values("fixture_id")).exists():
        raise ValidationError("Overlapping fixture identities require a separate evidence review")
    if old.ranking_entries.exists() or old.campaign_contributions.exists() or PlayerSeasonScore.objects.filter(player=old).exists() or PlayerTeamSeason.objects.filter(player=old).exists():
        raise ValidationError("Historical linked records require a separate identity review")
    alias=PlayerIdentityAlias.objects.create(alias_player=old,canonical_player=canonical,source_path=source_path,
        source_sha256=source_sha256,reviewer=reviewer,reviewed_at=timezone.now(),note=f"Owner-approved provider identity {old_id} -> {canonical_id}; raw IDs retained.")
    moved=rows.update(player=canonical)
    old.active=False; old.save(update_fields=["active","updated_at"])
    return alias,moved

@transaction.atomic
def import_corrections(path,aliases=(),reviewer="project owner",provider="api_football"):
    corrections,digest=read_corrections(path)
    source_path=f"docs/{Path(path).name}"
    ids=set(corrections)|{value for pair in aliases for value in pair}
    players={row.provider_id:row for row in Player.objects.filter(provider=provider,provider_id__in=ids)}
    if ids-players.keys(): raise ValidationError(f"Correction players not found: {sorted(ids-players.keys())}")
    moved=0
    for old,canonical in aliases:
        _,count=link_identity(old,canonical,source_path,digest,reviewer,provider)
        moved+=count
    alias_map=resolve_provider_aliases(provider,corrections)
    canonical_corrections={}
    for pid,position in corrections.items():
        player=alias_map.get(pid,players[pid])
        if player.pk in canonical_corrections and canonical_corrections[player.pk][1]!=position:
            raise ValidationError("Canonical identity has conflicting reviewed positions")
        canonical_corrections[player.pk]=(player,position)
    created=0
    for player,position in canonical_corrections.values():
        record,new=ReviewedPlayerPosition.objects.get_or_create(player=player,source_sha256=digest,
            defaults={"position":position,"source_path":source_path,"reviewer":reviewer,"reviewed_at":timezone.now()})
        if record.position!=position: raise ValidationError("Immutable review source has conflicting position")
        created+=new
    reviews=latest_reviews(canonical_corrections)
    for player,_ in canonical_corrections.values():
        profile=PlayerPositionProfile.objects.filter(player=player).first()
        player.primary_position=profile.position if profile and profile.position!=Position.UNKNOWN else reviews[player.pk].position
        player.save(update_fields=["primary_position","updated_at"])
    return {"supplied":len(corrections),"created_reviews":created,"canonical_players":len(canonical_corrections),"moved_appearances":moved,"source_sha256":digest}
