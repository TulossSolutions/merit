from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone as django_timezone

from apps.football.models import Competition, CompetitionSeason, Player, PlayerFixture, Position, Season
from apps.ingestion.models import PlayerPositionProfile, RawProviderPayload, ReviewedPlayerPosition, PlayerIdentityAlias
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.providers.base import ProviderRequestLimitReached
from apps.ingestion.services.archive import ArchiveBackfill
from apps.ingestion.services.profiles import ProfileCatalogue
from apps.ingestion.services.sync import ingest_fixture_bundle, sync_reference
from apps.ingestion.services.reviews import import_corrections, read_corrections, link_identity
from apps.ingestion.services.positions import award_positions
from apps.rankings.models import RankingSnapshot
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


@pytest.mark.parametrize("damage",["wrong_page","not_list","count","identity","duplicates","position","size"])
def test_malformed_profile_pages_are_not_accepted(damage):
    payload=profile_page(1)
    if damage=="wrong_page": payload["paging"]["current"]=2
    if damage=="not_list": payload["response"]={}; payload["results"]=0
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


def test_archive_uses_v1_5_and_requires_complete_profile_catalogue():
    provider=provider_for({1:profile_page(1)})
    job=ArchiveBackfill(provider,report=Mock())
    assert job.formula().config["position_source"]=="api_football_profile_with_reviewed_fallback"
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
    assert result["periods"]["2026-27"]["formula"]=="1.5"
    assert result["status"]=="caught_up" and provider._request.call_count==1
    assert PlayerPositionProfile.objects.count()==2
    assert ScoringFormula.objects.get(is_active=True).version=="1.5"


def test_known_profile_category_without_raw_evidence_is_not_accepted():
    provider=provider_for({1:profile_page(1)})
    season,cutoff=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    PlayerPositionProfile.objects.update(source_payload=None)
    with pytest.raises(ValidationError,match="payload evidence"):
        recompute_scores(season,formula("1.4"),cutoff)


def test_empty_tail_pages_are_retained_until_the_reported_end_not_treated_as_errors():
    provider=provider_for({1:profile_page(1,3),2:profile_page(2,3,()),3:profile_page(3,3,())})
    period(provider)
    catalogue=ProfileCatalogue(provider,report=Mock())
    assert catalogue.sync()["applied"]==2
    assert [call.args[1]["page"] for call in provider._request.call_args_list]==[1,2,3]
    assert RawProviderPayload.objects.filter(resource_type="player_profile_page").count()==3
    assert catalogue.state.metadata["status"]=="complete" and catalogue.state.metadata["profiles"]==2
    assert all(profile.position==Position.FWD for profile in PlayerPositionProfile.objects.all())


def test_entirely_empty_catalogue_cannot_mark_all_players_absent():
    provider=provider_for({1:profile_page(1,players=())})
    period(provider)
    catalogue=ProfileCatalogue(provider,report=Mock())
    with pytest.raises(ValueError,match="cannot establish player absence"):
        catalogue.sync()
    assert catalogue.state.metadata["status"]=="failed" and PlayerPositionProfile.objects.count()==0


def review_file(tmp_path, rows=((20589,"Attacker"),)):
    path=tmp_path/"review.md"
    path.write_text("\n".join(f"| {pid} | Player | Team | 180 | unavailable | {position} |" for pid,position in rows),encoding="utf8")
    return path


def reviewed_period():
    provider=provider_for({1:profile_page(1,players=((762,"Attacker"),(20589,None)))})
    season,cutoff=period(provider)
    ProfileCatalogue(provider,report=Mock()).sync()
    rebuild_elo(season)
    return provider,season,cutoff


def test_owner_correction_table_has_consistent_categories_and_excludes_id_zero():
    corrections,digest=read_corrections(Path("docs/unknown_profile_positions.md"))
    assert len(corrections)==68 and "0" not in corrections and corrections["90588"]==Position.FWD
    assert len(digest)==64 and set(corrections.values())=={Position.GK,Position.DEF,Position.MID,Position.FWD}


