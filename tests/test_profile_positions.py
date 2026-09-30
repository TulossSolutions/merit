from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone as django_timezone

from apps.football.models import Competition, CompetitionSeason, Player, PlayerFixture, Position, Season
from apps.ingestion.models import PlayerPositionProfile, RawProviderPayload
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.ingestion.services.archive import ArchiveBackfill
from apps.ingestion.services.profiles import ProfileCatalogue
from apps.ingestion.services.sync import ingest_fixture_bundle, sync_reference
from apps.rankings.services.publish import publish
from apps.scoring.models import PlayerSeasonScore, ScoringFormula
from apps.scoring.services.calculate import recompute_scores, _aggregate
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula

pytestmark=pytest.mark.django_db


def profile_page(page, total=1, players=((762,"Attacker"),(20589,"Attacker"))):
    rows=[{"player":{"id":pid,"name":str(pid),"position":position}} for pid,position in players]
    return {"results":len(rows),"paging":{"current":page,"total":total},"response":rows}


def provider_for(pages):
    with override_settings(API_FOOTBALL_KEY="test"):
        provider=ApiFootballProvider(Mock())
    provider._request=Mock(side_effect=lambda path, params: pages[params["page"]])
    return provider


def period(provider, year=2026, positions=("M","F")):
    season=Season.objects.create(name=f"{year}/{str(year+1)[-2:]}",slug=f"{year}-{str(year+1)[-2:]}",
        starts_on=date(year,8,1),ends_on=date(year+1,5,31),is_current=year==2026)
    comp,_=Competition.objects.get_or_create(provider="api_football",provider_id="39",defaults={"name":"Premier League",
        "slug":"pl","competition_type":"DOMESTIC_LEAGUE","participant_type":"CLUB","is_tracked":True})
    edition=CompetitionSeason.objects.create(competition=comp,season=season,provider_season_id=f"39:{year}",ends_on=season.ends_on)
    # The unmodified trophy rules require configured club editions, even before any title is awarded.
    with open("scoring_formulas/v1_4.json",encoding="utf8") as handle:
        policies=json.load(handle)["achievements"]["policy_versions"]
    for key in policies:
        pid=key.partition(":")[2]
        if pid=="39": continue
        national=pid not in {"2","140","135","78","61"}
        other,_=Competition.objects.get_or_create(provider="api_football",provider_id=pid,defaults={"name":f"Competition {pid}",
            "slug":f"comp-{pid}","competition_type":"CUP" if national else "DOMESTIC_LEAGUE",
            "participant_type":"NATIONAL" if national else "CLUB","is_tracked":True})
        if not national:
            CompetitionSeason.objects.get_or_create(competition=other,season=season,
                defaults={"provider_season_id":f"{pid}:{year}","ends_on":season.ends_on})
    for day,role in zip((10,11),positions):
        metadata={"fixture":{"id":year*100+day,"date":f"{year}-08-{day}T12:00:00+00:00","status":{"short":"FT","elapsed":90}},
            "league":{"id":39,"season":year,"round":"Regular Season - 1"},
            "teams":{"home":{"id":10,"name":"Home"},"away":{"id":20,"name":"Away"}},"goals":{"home":1,"away":0}}
        rows=[{"team":{"id":team},"players":[{"player":{"id":pid,"name":name},
            "statistics":[{"games":{"position":role,"minutes":90},"goals":{"total":1 if team==10 else 0}}]}]}
            for team,pid,name in ((10,762,"Vinicius"),(20,20589,"Bryan Mbeumo"))]
        ingest_fixture_bundle(provider.normalize_fixture({"fixture":metadata,"players":{"response":rows}}),edition,"api_football")
    return season,datetime(year,8,13,23,tzinfo=timezone.utc)


def formula(version):
    with open(f"scoring_formulas/v{version.replace('.', '_')}.json",encoding="utf8") as handle:
        config=json.load(handle)
    return ScoringFormula.objects.create(version=version,name=config["name"],config=config,checksum_sha256=validate_formula(config))


@override_settings(API_FOOTBALL_KEY="test")
def test_profile_endpoint_uses_page_parameter_and_retains_full_response():
    payload=profile_page(2,3)
    client=Mock()
    client.get.return_value=Mock(status_code=200,headers={},json=lambda:payload,raise_for_status=Mock())
    provider=ApiFootballProvider(client)
    assert provider.get_profile_page(2)==payload
    assert client.get.call_args.args[0].endswith("/players/profiles")
    assert client.get.call_args.kwargs["params"]=={"page":2}
    assert provider.requests_made==1


