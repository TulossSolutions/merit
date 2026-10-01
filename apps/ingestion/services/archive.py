"""Resumable, quota-bounded archive ingestion. HTTP pages never invoke this service."""
from datetime import date,datetime,time,timedelta,timezone
import hashlib
import json
import re
import time as clock

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count,Q
from django.utils import timezone as django_timezone
from django.utils.text import slugify

from apps.football.award_periods import award_year,ensure_award_period,fixtures_for_award_period
from apps.football.models import CampaignPolicy,Competition,CompetitionSeason,Fixture,Team,TeamCompetitionSeason,WinningCampaign
from apps.ingestion.models import ProviderSyncState,RawProviderPayload
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.scoring.models import ScoringFormula
from apps.scoring.services.achievements import prepare_season_achievements
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.campaigns import prepare_campaign_data, validate_campaign_policy
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula
from .sync import ingest_fixture_bundle
from .profiles import ProfileCatalogue
from .outcomes import reviewed_outcome, review_reference, winner_fixture_ids

CLUB_IDS={"39","140","135","78","61","2"}  # Verified provider catalog IDs.
NATIONAL_NAMES={"World Cup","Euro Championship","Copa America","Africa Cup of Nations","Asian Cup","CONCACAF Gold Cup","UEFA Nations League"}
STAGE_WEIGHTS={"group":"1","qualifier":"1","playoff":"1.10","r16":"1.15","quarter":"1.30","semi":"1.50","final":"1.75","third":"1"}


def stage_category(name):
    """Used when preparing exact aliases; never match 'final' inside 'semi-final'."""
    value=(name or "").strip().casefold()
    patterns=[("final",r"(?:finals? - |league a - )?final"),("semi",r"(?:finals? - |league a - )?semi-finals?"),
        ("quarter",r"(?:finals? - |league a - )?quarter-finals?"),("r16",r"(?:round of 16|8th finals|last 16)"),
        ("third",r"(?:3rd place final|third place|3rd place|third-place play-off)"),
        ("group",r"(?:regular season|group stage|group [a-z]|league stage|league [a-d])(?: - \d+)?"),
        ("playoff",r"(?:play-offs|knockout round play-offs|round of 32|16th finals)"),
        ("qualifier",r"(?:(?:1st|2nd|3rd|first|second|third|preliminary)(?: qualifying)? round|preliminary round [12]|play-offs [a-c]/[b-d]|relegation play-out|qualification round(?: - \d+)?|qualifying round(?: - \d+)?|qualifying play-offs path [a-d] - (?:semi-finals|final)|(?:league [a-d] - )?(?:relegation|promotion|play-out|play-off|play-offs|final)(?:s)?(?: - .+)?|preliminary round - (?:semi-finals|final))")]
    for category,pattern in patterns:
        if re.fullmatch(pattern,value): return category
    raise ValidationError(f"Unconfigured provider stage: {name!r}")


def final_fixture(rows):
    finals=[row for row in rows if (row.get("league",{}).get("round") or "").strip().casefold() in ("final","finals - final","league a - final")]
    if len(finals)!=1 or finals[0]["fixture"]["status"]["short"] not in ("FT","AET","PEN"): return None
    return finals[0]


def retained(provider,resource,key,request_path,payload):
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return RawProviderPayload.objects.create(provider=provider,resource_type=resource,provider_resource_id=key,request_path=request_path,
        payload=payload,payload_sha256=hashlib.sha256(encoded).hexdigest(),http_status=200)