@pytest.mark.parametrize("rows",[
    ((20589,"Attacker"),(20589,"Defender")),((20589,"Striker"),),
])
def test_invalid_or_conflicting_correction_categories_are_rejected(tmp_path,rows):
    with pytest.raises(ValidationError): read_corrections(review_file(tmp_path,rows))


def test_incomplete_correction_row_is_rejected(tmp_path):
    path=tmp_path/"bad.md"; path.write_text("| 20589 | Player |",encoding="utf8")
    with pytest.raises(ValidationError,match="Incomplete"): read_corrections(path)


def test_manual_fallback_rejoins_category_without_altering_api_evidence_or_old_snapshots(tmp_path):
    provider,season,cutoff=reviewed_period()
    old_formula=formula("1.4"); recompute_scores(season,old_formula,cutoff)
    old=publish(season,old_formula,cutoff); frozen=list(old.entries.values()); coverage=dict(old.coverage_summary)
    before=PlayerPositionProfile.objects.get(player__provider_id="20589")
    result=import_corrections(review_file(tmp_path))
    assert result["created_reviews"]==1
    scoring_formula=formula("1.5"); scores=recompute_scores(season,scoring_formula,cutoff)
    restored=next(item for item in scores if item.player.provider_id=="20589")
    assert restored.eligible and restored.position==Position.FWD and restored.minutes==180
    evidence=restored.context_summary["award_position"]
    assert evidence["source"]=="manual_reviewed_position" and evidence["source_sha256"]==result["source_sha256"]
    assert evidence["profile_payload_sha256"]==before.source_payload.payload_sha256
    new=publish(season,scoring_formula,cutoff)
    assert new.entries.count()==2 and new.coverage_summary["position_profiles"]["unavailable_players"]==0
    assert new.coverage_summary["position_profiles"]["reviewed_players"]==1
    before.refresh_from_db(); assert before.position==Position.UNKNOWN and before.provider_position==""
    old.refresh_from_db(); assert old.coverage_summary==coverage and list(old.entries.values())==frozen
    new_frozen=list(new.entries.values())
    import_corrections(review_file(tmp_path,((20589,"Midfielder"),)))
    assert list(new.entries.values())==new_frozen
    assert set(PlayerFixture.objects.values_list("position",flat=True))=={Position.MID,Position.FWD}
    provider._request.reset_mock()
    assert Client().get(f"/players/bryan-mbeumo/?season={season.slug}").status_code==200
    provider._request.assert_not_called()


def test_known_api_position_always_wins_and_reviews_are_idempotent_immutable(tmp_path):
    provider=provider_for({1:profile_page(1)})
    period(provider); ProfileCatalogue(provider,report=Mock()).sync()
    path=review_file(tmp_path,((762,"Midfielder"),))
    assert import_corrections(path)["created_reviews"]==1
    assert import_corrections(path)["created_reviews"]==0
    player=Player.objects.get(provider_id="762")
    assert player.primary_position==Position.FWD
    assert award_positions([player.pk],reviewed=True)[player.pk]["source"]=="api_football.players/profiles.position"
    review=ReviewedPlayerPosition.objects.get(player=player)
    review.position=Position.DEF
    with pytest.raises(ValidationError,match="immutable"): review.save()


def test_catalogue_refresh_retains_review_and_new_known_profile_takes_priority(tmp_path):
    provider,_,_=reviewed_period(); import_corrections(review_file(tmp_path))
    later=django_timezone.now()+timedelta(days=8)
    with patch("apps.ingestion.services.profiles.timezone.now",return_value=later):
        ProfileCatalogue(provider,now=later,report=Mock()).sync()
    player=Player.objects.get(provider_id="20589")
    assert player.primary_position==Position.FWD and player.position_profile.position==Position.UNKNOWN
    provider._request.side_effect=lambda path,params:profile_page(1,players=((762,"Attacker"),(20589,"Midfielder")))
    latest=later+timedelta(days=8)
    with patch("apps.ingestion.services.profiles.timezone.now",return_value=latest):
        ProfileCatalogue(provider,now=latest,report=Mock()).sync()
    player.refresh_from_db()
    assert player.primary_position==Position.MID and ReviewedPlayerPosition.objects.filter(player=player).count()==1
    assert award_positions([player.pk],reviewed=True)[player.pk]["position"]==Position.MID


