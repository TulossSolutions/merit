from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.core.cache import cache

from apps.football.award_periods import ensure_award_period
from apps.football.models import Competition, CompetitionSeason, Fixture, Player, PlayerFixture, PlayerFixtureMetric, Position
from apps.ingestion.models import ProviderSyncState
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.services.archive import CLUB_IDS
from apps.ingestion.services.profiles import ProfileCatalogue
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.rankings.services.publish import publish
from apps.rankings.services.queries import present_movement, previous_snapshot
from apps.rankings.services.weekly_replay import WeeklyReplay, retrieval_gate, weekly_cutoffs, FIRST_YEAR, LAST_YEAR, SYNC_KEY
from apps.scoring.models import PlayerSeasonScore
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.quality import check_quality
from test_profile_positions import period, formula, provider_for, profile_page

pytestmark=pytest.mark.django_db
NOW=datetime(2026,9,30,22,tzinfo=timezone.utc)


def archive_gate_data():
    summaries={}
    for year in range(FIRST_YEAR,LAST_YEAR+1):
        season=ensure_award_period(year)
        for pid in CLUB_IDS:
            comp,_=Competition.objects.get_or_create(provider="api_football",provider_id=pid,defaults={
                "name":f"League {pid}","slug":f"league-{pid}","competition_type":"DOMESTIC_LEAGUE","is_tracked":True})
            CompetitionSeason.objects.get_or_create(competition=comp,season=season)
        summaries[season.slug]={"expected":2,"loaded":1,"unavailable":1,"pending":0,"publication":"waiting_for_verified_trophies"}
    return ProviderSyncState.objects.create(provider="api_football",sync_key="archive-backfill",last_success_at=NOW,
        metadata={"status":"coverage_gaps","periods":summaries})


def replay_period():
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    config=formula("1.5")
    rebuild_elo(season)
    return season,config,cutoff


def saved_job(season,config,weeks,max_weeks=20):
    state,_=ProviderSyncState.objects.get_or_create(provider="api_football",sync_key=SYNC_KEY)
    state.metadata={"formula":config.version,"formula_checksum":config.checksum_sha256,"captured_at":NOW.isoformat(),
        "cadence":"Monday 06:00 UTC","periods":{season.slug:{"season_id":season.pk,"weeks":weeks}}}
    state.save()
    return WeeklyReplay(now=NOW,max_weeks=max_weeks,report=Mock())


def snap(season,config,cutoff,kind=None):
    return RankingSnapshot.objects.create(season=season,formula=config,cutoff_at=cutoff,published_at=NOW,is_public=True,
        coverage_summary={"weekly_replay":{"kind":kind}} if kind else {})


def entry(snapshot,player,rank=1,position=Position.FWD):
    return RankingEntry.objects.create(snapshot=snapshot,player=player,position=position,rank=rank,score=80,minutes=2000)


def test_fixed_12_period_gate_accepts_trophy_blocks_and_declared_provider_gaps():
    archive_gate_data()
    assert LAST_YEAR-FIRST_YEAR+1==12
    assert retrieval_gate(NOW) is None


@pytest.mark.parametrize("status",["running","failed","checkpointed","prepared"])
def test_gate_defers_until_completed_retrieval_run(status):
    state=archive_gate_data(); state.metadata["status"]=status; state.save()
    assert retrieval_gate(NOW) is not None
    result=WeeklyReplay(now=NOW).run()
    assert result["status"]=="waiting_for_retrieval" and result["api_calls"]==0
    assert not RankingSnapshot.objects.exists()


def test_gate_does_not_trust_stale_metadata_or_missing_editions():
    state=archive_gate_data(); state.metadata["periods"]["2017-18"]["pending"]=1; state.save()
    assert "2017-18" in retrieval_gate(NOW)
    state.metadata["periods"]["2017-18"]["pending"]=0; state.save()
    CompetitionSeason.objects.filter(season__slug="2018-19",competition__provider_id="39").delete()
    assert "2018-19" in retrieval_gate(NOW)