@pytest.mark.parametrize("damage",["wrong_page","empty","count","identity","duplicates","position","size"])
def test_malformed_profile_pages_are_not_accepted(damage):
    payload=profile_page(1)
    if damage=="wrong_page": payload["paging"]["current"]=2
    if damage=="empty": payload["response"]=[]; payload["results"]=0
    if damage=="count": payload["results"]=9
    if damage=="identity": payload["response"][0]["player"]["id"]=None
    if damage=="duplicates": payload["response"][1]["player"]["id"]=762
    if damage=="position": payload["response"][0]["player"]["position"]=42
    if damage=="size": payload=profile_page(1,players=tuple((pid,"Attacker") for pid in range(1,252)))
    with pytest.raises(ValueError): provider_for({1:payload}).get_profile_page(1)


@override_settings(API_FOOTBALL_KEY="test")
def test_quota_headers_preserve_shared_reserve_and_invalid_page_never_requests():
    client=Mock()
    client.get.return_value=Mock(status_code=200,headers={"x-ratelimit-requests-remaining":"500"},
        json=lambda:profile_page(1,2),raise_for_status=Mock())
    provider=ApiFootballProvider(client)
    provider.quota_reserve=500
    provider.get_profile_page(1)
    with pytest.raises(ProviderRequestLimitReached): provider.get_profile_page(2)
    with pytest.raises(ValueError): provider.get_profile_page(0)
    assert client.get.call_count==1 and provider.requests_made==1


