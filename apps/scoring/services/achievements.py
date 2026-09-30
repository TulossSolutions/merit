from decimal import Decimal, InvalidOperation
from collections import defaultdict
from django.core.exceptions import ValidationError
from django.db.models import Q
from apps.football.models import Competition,Fixture,PlayerFixture,PlayerTeamSeason,WinningCampaign
from .campaigns import campaign_contribution, record_campaign_contribution


def validate_achievement_config(config):
    if config is None: return
    if not isinstance(config,dict) or set(config)!={"enabled","weight","points_cap","policy_versions"}:
        raise ValidationError("Achievements require enabled, weight, points_cap and policy_versions.")
    if not isinstance(config["enabled"],bool) or not isinstance(config["policy_versions"],dict):
        raise ValidationError("Invalid achievement enable flag or policy mapping.")
    try:
        weight=Decimal(str(config["weight"]))
        cap=Decimal(str(config["points_cap"])) if config["points_cap"] is not None else None
    except (InvalidOperation,ValueError,TypeError): raise ValidationError("Invalid achievement weight or cap.") from None
    if not weight.is_finite() or not 0<=weight<=1: raise ValidationError("Achievement weight must lie between zero and one.")
    if config["enabled"]:
        if weight<=0 or cap is None or not cap.is_finite() or cap<=0 or not config["policy_versions"]:
            raise ValidationError("Enabling achievements requires a positive weight/cap and explicit competition policy versions.")
    elif weight!=0:
        raise ValidationError("Disabled achievements must have zero weight.")


def prepare_season_achievements(season,as_of,config):
    """Load/validate campaign evidence once per calculation, not once per player."""
    validate_achievement_config(config)
    if not config or not config["enabled"]: return []
    campaigns=WinningCampaign.objects.filter(edition__season=season,awarded_at__lte=as_of).select_related("edition__competition","policy","winner")
    prepared=[]
    for campaign in campaigns:
        competition=campaign.edition.competition
        key=f"{competition.provider}:{competition.provider_id}"
        version=config["policy_versions"].get(key)
        if version is None: continue
        if campaign.policy.version!=version: raise ValidationError(f"Campaign policy mismatch for {key}.")
        if not campaign.verified_at or not campaign.evidence.strip() or not campaign.is_complete:
            raise ValidationError(f"Competition outcome or campaign is unverified: {key}")
        campaign.full_clean()
        fixtures=list(Fixture.objects.filter(competition_season=campaign.edition).filter(Q(home_team=campaign.winner)|Q(away_team=campaign.winner)).exclude(status="CANCELLED").order_by("starts_at","pk"))
        rows=defaultdict(dict)
        for row in PlayerFixture.objects.filter(fixture__in=fixtures,team=campaign.winner).select_related("player"):
            rows[row.player_id][row.fixture_id]=row
        data={"fixtures":fixtures,"rows":rows,"members":set(PlayerTeamSeason.objects.filter(competition_season=campaign.edition,team=campaign.winner).values_list("player_id",flat=True)),
            "has_players":{row.fixture_id for items in rows.values() for row in items.values()}}
        # Even non-winners must not get scores against an incompletely verified campaign.
        representative=next((row.player for items in rows.values() for row in items.values()),None)
        if representative is None: raise ValidationError("Incomplete achievement evidence: campaign_player_data_missing")
        evidence=campaign_contribution(campaign,representative,as_of,data)
        if evidence["reason"]: raise ValidationError(f"Incomplete achievement evidence: {evidence['reason']}")
        if "title_points" not in campaign.policy.config: raise ValidationError(f"Title points are not configured for {key}.")
        prepared.append((campaign,data))
    for key in config["policy_versions"]:
        provider,separator,provider_id=key.partition(":")
        if not separator: raise ValidationError("Competition policy keys must use provider:provider_id.")
        editions=season.competitionseason_set.filter(competition__provider=provider,competition__provider_id=provider_id)
        if not editions.exists():
            if Competition.objects.filter(provider=provider,provider_id=provider_id,participant_type="NATIONAL",is_tracked=True).exists():
                continue  # Nonannual tournaments do not award a title in every period.
            raise ValidationError(f"Missing configured competition edition: {key}")
        if editions.filter(Q(ends_on__isnull=True)|Q(ends_on__lte=as_of.date()),winning_campaign__isnull=True).exists():
            raise ValidationError(f"Missing competition outcome: {key}")
    return prepared


def season_achievement(player,season,as_of,config,prepared=None):
    validate_achievement_config(config)
    if not config or not config["enabled"]: return None,{"enabled":False}
    prepared=prepare_season_achievements(season,as_of,config) if prepared is None else prepared
    points=Decimal("0"); details=[]
    for campaign,data in prepared:
        if player.pk not in data["rows"] and player.pk not in data["members"]: continue
        record=record_campaign_contribution(campaign,player,as_of,data)
        if record.contribution is None: raise ValidationError(f"Incomplete achievement evidence: {record.reason}")
        competition=campaign.edition.competition
        title_points=Decimal(str(campaign.policy.config["title_points"]))
        earned=title_points*record.contribution
        points+=earned
        details.append({"campaign":campaign.pk,"competition":competition.name,"team":campaign.winner.name,
            "contribution":str(record.contribution),"contribution_percent":str(record.contribution*100),"title_points":str(title_points),"earned_points":str(earned),
            "record":record.pk,"input_sha256":record.input_sha256,"policy_version":campaign.policy.version})
    cap=Decimal(str(config["points_cap"]))
    return min(points,cap)/cap*100,{"enabled":True,"points":str(points),"points_cap":str(cap),
                                 "weight":str(config["weight"]),"campaigns":details}