def test_gate_catches_real_pending_fixture_even_when_metadata_says_complete():
    season,_,_=replay_period(); archive_gate_data()
    Fixture.objects.filter(competition_season__season=season).update(stats_ingested_at=None,player_data_unavailable_at=None)
    assert "still pending" in retrieval_gate(NOW)
    Fixture.objects.filter(competition_season__season=season).update(player_data_unavailable_at=NOW)
    assert retrieval_gate(NOW) is None


def test_calendar_uses_monday_06_utc_and_keeps_summer_terminal():
    first=datetime(2024,8,10,12,tzinfo=timezone.utc)
    terminal=datetime(2025,7,6,23,59,59,999999,tzinfo=timezone.utc)
    rows=weekly_cutoffs(first,terminal)
    weekly=[datetime.fromisoformat(row["cutoff"]) for row in rows if row["kind"]=="weekly"]
    assert weekly[0]==datetime(2024,8,12,6,tzinfo=timezone.utc)
    assert all(row.weekday()==0 and row.hour==6 and row.utcoffset()==timedelta(0) for row in weekly)
    assert all(b-a==timedelta(days=7) for a,b in zip(weekly,weekly[1:]))
    assert rows[-1]=={"cutoff":terminal.isoformat(),"kind":"terminal"}
    assert len(weekly_cutoffs(first,weekly[0]))==1


def test_plan_is_fixed_across_restarts_and_all_12_years_include_actual_fixture_boundaries():
    archive_gate_data()
    config=formula("1.5")
    with patch("apps.rankings.services.weekly_replay.fixtures_for_award_period") as fixtures:
        # Supply captured boundaries for all 12 periods without importing any provider data.
        fixtures.return_value.filter.return_value.order_by.return_value.values_list.return_value.first.side_effect=[
            value for year in dict.fromkeys([2024,2026,*range(2025,2014,-1)])
            for value in (datetime(year,7,10,tzinfo=timezone.utc),min(NOW,datetime(year+1,7,6,tzinfo=timezone.utc)))]
        job=WeeklyReplay(now=NOW); job.plan(config)
    assert len(job.progress["periods"])==12 and list(job.progress["periods"])[:2]==["2024-25","2026-27"]
    assert job.progress["periods"]["2023-24"]["terminal"].startswith("2024-07-06")
    again=WeeklyReplay(now=NOW+timedelta(days=7)); again.plan(config)
    assert again.progress["periods"]==job.progress["periods"]


def test_weekly_replay_publishes_cumulative_real_scores_preserves_terminal_and_resumes_without_api():
    season,config,cutoff=replay_period()
    cutoff=datetime(2026,8,31,23,tzinfo=timezone.utc)
    recompute_scores(season,config,cutoff); terminal=publish(season,config,cutoff)
    frozen=list(terminal.entries.values()); snapshot_fields=list(RankingSnapshot.objects.filter(pk=terminal.pk).values())
    first=datetime(2026,8,10,6,tzinfo=timezone.utc)  # No played matches yet.
    second=datetime(2026,8,17,6,tzinfo=timezone.utc)
    third=datetime(2026,8,24,6,tzinfo=timezone.utc)
    weeks=[{"cutoff":value.isoformat(),"kind":"weekly"} for value in (first,second,third)]
    # Keep the fixture boundary at the original terminal date; a later weekly point remains a valid empty interval.
    job=saved_job(season,config,weeks,max_weeks=1)
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch.object(
        ApiFootballProvider,"_request",side_effect=AssertionError("Replay must never use API quota")):
        assert job.run()["status"]=="checkpointed"
        assert not PlayerSeasonScore.objects.filter(as_of=first).exists()
        result=WeeklyReplay(now=NOW,report=Mock()).run()
        assert result["status"]=="complete" and result["counts"]["published"]==2
        assert WeeklyReplay(now=NOW,report=Mock()).run()["counts"]==result["counts"]
    assert list(terminal.entries.values())==frozen
    assert list(RankingSnapshot.objects.filter(pk=terminal.pk).values())==snapshot_fields
    new=RankingSnapshot.objects.get(cutoff_at=second)
    assert set(new.entries.values_list("minutes",flat=True))=={180}
    assert not new.entries.filter(context_summary__achievements__campaigns__0__isnull=False).exists()
    assert set(RankingSnapshot.objects.get(cutoff_at=third).entries.values_list("movement",flat=True))=={0}


