"""One-off, resumable weekly archive reconstruction. No provider access."""
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import time as clock

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone as django_timezone

from apps.football.award_periods import fixtures_for_award_period
from apps.football.models import Fixture, PlayerFixture, Season, WinningCampaign
from apps.ingestion.models import ProviderSyncState
from apps.ingestion.services.archive import CLUB_IDS
from apps.rankings.models import RankingSnapshot
from apps.scoring.models import ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula
from .publish import publish

FIRST_YEAR=2015
LAST_YEAR=2026
SYNC_KEY="weekly-ranking-replay"


def period_slug(year): return f"{year}-{str(year+1)[-2:]}"


def retrieval_gate(now):
    archive=ProviderSyncState.objects.filter(provider="api_football",sync_key="archive-backfill").first()
    if not archive or not archive.last_success_at or archive.last_error or archive.metadata.get("status") not in ("caught_up","coverage_gaps"):
        return "Awaiting a successful, completed archive retrieval run."
    for year in range(FIRST_YEAR,LAST_YEAR+1):
        slug=period_slug(year); summary=archive.metadata.get("periods",{}).get(slug,{})
        if summary.get("pending")!=0 or not summary.get("loaded") or not summary.get("expected"):
            return f"Awaiting completed fixture retrieval for {slug}."
        season=Season.objects.filter(slug=slug).first()
        if season is None: return f"Missing award period {slug}."
        clubs=set(season.competitionseason_set.filter(competition__provider="api_football",competition__is_tracked=True)
            .values_list("competition__provider_id",flat=True))
        if not CLUB_IDS.issubset(clubs): return f"Missing one of the six club editions for {slug}."
        if fixtures_for_award_period(season).filter(status=Fixture.Status.FINISHED,starts_at__lte=now,
            stats_ingested_at__isnull=True,player_data_unavailable_at__isnull=True).exists():
            return f"Fixture player data is still pending for {slug}."
    return None


def weekly_cutoffs(first, terminal):
    """Monday 06:00 UTC; include a final partial week at the retained-data boundary."""
    first=first.astimezone(timezone.utc); terminal=terminal.astimezone(timezone.utc)
    monday=datetime.combine(first.date()-timedelta(days=first.weekday()),time(6),tzinfo=timezone.utc)
    if monday<first: monday+=timedelta(days=7)
    result=[]
    while monday<=terminal:
        result.append({"cutoff":monday.isoformat(),"kind":"weekly"})
        monday+=timedelta(days=7)
    if not result or datetime.fromisoformat(result[-1]["cutoff"])!=terminal:
        result.append({"cutoff":terminal.isoformat(),"kind":"terminal"})
    return result


class NoEligibleWeek(Exception): pass