def test_reference_sync_preserves_manual_category(tmp_path):
    provider,season,_=reviewed_period(); import_corrections(review_file(tmp_path))
    bundle=provider.normalize_fixture(RawProviderPayload.objects.filter(resource_type="fixture").first().payload)
    provider.list_teams=Mock(return_value=[bundle.fixture.away_team])
    provider.list_players=Mock(return_value=[replace(bundle.participations[1].player,position="M")])
    sync_reference(provider,season)
    assert Player.objects.get(provider_id="20589").primary_position==Position.FWD


def test_identity_zero_stays_excluded_even_with_a_known_profile_and_manual_row(tmp_path):
    provider,season,cutoff=reviewed_period()
    player=Player.objects.get(provider_id="20589"); player.provider_id="0"; player.save()
    profile=player.position_profile; profile.position=Position.FWD; profile.reason=""; profile.save()
    path=review_file(tmp_path,((0,"Attacker"),(762,"Attacker")))
    import_corrections(path)
    score=next(item for item in recompute_scores(season,formula("1.5"),cutoff) if item.player_id==player.pk)
    assert score.position==Position.UNKNOWN and not score.eligible and score.final_score is None
    assert score.context_summary["award_position"]["reason"]=="invalid_provider_player_id"
    assert not ReviewedPlayerPosition.objects.filter(player=player).exists()


def alias_fixture(provider,season):
    raw=RawProviderPayload.objects.filter(resource_type="fixture").first()
    bundle=provider.normalize_fixture(raw.payload)
    old_row=replace(bundle.participations[1],player=replace(bundle.participations[1].player,id="90588"),minutes=15)
    historical=replace(bundle,fixture=replace(bundle.fixture,id="old-mbeumo"),
        participations=(bundle.participations[0],old_row),raw_payload={"original_player_id":90588})
    fixture=ingest_fixture_bundle(historical,season.competitionseason_set.get(competition__provider_id="39"),"api_football")
    return historical,fixture


def test_mbeumo_alias_moves_rows_preserves_evidence_and_replays_canonically(tmp_path):
    provider,season,_=reviewed_period(); bundle,fixture=alias_fixture(provider,season)
    old=Player.objects.get(provider_id="90588"); canonical=Player.objects.get(provider_id="20589")
    row=PlayerFixture.objects.get(player=old); row_id=row.pk; metrics=list(row.metrics.values())
    payload=RawProviderPayload.objects.get(provider_resource_id="old-mbeumo"); digest=payload.payload_sha256
    path=review_file(tmp_path,((90588,"Attacker"),))
    result=import_corrections(path,aliases=[("90588","20589")])
    assert result["moved_appearances"]==1 and ReviewedPlayerPosition.objects.get().player_id==canonical.pk
    row.refresh_from_db(); old.refresh_from_db(); payload.refresh_from_db()
    assert row.player_id==canonical.pk and row.pk==row_id and row.minutes==15 and list(row.metrics.values())==metrics
    assert not old.active and payload.payload_sha256==digest and payload.payload=={"original_player_id":90588}
    assert import_corrections(path,aliases=[("90588","20589")])["moved_appearances"]==0
    ingest_fixture_bundle(bundle,fixture.competition_season,"api_football",retain_raw=False)
    assert PlayerFixture.objects.get(fixture=fixture,player=canonical).pk==row_id
    assert not PlayerFixture.objects.filter(player=old).exists() and Player.objects.count()==3
    response=Client().get(f"/players/{old.slug}/?season=2017-18")
    assert response.status_code==301 and response["Location"]==f"/players/{canonical.slug}/?season=2017-18"
    search=Client().get("/players/search/?q=Bryan")
    assert search.status_code==200 and old.slug.encode() not in search.content and canonical.slug.encode() in search.content
    provider.list_teams=Mock(return_value=[bundle.fixture.away_team]); provider.list_players=Mock(return_value=[bundle.participations[1].player])
    sync_reference(provider,season)
    old.refresh_from_db(); assert not old.active and Player.objects.count()==3