def test_week_scores_exclude_retained_future_matches_and_elo_is_prepared_once_when_missing():
    season,config,_=replay_period()
    later_fixture=Fixture.objects.order_by("-starts_at").first()
    # First eligible week sees two 90-minute matches, not a retained future match.
    from dataclasses import replace
    from apps.ingestion.models import RawProviderPayload
    from apps.ingestion.services.sync import ingest_fixture_bundle
    provider=provider_for({1:profile_page(1)})
    raw=RawProviderPayload.objects.filter(resource_type="fixture").first()
    bundle=provider.normalize_fixture(raw.payload)
    future=replace(bundle,fixture=replace(bundle.fixture,id="future-match",starts_at=datetime(2026,8,20,12,tzinfo=timezone.utc)))
    ingest_fixture_bundle(future,later_fixture.competition_season,"api_football")
    weeks=[{"cutoff":datetime(2026,8,day,6,tzinfo=timezone.utc).isoformat(),"kind":"weekly"} for day in (17,24)]
    job=saved_job(season,config,weeks)
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch(
        "apps.rankings.services.weekly_replay.rebuild_elo",wraps=rebuild_elo) as elo:
        result=job.run()
    assert elo.call_count==1 and result["counts"]["published"]==2
    assert set(RankingSnapshot.objects.get(cutoff_at=datetime(2026,8,17,6,tzinfo=timezone.utc)).entries.values_list("minutes",flat=True))=={180}
    assert set(RankingSnapshot.objects.get(cutoff_at=datetime(2026,8,24,6,tzinfo=timezone.utc)).entries.values_list("minutes",flat=True))=={270}


def test_early_week_unscored_cohort_does_not_block_another_eligible_cohort():
    season,config,_=replay_period()
    player=Player.objects.get(provider_id="20589")
    profile=player.position_profile; profile.position=Position.MID; profile.save()
    PlayerFixtureMetric.objects.filter(player_fixture__player=player).update(is_available=False)
    # Mbeumo has no supplied midfield metrics; Vinicius still has goals, and is eligible.
    cutoff=datetime(2026,8,17,6,tzinfo=timezone.utc)
    job=saved_job(season,config,[{"cutoff":cutoff.isoformat(),"kind":"weekly"}])
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None): result=job.run()
    assert result["status"]=="complete" and RankingSnapshot.objects.get().entries.count()==1
    score=PlayerSeasonScore.objects.get(player=player,as_of=cutoff)
    assert not score.eligible and score.performance_score is None and score.final_score is None
    assert not [issue for issue in check_quality(season,cutoff) if issue[0]=="ERROR"]


def test_trophy_blocked_weeks_rollback_and_other_weeks_publish_then_retry_changed_evidence():
    season,config,_=replay_period()
    cutoff=datetime(2026,8,17,6,tzinfo=timezone.utc)
    later=cutoff+timedelta(days=7)
    job=saved_job(season,config,[{"cutoff":value.isoformat(),"kind":"weekly"} for value in (cutoff,later)])
    original=recompute_scores
    def blocked(period,version,as_of):
        result=original(period,version,as_of)
        if as_of==later: raise ValidationError("Competition outcome or campaign is unverified: api_football:2")
        return result
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch(
        "apps.rankings.services.weekly_replay.recompute_scores",side_effect=blocked):
        result=job.run()
    assert result["status"]=="pending_verification" and result["counts"]["published"]==1 and result["counts"]["blocked"]==1
    assert "unverified" in result["periods"][season.slug]["weeks"][1]["reason"]
    assert not PlayerSeasonScore.objects.filter(as_of=later).exists()
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch(
        "apps.rankings.services.weekly_replay.recompute_scores",side_effect=AssertionError("Unchanged blocks must not be recomputed")):
        assert WeeklyReplay(now=NOW,report=Mock()).run()["status"]=="pending_verification"
    row=PlayerFixture.objects.first(); row.save()  # Updated local evidence permits retry.
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None):
        result=WeeklyReplay(now=NOW,report=Mock()).run()
    assert result["status"]=="complete" and result["counts"]["blocked"]==0
    assert "reason" not in result["periods"][season.slug]["weeks"][1]