class WeeklyReplay:
    def __init__(self, max_runtime=1800, max_weeks=20, now=None, report=print):
        self.now=now or django_timezone.now(); self.deadline=clock.monotonic()+max_runtime
        self.max_weeks=max_weeks; self.report=report
        self.state,_=ProviderSyncState.objects.get_or_create(provider="api_football",sync_key=SYNC_KEY)
        self.progress=dict(self.state.metadata)

    def checkpoint(self, **values):
        self.progress.update(values,api_calls=0,updated_at=django_timezone.now().isoformat())
        periods=self.progress.get("periods",{})
        weeks=[week for period in periods.values() for week in period["weeks"]]
        self.progress["counts"]={"planned":len(weeks),"published":sum(w.get("status")=="published" for w in weeks),
            "existing":sum(w.get("status")=="existing" for w in weeks),"no_eligible_players":sum(w.get("status")=="no_eligible_players" for w in weeks),
            "blocked":sum(w.get("status")=="blocked" for w in weeks),"pending":sum(not w.get("status") for w in weeks)}
        self.state.metadata=self.progress
        self.state.save(update_fields=["metadata","last_attempt_at","last_success_at","last_error","updated_at"])

    def plan(self, formula):
        if self.progress.get("periods"):
            if self.progress["formula_checksum"]!=formula.checksum_sha256:
                raise ValidationError("Replay formula changed; the saved series must not silently change rules.")
            return
        periods={}
        # Retain the archive priority: 2024/25, current season, then newer to older.
        for year in dict.fromkeys([2024,LAST_YEAR,*range(LAST_YEAR-1,FIRST_YEAR-1,-1)]):
            season=Season.objects.get(slug=period_slug(year))
            fixtures=fixtures_for_award_period(season).filter(status=Fixture.Status.FINISHED,starts_at__lte=self.now)
            first=fixtures.order_by("starts_at").values_list("starts_at",flat=True).first()
            last=fixtures.order_by("-starts_at").values_list("starts_at",flat=True).first()
            if first is None: raise ValidationError(f"No completed fixtures for {season.slug}.")
            terminal=min(self.now,datetime.combine(last.astimezone(timezone.utc).date(),time.max,tzinfo=timezone.utc))
            old=RankingSnapshot.objects.filter(season=season,is_public=True,cutoff_at__lte=self.now).aggregate(last=Max("cutoff_at"))["last"]
            if old is not None: terminal=max(terminal,old)
            periods[season.slug]={"season_id":season.pk,"first_fixture":first.isoformat(),"terminal":terminal.isoformat(),
                "weeks":weekly_cutoffs(first,terminal)}
        self.progress.update(periods=periods,formula=formula.version,formula_checksum=formula.checksum_sha256,
            captured_at=self.now.isoformat(),cadence="Monday 06:00 UTC",first_year=FIRST_YEAR,last_year=LAST_YEAR)
        self.checkpoint(status="planned")

    def evidence_digest(self, season):
        fixtures=fixtures_for_award_period(season)
        evidence={"fixtures":fixtures.aggregate(count=Count("pk"),updated=Max("updated_at"),ingested=Max("stats_ingested_at")),
            "appearances":PlayerFixture.objects.filter(fixture__in=fixtures).aggregate(count=Count("pk"),updated=Max("updated_at")),
            "campaigns":list(WinningCampaign.objects.filter(edition__season=season).order_by("pk")
                .values("pk","winner_id","policy_id","awarded_at","verified_at","is_complete","expected_matches","evidence"))}
        return hashlib.sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()

    def run(self):
        self.state.last_attempt_at=self.now; self.state.last_error=""
        reason=retrieval_gate(self.now)
        if reason:
            self.checkpoint(status="waiting_for_retrieval",reason=reason)
            return self.progress
        try:
            formula=ScoringFormula.objects.get(version=self.progress.get("formula","1.5"))
            if validate_formula(formula.config)!=formula.checksum_sha256:
                raise ValidationError("Replay formula checksum does not match its immutable rules.")
            self.plan(formula); attempted=0
            self.checkpoint(status="running",reason="")
            for slug,period in self.progress["periods"].items():
                season=Season.objects.get(pk=period["season_id"])
                weeks=[week for week in period["weeks"] if week.get("status") not in ("published","existing","no_eligible_players")]
                if not weeks: continue
                # Some trophy-blocked seasons never reached the archive's Elo preparation.
                # Reuse retained context when complete; only fill a genuinely missing season.
                if fixtures_for_award_period(season).filter(status=Fixture.Status.FINISHED).annotate(n=Count("teamelosnapshot")).exclude(n=2).exists():
                    if clock.monotonic()>=self.deadline:
                        self.checkpoint(status="checkpointed"); return self.progress
                    rebuild_elo(season)
                digest=self.evidence_digest(season)
                for week in weeks:
                    if week.get("status")=="blocked" and week.get("evidence_digest")==digest: continue
                    if attempted>=self.max_weeks or clock.monotonic()>=self.deadline:
                        self.checkpoint(status="checkpointed"); return self.progress
                    cutoff=datetime.fromisoformat(week["cutoff"])
                    existing=RankingSnapshot.objects.filter(season=season,formula=formula,cutoff_at=cutoff,is_public=True).first()
                    if existing:
                        week.update(status="existing",snapshot=existing.pk)
                        self.checkpoint(); continue
                    attempted+=1; started=clock.monotonic()
                    self.checkpoint(current_period=slug,current_cutoff=week["cutoff"])
                    try:
                        with transaction.atomic():
                            scores=recompute_scores(season,formula,cutoff)
                            if not any(score.eligible and score.final_score is not None for score in scores):
                                raise NoEligibleWeek()
                            snapshot=publish(season,formula,cutoff,allow_unavailable=True,
                                weekly_replay={"kind":week["kind"],"cadence":self.progress["cadence"],"captured_at":self.progress["captured_at"]})
                    except NoEligibleWeek:
                        week.update(status="no_eligible_players")
                    except (ValueError,ValidationError) as exc:
                        week.update(status="blocked",reason=str(exc),evidence_digest=digest)
                    else:
                        week.update(status="published",snapshot=snapshot.pk,entries=snapshot.entries.count())
                    if week["status"]!="blocked":
                        week.pop("reason",None); week.pop("evidence_digest",None)
                    week["seconds"]=round(clock.monotonic()-started,2)
                    self.checkpoint()
                    self.report(f"Weekly replay {slug} {week['cutoff']}: {week['status']}"+(f" — {week['reason']}" if week.get("reason") else ""))
            self.state.last_success_at=django_timezone.now()
            self.checkpoint(status="pending_verification" if self.progress["counts"]["blocked"] else "complete",current_period=None,current_cutoff=None)
            return self.progress
        except Exception as exc:
            self.state.last_error=str(exc)
            self.checkpoint(status="failed")
            raise
