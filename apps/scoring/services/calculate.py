from collections import defaultdict
from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.ingestion.models import PlayerPositionProfile
from apps.football.models import Competition, PlayerFixture, Position
from apps.scoring.models import PlayerSeasonScore
from .achievements import prepare_season_achievements,season_achievement
from apps.football.award_periods import fixtures_for_award_period

logger=logging.getLogger(__name__)

def average_percentiles(values):
    ordered=sorted(values.items(),key=lambda x:(x[1],x[0])); result={}; i=0; n=len(ordered)
    while i<n:
        j=i
        while j+1<n and ordered[j+1][1]==ordered[i][1]: j+=1
        pct=Decimal("100") if n==1 else (Decimal(i+j)/Decimal("2"))/Decimal(n-1)*Decimal("100")
        for k in range(i,j+1): result[ordered[k][0]]=pct
        i=j+1
    return result

def _aggregate(rows,metric,award_position=None):
    key=metric["source"]; available=[]; minutes=sum(r.minutes for r in rows)
    if metric["aggregation"]=="DERIVED_RATE" and key=="clean_sheet":
        eligible=[r for r in rows if (award_position==Position.GK or (award_position is None and r.position==Position.GK)) and r.minutes>=60]
        if not eligible:return None
        clean=sum(1 for r in eligible if (r.team_id==r.fixture.home_team_id and r.fixture.away_score==0) or (r.team_id==r.fixture.away_team_id and r.fixture.home_score==0))
        return Decimal(clean)/Decimal(len(eligible))
    for row in rows:
        item=next((m for m in row.metrics.all() if m.metric_key==key and m.is_available),None)
        if item: available.append((row,item))
    if not available or not minutes:return None
    agg=metric["aggregation"]
    if agg=="RATE":
        numerator=sum((m.numerator for _,m in available if m.numerator is not None),Decimal("0")); denominator=sum((m.denominator for _,m in available if m.denominator is not None),Decimal("0"))
        return numerator/denominator if denominator else None
    total=sum(((m.value or Decimal("0"))*(r.context_factor or Decimal("1")) if metric.get("context_adjust") and agg=="COUNT_PER90" else (m.value or Decimal("0")) for r,m in available),Decimal("0"))
    return total/Decimal(minutes)*Decimal("90")