def test_real_missing_title_evidence_remains_blocked_and_does_not_publish_future_credit():
    season,config,_=replay_period()
    # The configured club edition has finished, but has no verified winning campaign.
    CompetitionSeason.objects.filter(season=season,competition__provider_id="39").update(ends_on=datetime(2026,8,20).date())
    weeks=[{"cutoff":datetime(2026,8,day,6,tzinfo=timezone.utc).isoformat(),"kind":"weekly"} for day in (17,24)]
    job=saved_job(season,config,weeks)
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None): result=job.run()
    assert result["counts"]["published"]==1 and result["counts"]["blocked"]==1
    assert "Missing competition outcome" in result["periods"][season.slug]["weeks"][1]["reason"]
    assert RankingSnapshot.objects.count()==1 and PlayerSeasonScore.objects.count()==2


def test_existing_public_cutoff_is_reused_not_forced_or_recomputed():
    season,config,cutoff=replay_period(); recompute_scores(season,config,cutoff); snapshot=publish(season,config,cutoff)
    job=saved_job(season,config,[{"cutoff":cutoff.isoformat(),"kind":"terminal"}])
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch(
        "apps.rankings.services.weekly_replay.recompute_scores",side_effect=AssertionError("Do not rewrite existing results")):
        result=job.run()
    assert result["counts"]["existing"]==1 and RankingSnapshot.objects.get().pk==snapshot.pk


def test_runtime_pause_and_fatal_failure_leave_resumable_progress():
    season,config,cutoff=replay_period()
    job=saved_job(season,config,[{"cutoff":cutoff.isoformat(),"kind":"weekly"}]); job.deadline=0
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None):
        assert job.run()["status"]=="checkpointed"
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None),patch(
        "apps.rankings.services.weekly_replay.recompute_scores",side_effect=RuntimeError("test unexpected failure")):
        with pytest.raises(RuntimeError): WeeklyReplay(now=NOW,report=Mock()).run()
    state=ProviderSyncState.objects.get(sync_key=SYNC_KEY)
    assert state.metadata["status"]=="failed" and "unexpected failure" in state.last_error and not RankingSnapshot.objects.exists()


def test_command_shares_backfill_lock_and_rejects_invalid_limits():
    @contextmanager
    def busy(name):
        assert name=="season_backfill"
        yield False
    with patch("apps.rankings.management.commands.replay_weekly_rankings.application_lock",busy):
        call_command("replay_weekly_rankings")
    assert not ProviderSyncState.objects.exists()
    with pytest.raises(CommandError): call_command("replay_weekly_rankings",max_weeks=0)


def test_quality_checks_exact_cutoff_not_future_calculations_and_rejects_scored_invalid_weights():
    season,config,cutoff=replay_period(); scores=recompute_scores(season,config,cutoff)
    score=scores[0]
    invalid={"bad":{"active":True,"effective_weight":0.2}}
    PlayerSeasonScore.objects.create(season=season,formula=config,player=score.player,position=score.position,as_of=cutoff+timedelta(days=7),
        eligible=True,minutes=180,appearances=2,performance_score=80,final_score=80,metric_breakdown=invalid)
    assert not [issue for issue in check_quality(season,cutoff) if issue[0]=="ERROR"]
    score.metric_breakdown=invalid; score.save()
    assert any(issue[1]=="effective_weights_not_one" for issue in check_quality(season,cutoff))
    score.metric_breakdown={}; score.save()
    assert any(issue[1]=="effective_weights_not_one" for issue in check_quality(season,cutoff))