def test_duplicate_canonical_fixture_input_is_rejected_without_writes(tmp_path):
    provider,season,_=reviewed_period(); bundle,fixture=alias_fixture(provider,season)
    import_corrections(review_file(tmp_path,((90588,"Attacker"),)),aliases=[("90588","20589")])
    duplicate=replace(bundle,participations=(*bundle.participations,replace(bundle.participations[1],
        player=replace(bundle.participations[1].player,id="20589"))))
    before=RawProviderPayload.objects.count(); rows=list(PlayerFixture.objects.filter(fixture=fixture).values())
    with pytest.raises(ValidationError,match="Duplicate canonical"):
        ingest_fixture_bundle(duplicate,fixture.competition_season,"api_football")
    assert RawProviderPayload.objects.count()==before and list(PlayerFixture.objects.filter(fixture=fixture).values())==rows


def test_overlapping_alias_fixtures_are_not_silently_merged(tmp_path):
    provider,season,_=reviewed_period()
    old=Player.objects.create(provider="api_football",provider_id="90588",name="Bryan Mbeumo")
    row=PlayerFixture.objects.filter(player__provider_id="20589").first()
    PlayerFixture.objects.create(player=old,fixture=row.fixture,team=row.team,opponent=row.opponent,position=Position.FWD,minutes=15)
    with pytest.raises(ValidationError,match="Overlapping"):
        import_corrections(review_file(tmp_path,((90588,"Attacker"),)),aliases=[("90588","20589")])
    assert not PlayerIdentityAlias.objects.exists() and not ReviewedPlayerPosition.objects.exists()
    assert PlayerFixture.objects.filter(player=old).count()==1


@pytest.mark.parametrize("old,canonical",[("0","20589"),("20589","20589")])
def test_invalid_identity_alias_is_rejected(old,canonical):
    with pytest.raises(ValidationError,match="Invalid"):
        link_identity(old,canonical,"docs/review.md","a"*64,"project owner")


def test_missing_player_blocks_import_without_partial_reviews(tmp_path):
    reviewed_period()
    with pytest.raises(ValidationError,match="not found"):
        import_corrections(review_file(tmp_path,((20589,"Attacker"),(999999,"Defender"))))
    assert not ReviewedPlayerPosition.objects.exists()


def test_v1_5_changes_only_position_fallback_policy():
    old=formula("1.4").config; new=formula("1.5").config
    assert new["position_source"]=="api_football_profile_with_reviewed_fallback"
    for config in (old,new):
        for key in ("version","name","notes","position_source"): config.pop(key,None)
    assert old==new


def test_publication_command_adds_corrected_snapshot_without_api_calls_or_rewriting_history(tmp_path):
    provider,season,cutoff=reviewed_period()
    old_formula=formula("1.4"); recompute_scores(season,old_formula,cutoff)
    old=publish(season,old_formula,cutoff); frozen=list(old.entries.values())
    call_command("import_reviewed_positions",str(review_file(tmp_path)))
    new_formula=formula("1.5")
    with patch.object(ApiFootballProvider,"_request",side_effect=AssertionError("No API calls permitted")):
        call_command("publish_reviewed_rankings")
        call_command("publish_reviewed_rankings")
    assert RankingSnapshot.objects.count()==2
    new=RankingSnapshot.objects.get(formula=new_formula)
    assert new.cutoff_at==old.cutoff_at and new.is_public and new.entries.count()==2
    assert list(old.entries.values())==frozen
    response=Client().get(f"/rankings/attackers/?season={season.slug}")
    assert response.status_code==200 and b"Bryan Mbeumo" in response.content


def test_publication_quality_failure_keeps_old_publication_and_rolls_back_new_scores(tmp_path):
    provider,season,cutoff=reviewed_period()
    old_formula=formula("1.4"); recompute_scores(season,old_formula,cutoff); old=publish(season,old_formula,cutoff)
    import_corrections(review_file(tmp_path)); new_formula=formula("1.5")
    with patch("apps.rankings.services.publish.check_quality",return_value=[("ERROR","test_missing_evidence",1)]):
        with pytest.raises(CommandError,match="blocked"):
            call_command("publish_reviewed_rankings")
    assert RankingSnapshot.objects.get().pk==old.pk and not PlayerSeasonScore.objects.filter(formula=new_formula).exists()