@transaction.atomic
def recompute_scores(season,formula,as_of):
    logger.info("score_calculation_start season=%s formula=%s cutoff=%s",season.slug,formula.version,as_of)
    prepared_achievements=prepare_season_achievements(season,as_of,formula.config.get("achievements"))
    rows=PlayerFixture.objects.filter(fixture__in=fixtures_for_award_period(season),fixture__competition_season__competition__is_tracked=True,fixture__status="FINISHED",fixture__starts_at__lte=as_of,fixture__stats_ingested_at__isnull=False).select_related("fixture","fixture__competition_season__competition","player").prefetch_related("metrics").order_by("fixture__starts_at","fixture_id","player_id")
    rows=list(rows)
    profile_based=formula.config.get("position_source")=="api_football_profile"
    profiles={}
    if profile_based:
        profiles={item.player_id:item for item in PlayerPositionProfile.objects.filter(player_id__in={row.player_id for row in rows})
            .select_related("source_payload").only("player_id","position","provider_position","checked_at","reason",
                "source_payload_id","source_payload__payload_sha256")}
        missing={row.player_id for row in rows}-profiles.keys()
        if missing: raise ValidationError(f"Verified player profile lookup required for {len(missing)} players")
        if any(item.position!=Position.UNKNOWN and (not item.source_payload_id or item.reason) for item in profiles.values()):
            raise ValidationError("Known award positions require verified profile payload evidence")
    grouped=defaultdict(list)
    for row in rows:
        position=profiles[row.player_id].position if profile_based else row.position
        grouped[(position,row.player_id)].append(row)
    configs=formula.config; output=[]
    if profile_based:
        for (position,pid),items in grouped.items():
            if position!=Position.UNKNOWN: continue
            profile=profiles[pid]
            score,_=PlayerSeasonScore.objects.update_or_create(season=season,player_id=pid,formula=formula,as_of=as_of,defaults={
                "position":Position.UNKNOWN,"eligible":False,"minutes":sum(row.minutes for row in items),"appearances":len(items),
                "performance_score":None,"availability_score":None,"final_score":None,"achievement_score":None,"achievement_breakdown":{},
                "metric_breakdown":{},"coverage_breakdown":{},"context_summary":{"award_position":{"source":"api_football.players/profiles.position",
                    "position":Position.UNKNOWN,"reason":profile.reason,"checked_at":profile.checked_at.isoformat()}}})
            output.append(score)
    for position in (Position.GK,Position.DEF,Position.MID,Position.FWD):
        player_rows={pid:items for (pos,pid),items in grouped.items() if pos==position}; aggregate={pid:{} for pid in player_rows}
        for pid,items in player_rows.items():
            for metric in configs["positions"][position]["metrics"]: aggregate[pid][metric["key"]]=_aggregate(items,metric,position if profile_based else None)
        population=[pid for pid,items in player_rows.items() if sum(x.minutes for x in items)>=180]
        active={}; coverage={}
        for metric in configs["positions"][position]["metrics"]:
            cov=(Decimal(sum(aggregate[p][metric["key"]] is not None for p in population))/Decimal(len(population))) if population else Decimal("0")
            active[metric["key"]]=cov>=Decimal(str(configs["coverage_threshold"])); coverage[metric["key"]]={"coverage":float(cov),"active":active[metric["key"]]}
        total_weight=sum((Decimal(str(m["weight"])) for m in configs["positions"][position]["metrics"] if active[m["key"]]),Decimal("0"))
        percentiles={}
        for metric in configs["positions"][position]["metrics"]:
            vals={pid:data[metric["key"]] for pid,data in aggregate.items() if data[metric["key"]] is not None}
            percentiles[metric["key"]]=average_percentiles(vals) if active[metric["key"]] else {}
        for pid,items in player_rows.items():
            minutes=sum(x.minutes for x in items); eligible=minutes>=season.eligibility_minutes(as_of.date()) and total_weight>0; breakdown={}; performance=Decimal("0")
            for metric in configs["positions"][position]["metrics"]:
                key=metric["key"]; base=Decimal(str(metric["weight"])); effective=base/total_weight if active[key] and total_weight else Decimal("0"); pct=percentiles[key].get(pid)
                score=(Decimal("100")-pct if metric["direction"]=="negative" and pct is not None else pct)
                if score is not None: performance+=score*effective
                breakdown[key]={"label":metric["label"],"raw_value":float(aggregate[pid][key]) if aggregate[pid][key] is not None else None,"percentile":float(score) if score is not None else None,"base_weight":float(base),"effective_weight":float(effective),"active":active[key],"direction":metric["direction"]}
            availability=min(Decimal("100"),Decimal(minutes)/Decimal(season.availability_target_minutes(as_of.date()))*Decimal("100")); final=performance*Decimal(str(configs["performance_weight"]))+availability*Decimal(str(configs["availability_weight"])) if eligible else None
            achievement,achievement_breakdown=season_achievement(items[0].player,season,as_of,configs.get("achievements"),prepared_achievements)
            if final is not None and achievement is not None:
                weight=Decimal(str(configs["achievements"]["weight"]))
                final=final*(1-weight)+achievement*weight
            contexts=[x for x in items if x.context_factor is not None]
            summary={"average_opponent_elo":float(sum((x.opponent_elo_before for x in contexts),Decimal("0"))/len(contexts)) if contexts else None,"average_context_factor":float(sum((x.context_factor for x in contexts),Decimal("0"))/len(contexts)) if contexts else None,"domestic_minutes":sum(x.minutes for x in items if x.fixture.competition_season.competition.competition_type==Competition.Type.DOMESTIC_LEAGUE),"ucl_minutes":sum(x.minutes for x in items if x.fixture.competition_season.competition.competition_type==Competition.Type.UCL)}
            if configs.get("achievements"):
                summary["achievements"]={**achievement_breakdown,"score":str(achievement) if achievement is not None else None}
            if profile_based:
                profile=profiles[pid]
                summary["award_position"]={"source":"api_football.players/profiles.position","position":position,
                    "provider_position":profile.provider_position,"checked_at":profile.checked_at.isoformat(),
                    "payload_sha256":profile.source_payload.payload_sha256 if profile.source_payload else None,
                    "policy":"current_profile_all_imported_seasons"}
            score,_=PlayerSeasonScore.objects.update_or_create(season=season,player_id=pid,formula=formula,as_of=as_of,defaults={"position":position,"eligible":eligible,"minutes":minutes,"appearances":len(items),"performance_score":performance if total_weight else None,"availability_score":availability,"achievement_score":achievement,"achievement_breakdown":achievement_breakdown,"final_score":final,"metric_breakdown":breakdown,"coverage_breakdown":coverage,"context_summary":summary}); output.append(score)
    logger.info("score_calculation_complete season=%s formula=%s players=%s",season.slug,formula.version,len(output)); return output