def test_catalogue_resumes_cached_pages_without_guessing_absent_players():
    provider=provider_for({1:profile_page(1,2,((762,"Attacker"),))})
    period(provider)
    provider._request.side_effect=[profile_page(1,2,((762,"Attacker"),)),ProviderRequestLimitReached("budget")]
    first=ProfileCatalogue(provider,report=Mock())
    with pytest.raises(ProviderRequestLimitReached): first.sync()
    assert first.state.metadata["next_page"]==2 and first.state.metadata["status"]=="checkpointed"
    assert PlayerPositionProfile.objects.count()==0  # A partial scan cannot establish absence.
    assert RawProviderPayload.objects.filter(resource_type="player_profile_page").count()==1
    second_provider=provider_for({2:profile_page(2,2,((20589,"Attacker"),(99999,"Defender")))})
    second=ProfileCatalogue(second_provider,report=Mock())
    assert second.sync()["applied"]==2
    assert second_provider._request.call_args.args==("players/profiles",{"page":2})
    assert RawProviderPayload.objects.filter(resource_type="player_profile_page").count()==2
    assert Player.objects.count()==2 and PlayerPositionProfile.objects.count()==2  # Not the whole global catalogue.
    for profile in PlayerPositionProfile.objects.select_related("player","source_payload"):
        assert profile.position==Position.FWD and profile.player.primary_position==Position.FWD
        encoded=json.dumps(profile.source_payload.payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        assert profile.source_payload.payload_sha256==hashlib.sha256(encoded).hexdigest()
    assert set(PlayerFixture.objects.values_list("position",flat=True))=={Position.MID,Position.FWD}


def test_cached_catalogue_applies_to_players_arriving_later_without_api_calls():
    provider=provider_for({1:profile_page(1)})
    first=ProfileCatalogue(provider,report=Mock())
    assert first.sync()["applied"]==0
    period(provider)
    provider._request.reset_mock()
    cached=ProfileCatalogue(provider,now=django_timezone.now()+timedelta(days=1),report=Mock())
    assert cached.sync()["applied"]==2
    provider._request.assert_not_called()
    assert cached.apply_profiles()["applied"]==0


def test_catalogue_refresh_is_cached_for_seven_days_and_retains_prior_evidence():
    provider=provider_for({1:profile_page(1)})
    period(provider)
    first=ProfileCatalogue(provider,report=Mock()); first.sync()
    old_payload=set(PlayerPositionProfile.objects.values_list("source_payload_id",flat=True))
    provider._request.side_effect=lambda path,params: profile_page(1,players=((762,"Midfielder"),(20589,"Attacker")))
    provider._request.reset_mock()
    later=django_timezone.now()+timedelta(days=8)
    with patch("apps.ingestion.services.profiles.timezone.now",return_value=later):
        refreshed=ProfileCatalogue(provider,now=later,report=Mock()); refreshed.sync()
    assert provider._request.call_count==1
    assert PlayerPositionProfile.objects.get(player__provider_id="762").position==Position.MID
    assert RawProviderPayload.objects.filter(pk__in=old_payload).count()==1
    assert RawProviderPayload.objects.filter(resource_type="player_profile_page").count()==2


@pytest.mark.parametrize("provider_position,expected",[("Goalkeeper",Position.GK),("Defender",Position.DEF),
    ("Midfielder",Position.MID),("Attacker",Position.FWD),(None,Position.UNKNOWN),("Unsupported",Position.UNKNOWN)])
def test_verified_profile_not_tactical_role_defines_category(provider_position,expected):
    provider=provider_for({1:profile_page(1,players=((762,provider_position),(20589,"Attacker")))})
    period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    profile=PlayerPositionProfile.objects.get(player__provider_id="762")
    assert profile.position==expected and profile.source_payload_id
    assert bool(profile.reason)==(expected==Position.UNKNOWN)


@pytest.mark.parametrize("year",[2024,2026])
def test_v1_4_aggregates_every_role_once_in_each_imported_season(year):
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider,year)
    ProfileCatalogue(provider,report=Mock()).sync()
    rebuild_elo(season)
    old_formula=formula("1.3")
    recompute_scores(season,old_formula,cutoff)
    legacy=PlayerSeasonScore.objects.filter(season=season,formula=old_formula,as_of=cutoff)
    assert all(score.position==Position.FWD and score.minutes==90 and not score.eligible for score in legacy)
    with CaptureQueriesContext(connection) as queries:
        scores=recompute_scores(season,formula("1.4"),cutoff)
    assert len(scores)==2 and all(score.position==Position.FWD and score.minutes==180 and score.appearances==2 and score.eligible for score in scores)
    assert all(score.context_summary["award_position"]["policy"]=="current_profile_all_imported_seasons" for score in scores)
    assert all(score.context_summary["award_position"]["payload_sha256"] for score in scores)
    assert set(PlayerFixture.objects.values_list("position",flat=True))=={Position.MID,Position.FWD}
    profile_queries=[item["sql"] for item in queries if '"ingestion_playerpositionprofile"' in item["sql"]]
    assert profile_queries and all('"ingestion_rawproviderpayload"."payload"' not in sql for sql in profile_queries)


def test_missing_profile_lookup_blocks_scoring_not_falls_back_to_match():
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider)
    with pytest.raises(ValidationError,match="profile lookup required"):
        recompute_scores(season,formula("1.4"),cutoff)


def test_absent_profile_is_reported_excluded_and_never_invented():
    provider=provider_for({1:profile_page(1,players=((762,"Attacker"),))})
    season,cutoff=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    unknown=PlayerPositionProfile.objects.get(player__provider_id="20589")
    assert unknown.position==Position.UNKNOWN and unknown.reason=="profile_not_found" and unknown.source_payload_id is None
    rebuild_elo(season)
    scoring_formula=formula("1.4")
    scores=recompute_scores(season,scoring_formula,cutoff)
    excluded=next(score for score in scores if score.player_id==unknown.player_id)
    assert excluded.minutes==180 and not excluded.eligible and excluded.final_score is None
    snapshot=publish(season,scoring_formula,cutoff)
    assert snapshot.entries.count()==1 and snapshot.coverage_summary["position_profiles"]["unavailable_players"]==1
    with patch.object(ApiFootballProvider,"_request") as request:
        response=Client().get(f"/rankings/attackers/?season={season.slug}")
    request.assert_not_called()
    assert response.status_code==200 and b"not assigned a guessed position" in response.content
    ingest_fixture_bundle(provider.normalize_fixture(RawProviderPayload.objects.filter(resource_type="fixture").first().payload),
        season.competitionseason_set.first(),"api_football",retain_raw=False)
    unknown.player.refresh_from_db()
    assert unknown.player.primary_position==Position.UNKNOWN