def test_quality_correction_keeps_raw_stat_and_elo_errors_fatal():
    season,config,cutoff=replay_period(); recompute_scores(season,config,cutoff)
    row=PlayerFixture.objects.first()
    PlayerFixtureMetric.objects.update_or_create(player_fixture=row,metric_key="inconsistent",defaults={"value":-1,"numerator":2,"denominator":1})
    row.fixture.teamelosnapshot_set.all().delete()
    codes={issue[1] for issue in check_quality(season,cutoff) if issue[0]=="ERROR"}
    assert {"negative_count_statistics","numerator_greater_than_denominator","finished_fixture_missing_elo"}<=codes


def test_display_movement_uses_persisted_prior_week_not_same_date_revisions_or_daily_baseline():
    season,config,cutoff=replay_period()
    player=Player.objects.first()
    weekly=snap(season,config,cutoff-timedelta(days=7),"weekly"); entry(weekly,player,rank=5)
    daily=snap(season,config,cutoff-timedelta(days=1)); entry(daily,player,rank=8)
    old_formula=formula("1.4"); same_date=snap(season,old_formula,cutoff); entry(same_date,player,rank=9)
    current=snap(season,config,cutoff); row=entry(current,player,rank=2)
    frozen=list(current.entries.values())
    assert previous_snapshot(current)==weekly
    with CaptureQueriesContext(connection) as queries:
        shown=present_movement([row],current)
    assert len(queries)<=2 and shown[0].display_movement==3 and row.movement_state=="existing"
    assert list(current.entries.values())==frozen and row.movement is None


@pytest.mark.parametrize("position,slug",[(Position.FWD,"attackers"),(Position.MID,"midfielders"),(Position.DEF,"defenders"),(Position.GK,"goalkeepers")])
def test_all_position_pages_home_player_and_compare_distinguish_no_baseline_from_new(client,position,slug):
    season,config,cutoff=replay_period(); season.is_published=True; season.save()
    player=Player.objects.first(); other=Player.objects.last()
    snapshot=snap(season,config,cutoff); row=entry(snapshot,player,position=position)
    cache.clear()
    for url in ("/",f"/rankings/{slug}/",f"/players/{player.slug}/"):
        response=client.get(url)
        assert response.status_code==200 and b'aria-label="No earlier ranking"' in response.content
        assert b'aria-label="New entry"' not in response.content
    assert client.get(f"/compare/?a={player.slug}").status_code==200
    prior=snap(season,config,cutoff-timedelta(days=7),"weekly"); entry(prior,other,position=position)
    assert present_movement([row],snapshot)[0].movement_state=="new_entry"
    cache.clear()
    assert b'aria-label="New entry"' in client.get(f"/rankings/{slug}/").content
    entry(prior,player,rank=2,position=Position.DEF if position!=Position.DEF else Position.MID)
    assert present_movement([row],snapshot)[0].movement_state=="new_category"
    cache.clear()
    assert b'aria-label="New award category"' in client.get(f"/rankings/{slug}/").content
    cache.clear()


def test_season_long_chart_prefers_real_weekly_series_keeps_terminal_and_bounds(client):
    season,config,cutoff=replay_period(); season.is_published=True; season.save()
    player=Player.objects.first()
    for number in range(52):
        snapshot=snap(season,config,cutoff+timedelta(weeks=number),"weekly")
        entry(snapshot,player,rank=1+number%15)
    daily=snap(season,config,cutoff+timedelta(days=1)); entry(daily,player,rank=30)
    terminal=snap(season,config,cutoff+timedelta(weeks=52),"terminal"); entry(terminal,player,rank=3)
    response=client.get(f"/players/{player.slug}/")
    points=response.context["chart_points"]
    assert len(points)==53 and all(72<=point["x"]<=960 and 40<=point["y"]<=260 for point in points)
    assert points[-1]["item"].snapshot_id==terminal.pk and all(point["item"].snapshot_id!=daily.pk for point in points)
    assert b'viewBox="0 0 1000 320"' in response.content
