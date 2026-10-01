"""Resolve award positions without changing retained API evidence."""
import re
from django.core.exceptions import ValidationError
from apps.football.models import Position
from apps.ingestion.models import PlayerPositionProfile,ReviewedPlayerPosition

PROFILE_SOURCES={"api_football_profile","api_football_profile_with_reviewed_fallback"}

def validate_position_overrides(overrides):
    if not isinstance(overrides,dict): raise ValueError("Position overrides must be an audited mapping")
    fields={"position","reviewer","source_path","source_sha256"}
    for identity,review in overrides.items():
        if not isinstance(identity,str) or not re.fullmatch(r"api_football:[1-9][0-9]*",identity):
            raise ValueError("Position overrides require a nonzero API-Football player identity")
        if not isinstance(review,dict) or set(review)!=fields:
            raise ValueError("Position overrides require position, reviewer, source_path and source_sha256")
        if review["position"] not in (Position.GK,Position.DEF,Position.MID,Position.FWD):
            raise ValueError("Position overrides require a known award category")
        if not isinstance(review["reviewer"],str) or not review["reviewer"].strip():
            raise ValueError("Position overrides require an explicit reviewer")
        if not isinstance(review["source_path"],str) or not re.fullmatch(r"docs/[a-zA-Z0-9_-]+\.md",review["source_path"]):
            raise ValueError("Position overrides require a source document in docs/")
        if not isinstance(review["source_sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",review["source_sha256"]):
            raise ValueError("Position overrides require a source SHA-256")


def active_position_overrides():
    from apps.scoring.models import ScoringFormula
    config=ScoringFormula.objects.filter(is_active=True).values_list("config",flat=True).first() or {}
    overrides=config.get("position_overrides",{})
    validate_position_overrides(overrides)
    return overrides

def latest_reviews(player_ids=None):
    rows=ReviewedPlayerPosition.objects.exclude(player__provider_id="0").select_related("player").order_by("-reviewed_at","-pk")
    if player_ids is not None: rows=rows.filter(player_id__in=player_ids)
    result={}
    for row in rows: result.setdefault(row.player_id,row)
    return result

def award_positions(player_ids,reviewed=False,overrides=None):
    overrides={} if overrides is None else overrides
    validate_position_overrides(overrides)
    profiles={row.player_id:row for row in PlayerPositionProfile.objects.filter(player_id__in=player_ids)
        .select_related("source_payload","player").only("player_id","player__provider","player__provider_id","position","provider_position","checked_at","reason",
            "source_payload_id","source_payload__payload_sha256")}
    missing=set(player_ids)-profiles.keys()
    if missing: raise ValidationError(f"Verified player profile lookup required for {len(missing)} players")
    reviews=latest_reviews(player_ids) if reviewed else {}
    result={}
    for pid,profile in profiles.items():
        if profile.position!=Position.UNKNOWN and (not profile.source_payload_id or profile.reason):
            raise ValidationError("Known award positions require verified profile payload evidence")
        evidence={"source":"api_football.players/profiles.position","position":profile.position,
            "provider_position":profile.provider_position,"checked_at":profile.checked_at.isoformat(),
            "payload_sha256":profile.source_payload.payload_sha256 if profile.source_payload else None,
            "policy":"current_profile_all_imported_seasons"}
        if profile.position==Position.UNKNOWN: evidence["reason"]=profile.reason
        override=overrides.get(f"{profile.player.provider}:{profile.player.provider_id}")
        if reviewed and profile.player.provider_id=="0":
            evidence.update(position=Position.UNKNOWN,reason="invalid_provider_player_id")
        elif override:
            evidence={"source":"manual_position_override",**override,"profile_position":profile.position,
                "provider_position":profile.provider_position,"profile_checked_at":profile.checked_at.isoformat(),
                "profile_payload_sha256":evidence["payload_sha256"],"policy":"owner_override_all_imported_seasons"}
        elif profile.position==Position.UNKNOWN and pid in reviews:
            review=reviews[pid]
            if review.position not in (Position.GK,Position.DEF,Position.MID,Position.FWD):
                raise ValidationError("Reviewed positions must use a known award category")
            evidence={"source":"manual_reviewed_position","position":review.position,"reviewer":review.reviewer,
                "checked_at":review.reviewed_at.isoformat(),"source_path":review.source_path,"source_sha256":review.source_sha256,
                "profile_payload_sha256":evidence["payload_sha256"],"policy":"reviewed_fallback_all_imported_seasons"}
        result[pid]=evidence
    return result
