"""Auditable campaign participation, independent of the performance formula."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from apps.football.models import Fixture, PlayerCampaignContribution, PlayerFixture, PlayerTeamSeason


def validate_campaign_policy(config):
    required={"stage_weights","stage_aliases"}
    optional={"title_points","performance_stage_weights"}
    if not isinstance(config,dict) or not required.issubset(config) or set(config)-required-optional:
        raise ValidationError("Policy requires stage_weights and stage_aliases; only title_points and performance_stage_weights are optional.")
    weights, aliases = config["stage_weights"], config["stage_aliases"]
    if not isinstance(weights,dict) or not weights or not isinstance(aliases,dict):
        raise ValidationError("Stage weights and aliases must be mappings.")
    for stage, value in weights.items():
        try:
            number=Decimal(str(value))
        except (InvalidOperation,ValueError,TypeError):
            raise ValidationError(f"Invalid stage weight: {stage}") from None
        if not isinstance(stage,str) or not stage or not number.is_finite() or number<=0:
            raise ValidationError("All stage weights must be finite and positive.")
    for name,stage in aliases.items():
        if not isinstance(name,str) or not name or name!=name.strip().casefold() or stage not in weights:
            raise ValidationError("Aliases must be normalized exact names mapping to configured stages.")
    performance=config.get("performance_stage_weights")
    if performance is not None:
        if not isinstance(performance,dict) or set(performance)!=set(weights):
            raise ValidationError("Performance weights must configure every stage explicitly.")
        for value in performance.values():
            try: number=Decimal(str(value))
            except (InvalidOperation,ValueError,TypeError): raise ValidationError("Invalid performance weight.") from None
            if not number.is_finite() or number<=0: raise ValidationError("Performance weights must be finite and positive.")
    if "title_points" in config:
        try: points=Decimal(str(config["title_points"]))
        except (InvalidOperation,ValueError,TypeError): raise ValidationError("Invalid title points.") from None
        if not points.is_finite() or points<0: raise ValidationError("Title points must be finite and nonnegative.")
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def campaign_contribution(campaign,player,as_of,prepared=None):
    """Return None with an explicit reason when campaign evidence is incomplete."""
    if timezone.is_naive(as_of): raise ValidationError("Contribution cutoff must be timezone-aware.")
    checksum=validate_campaign_policy(campaign.policy.config)
    if checksum!=campaign.policy.checksum_sha256: raise ValidationError("Campaign policy checksum mismatch.")
    if prepared is None: campaign.full_clean()
    breakdown={"policy_version":campaign.policy.version,"policy_checksum":checksum,
               "campaign":campaign.pk,"winner":campaign.winner_id,"awarded_at":campaign.awarded_at.isoformat(),
               "as_of":as_of.isoformat(),"expected_matches":campaign.expected_matches,
               "verified_at":campaign.verified_at.isoformat() if campaign.verified_at else None,
               "evidence":campaign.evidence,"policy":campaign.policy.config,"matches":[]}

    def result(value=None,reason=""):
        breakdown["reason"]=reason
        digest=hashlib.sha256(json.dumps(breakdown,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        return {"contribution":value,"reason":reason,"breakdown":breakdown,"input_sha256":digest}

    if campaign.awarded_at>as_of: return result(reason="title_not_yet_awarded")
    if not campaign.verified_at or not campaign.evidence.strip(): return result(reason="winner_unverified")
    if not campaign.is_complete: return result(reason="campaign_incomplete")
    fixtures=prepared["fixtures"] if prepared is not None else list(Fixture.objects.filter(competition_season=campaign.edition).filter(
        Q(home_team=campaign.winner)|Q(away_team=campaign.winner)).exclude(
        status=Fixture.Status.CANCELLED).order_by("starts_at","pk"))
    if len(fixtures)!=campaign.expected_matches: return result(reason="campaign_match_count_mismatch")
    rows=prepared["rows"].get(player.pk,{}) if prepared is not None else {row.fixture_id:row for row in PlayerFixture.objects.filter(fixture__in=fixtures,player=player,team=campaign.winner)}
    member=bool(rows) or (player.pk in prepared["members"] if prepared is not None else PlayerTeamSeason.objects.filter(player=player,team=campaign.winner,competition_season=campaign.edition).exists())
    numerator=denominator=Decimal("0")
    config=campaign.policy.config
    for fixture in fixtures:
        if fixture.status!=Fixture.Status.FINISHED or fixture.starts_at>campaign.awarded_at or fixture.starts_at>as_of:
            return result(reason="campaign_fixture_not_finished")
        if not fixture.stats_ingested_at: return result(reason="campaign_player_data_missing")
        has_players=fixture.pk in prepared["has_players"] if prepared is not None else fixture.playerfixture_set.filter(team=campaign.winner).exists()
        if not has_players: return result(reason="campaign_player_data_missing")
        if not fixture.available_minutes or fixture.available_minutes<=0: return result(reason="match_duration_missing")
        raw_stage=(fixture.stage_name or fixture.round_name or "").strip().casefold()
        stage=config["stage_aliases"].get(raw_stage)
        if stage is None: return result(reason="stage_not_configured")
        weight=Decimal(str(config["stage_weights"][stage]))
        minutes=rows[fixture.pk].minutes if fixture.pk in rows else 0
        participation=min(Decimal("1"),Decimal(minutes)/Decimal(fixture.available_minutes))
        numerator+=participation*weight
        denominator+=weight
        breakdown["matches"].append({"fixture":fixture.pk,"provider_fixture":fixture.provider_id,
            "stage":stage,"minutes":minutes,"available_minutes":fixture.available_minutes,
            "context_weight":str(weight),"participation":str(participation)})
    breakdown.update(weighted_participation=str(numerator),maximum_participation=str(denominator))
    if not member: return result(reason="player_not_in_winning_campaign")
    return result(numerator/denominator)


def record_campaign_contribution(campaign,player,as_of,prepared=None):
    result=campaign_contribution(campaign,player,as_of,prepared)
    record,_=PlayerCampaignContribution.objects.get_or_create(campaign=campaign,player=player,as_of=as_of,
        input_sha256=result["input_sha256"],defaults={key:result[key] for key in ("contribution","reason","breakdown")})
    return record


def performance_context_weight(fixture):
    policy=fixture.competition_season.context_policy
    checksum=validate_campaign_policy(policy.config)
    if checksum!=policy.checksum_sha256: raise ValidationError("Campaign policy checksum mismatch.")
    config=policy.config
    stage=config["stage_aliases"].get((fixture.stage_name or fixture.round_name or "").strip().casefold())
    weights=config.get("performance_stage_weights",{})
    if stage is None or stage not in weights:
        raise ValidationError("Configure exact stage aliases and performance weights before scoring a new tournament.")
    return Decimal(str(weights[stage]))