class ArchiveBackfill:
    def __init__(self,provider,budget=7000,reserve=500,interval=0.25,max_runtime=21600,first_year=2015,now=None,report=print):
        self.provider=provider; self.budget=budget; self.reserve=reserve; self.interval=interval
        self.now=now or django_timezone.now(); self.today=self.now.date(); self.current_year=award_year(self.today)
        self.first_year=first_year; self.deadline=clock.monotonic()+max_runtime; self.report=report
        self.state,_=ProviderSyncState.objects.get_or_create(provider=provider.provider_name,sync_key="archive-backfill")
        self.progress=dict(self.state.metadata); self.progress.setdefault("periods",{})
        self.progress.update(status="running",started_at=self.now.isoformat(),priority_order=self.priorities())
        self.descriptors=[]
        self.memberships=set(TeamCompetitionSeason.objects.values_list("team_id","competition_season_id"))
        self.profile_catalogue=None

    def priorities(self):
        years=list(range(self.current_year,self.first_year-1,-1))
        return list(dict.fromkeys([2024,self.current_year,self.current_year-1]+years))

    def checkpoint(self,**values):
        self.progress.update(values,calls_used=self.provider.requests_made,quota_remaining=self.provider.quota_remaining)
        self.state.metadata=self.progress; self.state.last_attempt_at=self.now
        self.state.save(update_fields=["metadata","last_attempt_at","updated_at"])

    def limits(self):
        self.provider.configure_request_limits(self.budget,self.interval)
        response=self.provider._request("status")["response"]
        requests=response["requests"]; daily=int(requests["limit_day"]); remaining=daily-int(requests["current"])
        reserve=min(self.reserve,max(10,daily//10))
        allowed=min(self.budget,max(0,remaining-reserve))
        if allowed<1: raise ProviderRequestLimitReached("Daily reserve reached; resume after the quota reset")
        self.provider.configure_request_limits(min(self.budget,self.provider.requests_made+allowed),max(self.interval,6.1 if daily<=100 else 0.25))
        self.provider.quota_reserve=reserve
        self.checkpoint(daily_limit=daily,reserve=reserve,effective_budget=self.provider.request_budget)

    def expired(self): return clock.monotonic()>=self.deadline

    def catalog(self):
        saved=RawProviderPayload.objects.filter(provider=self.provider.provider_name,resource_type="archive_catalog").order_by("-received_at","-pk").first()
        if saved and saved.received_at.date()==self.today: return saved.payload["response"]
        rows=self.provider._all("leagues")
        retained(self.provider.provider_name,"archive_catalog","all","/leagues",{"response":rows})
        return rows

    def manifest(self,league,edition):
        key=f"{league}:{edition['year']}"
        saved=RawProviderPayload.objects.filter(provider=self.provider.provider_name,resource_type="archive_manifest",provider_resource_id=key).order_by("-received_at","-pk").first()
        if saved:
            rows=saved.payload["response"]
            finished=all(row["fixture"]["status"]["short"] in ("FT","AET","PEN","CANC","ABD","WO","AWD") for row in rows)
            if saved.received_at.date()==self.today or (finished and date.fromisoformat(edition["end"])<self.today): return rows,saved.payload_sha256
        rows=self.provider._all("fixtures",{"league":league,"season":edition["year"]})
        saved=retained(self.provider.provider_name,"archive_manifest",key,f"/fixtures?league={league}&season={edition['year']}",{"response":rows})
        return rows,saved.payload_sha256

    def policy(self,family,aliases):
        performance={key:("1" if family=="domestic" else {"group":"1.05","qualifier":"1.05","playoff":"1.05","r16":"1.06","quarter":"1.07","semi":"1.08","final":"1.10","third":"1.05"}[key] if family=="ucl" else {"group":"1","qualifier":"1","playoff":"1","r16":"1.05","quarter":"1.08","semi":"1.10","final":"1.15","third":"1"}[key]) for key in STAGE_WEIGHTS}
        config={"stage_weights":STAGE_WEIGHTS,"stage_aliases":dict(sorted(aliases.items())),"performance_stage_weights":performance,"title_points":"3" if family=="domestic" else "5"}
        version={"domestic":"domestic-title-2.0","ucl":"ucl-title-2.0","national":"national-major-title-2.0"}[family]
        checksum=validate_campaign_policy(config)
        existing=CampaignPolicy.objects.filter(version=version).first()
        if existing:
            # Future schedules can add exact aliases already implied by the versioned categories.
            # Never change the persisted policy; missing aliases need a new version/formula.
            if any(existing.config["stage_aliases"].get(name)!=category for name,category in aliases.items()):
                raise ValidationError(f"New provider stages require a new immutable policy version: {version}")
            return existing
        return CampaignPolicy.objects.create(version=version,config=config,checksum_sha256=checksum)

    def prepare(self):
        aliases={family:{} for family in ("domestic","ucl","national")}
        unknown=set()
        for row in self.catalog():
            league=row["league"]; provider_id=str(league["id"])
            national=league["name"] in NATIONAL_NAMES
            if not national and provider_id not in CLUB_IDS: continue
            competition,_=Competition.objects.update_or_create(provider=self.provider.provider_name,provider_id=provider_id,defaults={
                "name":league["name"],"slug":slugify(league["name"]),"competition_type":"CUP" if national else "UCL" if provider_id=="2" else "DOMESTIC_LEAGUE",
                "format":"CUP" if national or provider_id=="2" else "LEAGUE","participant_type":"NATIONAL" if national else "CLUB",
                "scope":"GLOBAL" if national and league["name"]=="World Cup" else "CONTINENTAL" if national or provider_id=="2" else "DOMESTIC","is_tracked":True})
            family="national" if national else "ucl" if provider_id=="2" else "domestic"
            for edition in row.get("seasons",[]):
                if edition["year"]<self.first_year or edition["year"]>self.current_year: continue
                if not edition.get("coverage",{}).get("fixtures",{}).get("statistics_players"): continue
                if self.expired(): raise TimeoutError("Archive manifest preparation reached its time limit")
                rows,digest=self.manifest(provider_id,edition)
                final=final_fixture(rows) if family!="domestic" else None
                title_date=date.fromisoformat(final["fixture"]["date"][:10]) if final else date.fromisoformat(edition["end"])
                year=award_year(title_date) if national else edition["year"]
                season=ensure_award_period(year)
                cs,_=CompetitionSeason.objects.get_or_create(competition=competition,provider_season_id=f"{provider_id}:{edition['year']}",defaults={"season":season})
                # Only new, unpublished national edition assignments can change when a final is announced.
                if cs.season_id!=season.pk:
                    if hasattr(cs,"winning_campaign"): raise ValidationError("Verified edition award period cannot be reassigned")
                    cs.season=season
                cs.edition_name=f"{league['name']} {edition['year']}"; cs.starts_on=date.fromisoformat(edition["start"]); cs.ends_on=date.fromisoformat(edition["end"]); cs.save()
                for item in rows:
                    if item["fixture"]["status"]["short"] in ("CANC","ABD"): continue
                    name=(item.get("league",{}).get("round") or "").strip().casefold()
                    try: aliases[family][name]="group" if family=="domestic" else stage_category(name)
                    except ValidationError: unknown.add(name)
                self.descriptors.append({"cs":cs,"rows":rows,"family":family,"digest":digest,"final":final})
        if unknown: raise ValidationError(f"Unconfigured provider stages: {sorted(unknown)}")
        if set(str(item["cs"].competition.provider_id) for item in self.descriptors if item["family"]!="national")!=CLUB_IDS:
            raise ValidationError("Catalog did not provide all six required club competitions")
        policies={family:self.policy(family,names) for family,names in aliases.items()}
        for descriptor in self.descriptors:
            descriptor["policy"]=policies[descriptor["family"]]
            if descriptor["family"]=="national":
                descriptor["cs"].context_policy=descriptor["policy"]; descriptor["cs"].save(update_fields=["context_policy"])
        self.checkpoint(editions=len(self.descriptors),competitions=len({item["cs"].competition_id for item in self.descriptors}))

    def period_rows(self,year):
        output=[]
        for descriptor in self.descriptors:
            for row in descriptor["rows"]:
                if row["fixture"]["status"]["short"] not in ("FT","AET","PEN"): continue
                fixture=self.provider._normalize_fixture_meta(row)
                assigned=award_year(fixture.starts_at.date()) if descriptor["family"]=="national" else descriptor["cs"].season.starts_on.year
                if assigned==year and fixture.starts_at<=self.now: output.append((fixture,descriptor))
        return sorted(output,key=lambda item:(item[0].starts_at,item[0].id))

    def ingest_period(self,year):
        rows=self.period_rows(year)
        existing={row.provider_id:row for row in Fixture.objects.filter(provider=self.provider.provider_name,provider_id__in=[item.id for item,_ in rows]).annotate(playing_teams=Count("playerfixture__team_id",filter=Q(playerfixture__minutes__gt=0),distinct=True))}
        # Retained full manifests supply durations for older rows without extra player requests.
        for item,_ in rows:
            fixture=existing.get(item.id)
            if fixture is None: continue
            changes=[]
            if fixture.available_minutes is None and item.available_minutes is not None:
                fixture.available_minutes=item.available_minutes; changes.append("available_minutes")
            if fixture.stats_ingested_at and fixture.playing_teams!=2:
                fixture.stats_ingested_at=None; changes.append("stats_ingested_at")
            if changes: fixture.save(update_fields=changes)
        queue=[item for item in rows if item[0].id not in existing or (not existing[item[0].id].stats_ingested_at and (not existing[item[0].id].player_data_unavailable_at or existing[item[0].id].player_data_unavailable_at<self.now-timedelta(days=7)))]
        imported=0
        for offset in range(0,len(queue),20):
            if self.expired(): raise TimeoutError("Archive import reached its checkpoint time limit")
            batch=queue[offset:offset+20]
            bundles=self.provider.get_fixture_batch([fixture for fixture,_ in batch])
            mapping={fixture.id:descriptor for fixture,descriptor in batch}
            for bundle in bundles:
                descriptor=mapping[bundle.fixture.id]
                fixture=ingest_fixture_bundle(bundle,descriptor["cs"],self.provider.provider_name,"/fixtures?ids=batch")
                missing=[(team_id,descriptor["cs"].pk) for team_id in (fixture.home_team_id,fixture.away_team_id) if (team_id,descriptor["cs"].pk) not in self.memberships]
                if missing:
                    TeamCompetitionSeason.objects.bulk_create([TeamCompetitionSeason(team_id=team_id,competition_season_id=edition_id) for team_id,edition_id in missing],ignore_conflicts=True)
                    self.memberships.update(missing)
                if descriptor["family"]=="national" and (descriptor["cs"].competition.name!="UEFA Nations League" or stage_category(bundle.fixture.stage_name) in ("semi","final","third")):
                    fixture.neutral_venue=True; fixture.save(update_fields=["neutral_venue"])
                imported+=1
            self.checkpoint(period=f"{year}-{str(year+1)[-2:]}",batch_imported=imported,batch_remaining=max(0,len(queue)-offset-20))
            self.report(f"Archive {year}/{year+1}: imported {imported}; queued {max(0,len(queue)-offset-20)}",flush=True)
        fixtures=Fixture.objects.filter(provider=self.provider.provider_name,provider_id__in=[item.id for item,_ in rows])
        loaded=fixtures.filter(stats_ingested_at__isnull=False).count(); unavailable=fixtures.filter(player_data_unavailable_at__isnull=False).count()
        return {"expected":len(rows),"loaded":loaded,"unavailable":unavailable,"pending":len(rows)-loaded-unavailable,"imported":imported}

    def verify_winners(self,year):
        for descriptor in self.descriptors:
            cs=descriptor["cs"]
            if cs.season.starts_on.year!=year: continue
            rows=descriptor["rows"]; final=descriptor["final"]; winner_id=None; awarded=None; evidence=None
            review=reviewed_outcome(cs,rows)
            if review:
                winner_id=review.payload["winner"]
                awarded=datetime.fromisoformat(review.payload["awarded_at"])
                evidence=review_reference(review)
            elif descriptor["family"]=="domestic":
                if cs.ends_on>=self.today or not rows or any(row["fixture"]["status"]["short"] not in ("FT","AET","PEN","CANC","ABD","WO","AWD") for row in rows): continue
                standings_key=f"{cs.competition.provider_id}:{cs.provider_season_id.split(':')[1]}"
                saved=RawProviderPayload.objects.filter(provider=self.provider.provider_name,resource_type="archive_standings",provider_resource_id=standings_key).order_by("-pk").first()
                payload=saved.payload if saved else self.provider._request("standings",{"league":cs.competition.provider_id,"season":cs.provider_season_id.split(":")[1]})
                if not saved: saved=retained(self.provider.provider_name,"archive_standings",standings_key,"/standings",payload)
                groups=[group for result in payload.get("response",[]) for group in result.get("league",{}).get("standings",[])]
                if len(groups)!=1: continue
                leaders=[entry for entry in groups[0] if entry.get("rank")==1]
                if len(leaders)!=1: continue
                # Rank #1 is only championship evidence after a complete double round robin.
                required=2*(len(groups[0])-1)
                if required<2 or any(entry.get("all",{}).get("played")!=required for entry in groups[0]): continue
                winner_id=str(leaders[0]["team"]["id"])
                finished=[item for item in rows if item["fixture"]["status"]["short"] in ("FT","AET","PEN") and (item.get("league",{}).get("round") or "").strip().casefold().startswith("regular season")]
                if not finished: continue
                awarded=max(datetime.fromisoformat(item["fixture"]["date"].replace("Z","+00:00")) for item in finished)+timedelta(hours=3)
                evidence={"source":"final standings","payload_sha256":saved.payload_sha256,"manifest_sha256":descriptor["digest"]}
                administrative=[{"fixture":row["fixture"]["id"],"status":row["fixture"]["status"]["short"]}
                    for row in rows if row["fixture"]["status"]["short"] in ("WO","AWD")]
                if administrative: evidence["administrative_results"]=administrative
            elif final:
                winning=[str(team["id"]) for team in final["teams"].values() if team.get("winner") is True]
                if len(winning)!=1: continue
                winner_id=winning[0]; awarded=datetime.fromisoformat(final["fixture"]["date"].replace("Z","+00:00"))+timedelta(hours=3)
                evidence={"source":"completed title final","provider_fixture":final["fixture"]["id"],"manifest_sha256":descriptor["digest"]}
            if not winner_id or awarded>self.now: continue
            team=Team.objects.filter(provider=self.provider.provider_name,provider_id=winner_id).first()
            if not team: continue
            expected=winner_fixture_ids(rows,winner_id)
            campaign,_=WinningCampaign.objects.update_or_create(edition=cs,defaults={"winner":team,"policy":descriptor["policy"],"awarded_at":awarded,
                "verified_at":django_timezone.now(),"evidence":json.dumps(evidence,sort_keys=True),"expected_matches":len(expected),"is_complete":False})
            data=prepare_campaign_data(campaign)
            complete={row.provider_id for row in data["fixtures"]}==set(expected) and all(
                row.status=="FINISHED" and row.available_minutes and row.pk in data["has_players"]
                and (row.stats_ingested_at or row.pk in data["participation_evidence"]) for row in data["fixtures"])
            campaign.is_complete=bool(complete)
            campaign.save(update_fields=["is_complete","updated_at"])

    def formula(self):
        path=settings.BASE_DIR/"scoring_formulas"/"v1_6.json"
        with path.open(encoding="utf8") as handle: config=json.load(handle)
        checksum=validate_formula(config)
        formula,created=ScoringFormula.objects.get_or_create(version=config["version"],defaults={"name":config["name"],"config":config,"checksum_sha256":checksum,"notes":config.get("notes","")})
        if not created and formula.checksum_sha256!=checksum: raise ValidationError("Immutable formula v1.6 already has different rules")
        return formula

    def publish_period(self,year,summary):
        if summary["pending"] or not summary["loaded"]: return {"publication":"waiting_for_complete_player_data"}
        season=ensure_award_period(year)
        if len([item for item in self.descriptors if item["family"]!="national" and item["cs"].season_id==season.pk])!=6:
            return {"publication":"no_complete_six_competition_period"}
        latest=fixtures_for_award_period(season).filter(status="FINISHED").order_by("-starts_at").first()
        cutoff=min(self.now,datetime.combine(latest.starts_at.date(),time.max,tzinfo=timezone.utc))
        formula=self.formula()
        if formula.config.get("position_source") in ("api_football_profile","api_football_profile_with_reviewed_fallback"):
            if self.profile_catalogue is None:
                return {"publication":"waiting_for_verified_player_profiles"}
            self.profile_catalogue.apply_profiles()
        try: prepare_season_achievements(season,cutoff,formula.config["achievements"])
        except ValidationError as exc: return {"publication":"waiting_for_verified_trophies","reason":str(exc)}
        snapshot=RankingSnapshot.objects.filter(season=season,formula=formula,cutoff_at=cutoff,is_public=True).first()
        if not snapshot:
            try:
                rebuild_elo(season); recompute_scores(season,formula,cutoff); snapshot=publish(season,formula,cutoff,allow_unavailable=True)
            except (ValueError,ValidationError) as exc:
                return {"publication":"blocked_by_data_quality","reason":str(exc)}
        with transaction.atomic():
            ScoringFormula.objects.filter(is_active=True).exclude(pk=formula.pk).update(is_active=False)
            ScoringFormula.objects.filter(pk=formula.pk).update(is_active=True,activated_at=django_timezone.now())
            if year==self.current_year:
                Season=type(season)
                Season.objects.exclude(pk=season.pk).filter(is_current=True).update(is_current=False)
                Season.objects.filter(pk=season.pk).update(is_current=True)
        from django.core.cache import cache
        cache.clear()
        return {"publication":"published","snapshot":snapshot.pk,"formula":formula.version,"cutoff":cutoff.isoformat(),"coverage":snapshot.coverage_summary}

    def run(self,prepare_only=False):
        self.state.last_error=""; self.state.save(update_fields=["last_error","updated_at"])
        try:
            self.limits(); self.prepare()
            if prepare_only:
                self.checkpoint(status="prepared"); return self.progress
            self.profile_catalogue=ProfileCatalogue(self.provider,now=self.now,expired=self.expired,report=self.report)
            profiles=self.profile_catalogue.sync()
            self.checkpoint(profile_catalogue=profiles)
            for year in self.priorities():
                if year<self.first_year or year>self.current_year: continue
                summary=self.ingest_period(year)
                self.verify_winners(year)
                summary.update(self.publish_period(year,summary))
                self.progress["periods"][f"{year}-{str(year+1)[-2:]}"]=summary
                self.checkpoint()
                self.report(json.dumps({"period":year,**summary}),flush=True)
            # Titles spanning more than one award period can only be completed after older appearances arrive.
            for year in self.priorities():
                key=f"{year}-{str(year+1)[-2:]}"
                summary=self.progress["periods"].get(key)
                if not summary or self.expired(): continue
                self.verify_winners(year)
                summary.update(self.publish_period(year,summary))
                self.checkpoint()
            self.state.last_success_at=django_timezone.now(); self.state.save(update_fields=["last_success_at","updated_at"])
            gaps=any(item.get("pending") or item.get("unavailable") or item.get("publication")!="published" for item in self.progress["periods"].values())
            self.checkpoint(status="coverage_gaps" if gaps else "caught_up"); return self.progress
        except (ProviderRequestLimitReached,TimeoutError) as exc:
            self.checkpoint(status="checkpointed",pause_reason=str(exc)); return self.progress
        except Exception as exc:
            self.state.last_error=str(exc); self.state.save(update_fields=["last_error","updated_at"])
            self.checkpoint(status="failed"); raise