def test_profile_classification_does_not_rewrite_published_snapshots():
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider,positions=("F","F"))
    rebuild_elo(season)
    old_formula=formula("1.3"); recompute_scores(season,old_formula,cutoff)
    old=publish(season,old_formula,cutoff)
    frozen=list(old.entries.values())
    old_coverage=dict(old.coverage_summary)
    PlayerFixture.objects.filter(fixture__starts_at__day=10).update(position=Position.MID)
    ProfileCatalogue(provider,report=Mock()).sync()
    new_formula=formula("1.4"); recompute_scores(season,new_formula,cutoff)
    new=publish(season,new_formula,cutoff)
    assert new.pk!=old.pk and new.formula.version=="1.4"
    assert list(old.entries.values())==frozen
    old.refresh_from_db(); assert old.coverage_summary==old_coverage
    saved=list(new.entries.values())
    PlayerPositionProfile.objects.update(position=Position.MID)
    assert list(new.entries.values())==saved  # Snapshot freezes its source and resolved category.


def test_reference_sync_cannot_clobber_verified_profile_category():
    provider=provider_for({1:profile_page(1)})
    season,_=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    fixture_payload=RawProviderPayload.objects.filter(resource_type="fixture").first().payload
    bundle=provider.normalize_fixture(fixture_payload)
    provider.list_teams=Mock(return_value=[bundle.fixture.home_team])
    provider.list_players=Mock(return_value=[bundle.participations[0].player])
    sync_reference(provider,season)
    assert Player.objects.get(provider_id="762").primary_position==Position.FWD


def test_goalkeeper_clean_sheets_use_profile_category_without_rewriting_match_role():
    provider=provider_for({1:profile_page(1,players=((762,"Attacker"),(20589,"Goalkeeper")))})
    season,_=period(provider)
    rows=list(PlayerFixture.objects.filter(player__provider_id="762").select_related("fixture"))
    metric={"source":"clean_sheet","aggregation":"DERIVED_RATE"}
    assert _aggregate(rows,metric) is None
    assert _aggregate(rows,metric,Position.GK)==1
    assert _aggregate(rows,metric,Position.FWD) is None
    assert season.slug=="2026-27"


def test_v1_4_changes_only_category_policy_not_weights_or_trophy_rules():
    old=formula("1.3").config
    new=formula("1.4").config
    assert new["position_source"]=="api_football_profile"
    for config in (old,new):
        for key in ("name","version","notes","position_source"): config.pop(key,None)
    assert old==new


def test_archive_uses_v1_4_and_requires_complete_profile_catalogue():
    provider=provider_for({1:profile_page(1)})
    job=ArchiveBackfill(provider,report=Mock())
    assert job.formula().config["position_source"]=="api_football_profile"
    with pytest.raises(ValueError,match="incomplete"):
        ProfileCatalogue(provider,report=Mock()).apply_profiles()
    response=Client().get("/methodology/")
    assert response.status_code==200 and b"/players/profiles" in response.content


def test_archive_scans_profiles_before_ingestion_then_publishes_new_players_from_cache():
    provider=provider_for({1:profile_page(1)})
    job=ArchiveBackfill(provider,now=datetime(2026,9,30,tzinfo=timezone.utc),report=Mock())
    job.limits=Mock(); job.prepare=Mock(); job.verify_winners=Mock(); job.priorities=Mock(return_value=[2026])
    def ingest(year):
        assert job.profile_catalogue.state.metadata["status"]=="complete"
        season,_=period(provider,year)
        job.descriptors=[{"cs":cs,"family":"domestic"} for cs in season.competitionseason_set.filter(competition__participant_type="CLUB")]
        return {"loaded":2,"pending":0,"unavailable":0}
    job.ingest_period=ingest
    result=job.run()
    assert result["periods"]["2026-27"]["publication"]=="published"
    assert result["periods"]["2026-27"]["formula"]=="1.4"
    assert result["status"]=="caught_up" and provider._request.call_count==1
    assert PlayerPositionProfile.objects.count()==2
    assert ScoringFormula.objects.get(is_active=True).version=="1.4"


def test_known_profile_category_without_raw_evidence_is_not_accepted():
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    PlayerPositionProfile.objects.update(source_payload=None)
    with pytest.raises(ValidationError,match="payload evidence"):
        recompute_scores(season,formula("1.4"),cutoff)
