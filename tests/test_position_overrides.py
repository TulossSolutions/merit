from datetime import timedelta
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock,patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command,CommandError
from django.test import Client,override_settings
from django.utils import timezone

from apps.football.models import Player,PlayerFixture,Position
from apps.ingestion.models import PlayerPositionProfile,ProviderSyncState,RawProviderPayload
from apps.ingestion.providers.api_football import ApiFootballProvider
from apps.ingestion.services.positions import award_positions
from apps.ingestion.services.profiles import ProfileCatalogue
from apps.ingestion.services.reviews import import_corrections
from apps.ingestion.services.sync import sync_reference
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.publish import publish
from apps.rankings.services.weekly_replay import WeeklyReplay,SYNC_KEY
from apps.scoring.models import PlayerSeasonScore,ScoringFormula
from apps.scoring.services.calculate import recompute_scores
from apps.scoring.services.elo import rebuild_elo
from apps.scoring.services.formulas import validate_formula
from test_profile_positions import formula,period,profile_page,provider_for,review_file

pytestmark=pytest.mark.django_db


def setup_period(year=2026):
    provider=provider_for({1:profile_page(1,players=((19617,"Midfielder"),(20589,"Attacker"),(762,"Attacker")))})
    season,cutoff=period(provider,year,player_ids=(19617,20589),names=("Michael Olise","Bryan Mbeumo"))
    ProfileCatalogue(provider,report=Mock()).sync()
    rebuild_elo(season)
    return provider,season,cutoff


def test_v1_6_changes_only_the_audited_olise_exception():
    old=formula("1.5").config;new=formula("1.6").config
    assert set(new["position_overrides"])=={"api_football:19617"}
    review=new["position_overrides"]["api_football:19617"]
    assert review["position"]==Position.FWD and review["reviewer"]=="project owner"
    assert hashlib.sha256(Path(review["source_path"]).read_text(encoding="utf8").encode()).hexdigest()==review["source_sha256"]
    for config in (old,new):
        for key in ("name","version","notes","position_overrides"):config.pop(key,None)
    assert old==new


@pytest.mark.parametrize("damage",["zero","bad_identity","unknown_position","reviewer","source_path","source_hash","no_audit","not_mapping","match_policy"])
def test_unaudited_or_invalid_overrides_are_rejected(damage):
    config=json.loads(Path("scoring_formulas/v1_6.json").read_text(encoding="utf8"))
    review=config["position_overrides"]["api_football:19617"]
    if damage=="zero":config["position_overrides"]={"api_football:0":review}
    elif damage=="bad_identity":config["position_overrides"]={"19617":review}
    elif damage=="unknown_position":review["position"]="UNKNOWN"
    elif damage=="reviewer":review["reviewer"]=""
    elif damage=="source_path":review["source_path"]="docs/../.env"
    elif damage=="source_hash":review["source_sha256"]="unverified"
    elif damage=="no_audit":review.pop("source_sha256")
    elif damage=="not_mapping":config["position_overrides"]=[]
    else:config["position_source"]="match"
    with pytest.raises(ValueError):validate_formula(config)


@pytest.mark.parametrize("year",[2021,2022,2023,2024,2026])
def test_override_all_seasons_preserves_profiles_roles_and_old_snapshots(year):
    provider,season,cutoff=setup_period(year)
    old_formula=formula("1.5");recompute_scores(season,old_formula,cutoff)
    old=publish(season,old_formula,cutoff);frozen=list(old.entries.values())
    profiles=list(PlayerPositionProfile.objects.order_by("pk").values())
    roles=list(PlayerFixture.objects.order_by("pk").values())
    new_formula=formula("1.6")
    with patch.object(ApiFootballProvider,"_request",side_effect=AssertionError("No API calls")):
        scores=recompute_scores(season,new_formula,cutoff);new=publish(season,new_formula,cutoff)
    olise=next(score for score in scores if score.player.provider_id=="19617")
    evidence=olise.context_summary["award_position"]
    assert olise.position==Position.FWD and olise.minutes==180 and olise.appearances==2
    assert evidence["source"]=="manual_position_override" and evidence["profile_position"]==Position.MID
    assert evidence["provider_position"]=="Midfielder" and evidence["profile_payload_sha256"]
    assert evidence["source_sha256"]==new_formula.config["position_overrides"]["api_football:19617"]["source_sha256"]
    assert new.coverage_summary["position_profiles"]["overridden_players"]==1
    assert list(old.entries.values())==frozen and list(PlayerPositionProfile.objects.order_by("pk").values())==profiles
    assert list(PlayerFixture.objects.order_by("pk").values())==roles
    assert award_positions([olise.player_id],reviewed=True)[olise.player_id]["position"]==Position.MID
    assert next(score for score in scores if score.player.provider_id=="20589").context_summary["award_position"]["source"]=="api_football.players/profiles.position"
    provider._request.assert_called_once()


def test_override_cannot_hide_missing_or_invalid_profile_evidence():
    _,season,cutoff=setup_period();config=formula("1.6")
    PlayerPositionProfile.objects.filter(player__provider_id="19617").update(source_payload=None)
    with pytest.raises(ValidationError,match="payload evidence"):recompute_scores(season,config,cutoff)
    PlayerPositionProfile.objects.filter(player__provider_id="19617").delete()
    with pytest.raises(ValidationError,match="profile lookup required"):recompute_scores(season,config,cutoff)


def test_active_override_survives_catalogue_reference_and_review_imports(tmp_path):
    provider,season,_=setup_period();config=formula("1.6")
    config.is_active=True;config.save()
    later=timezone.now()+timedelta(days=8)
    with patch("apps.ingestion.services.profiles.timezone.now",return_value=later):
        ProfileCatalogue(provider,now=later,report=Mock()).sync()
    olise=Player.objects.get(provider_id="19617")
    assert olise.primary_position==Position.FWD and olise.position_profile.position==Position.MID
    bundle=provider.normalize_fixture(RawProviderPayload.objects.filter(resource_type="fixture").first().payload)
    provider.list_teams=Mock(return_value=[bundle.fixture.home_team]);provider.list_players=Mock(return_value=[bundle.participations[0].player])
    sync_reference(provider,season)
    import_corrections(review_file(tmp_path,((19617,"Defender"),)))
    olise.refresh_from_db()
    assert olise.primary_position==Position.FWD and olise.position_profile.position==Position.MID
    assert award_positions([olise.pk],reviewed=True,overrides=config.config["position_overrides"])[olise.pk]["position"]==Position.FWD
    assert Player.objects.get(provider_id="20589").primary_position==Position.FWD


def test_correction_command_is_additive_idempotent_and_keeps_unaffected_seasons():
    provider,season,cutoff=setup_period()
    other,other_cutoff=period(provider,2022)
    ProfileCatalogue(provider,report=Mock()).sync();rebuild_elo(other)
    old_formula=formula("1.5")
    for target,as_of in ((season,cutoff),(other,other_cutoff)):
        recompute_scores(target,old_formula,as_of);publish(target,old_formula,as_of)
    frozen=list(RankingSnapshot.objects.order_by("pk").values())
    entries=[list(item.entries.values()) for item in RankingSnapshot.objects.order_by("pk")]
    warm=Client().get(f"/rankings/attackers/?season={season.slug}")
    assert warm.status_code==200 and b"Michael Olise" not in warm.content
    with patch.object(ApiFootballProvider,"_request",side_effect=AssertionError("No API calls")):
        call_command("publish_position_overrides");call_command("publish_position_overrides")
    assert RankingSnapshot.objects.count()==3
    new=RankingSnapshot.objects.get(formula__version="1.6")
    assert new.season==season and new.cutoff_at==cutoff
    assert new.entries.get(player__provider_id="19617").position==Position.FWD
    assert list(RankingSnapshot.objects.exclude(pk=new.pk).order_by("pk").values())==frozen
    assert [list(item.entries.values()) for item in RankingSnapshot.objects.exclude(pk=new.pk).order_by("pk")]==entries
    assert Player.objects.get(provider_id="19617").primary_position==Position.FWD
    assert ScoringFormula.objects.get(is_active=True).version=="1.6"
    response=Client().get(f"/players/michael-olise/?season={season.slug}")
    assert response.status_code==200 and response.context["entry"].position==Position.FWD
    refreshed=Client().get(f"/rankings/attackers/?season={season.slug}")
    assert refreshed.status_code==200 and b"Michael Olise" in refreshed.content
    assert Client().get("/methodology/").status_code==200


def test_command_republishes_cohort_even_when_olise_is_ineligible():
    _,season,cutoff=setup_period()
    row=PlayerFixture.objects.filter(player__provider_id="19617").first();row.minutes=89;row.save()
    old_formula=formula("1.5");recompute_scores(season,old_formula,cutoff);old=publish(season,old_formula,cutoff)
    assert not old.entries.filter(player__provider_id="19617").exists()
    call_command("publish_position_overrides")
    new=RankingSnapshot.objects.get(formula__version="1.6")
    assert new.season==season and new.coverage_summary["position_profiles"]["overridden_players"]==1
    assert not new.entries.filter(player__provider_id="19617").exists()
    assert PlayerSeasonScore.objects.get(formula=new.formula,player__provider_id="19617").position==Position.FWD


def test_publication_failure_rolls_back_formula_scores_position_and_snapshots():
    _,season,cutoff=setup_period();old_formula=formula("1.5")
    old_formula.is_active=True;old_formula.save()
    recompute_scores(season,old_formula,cutoff);old=publish(season,old_formula,cutoff)
    frozen=list(old.entries.values())
    with patch("apps.rankings.services.publish.check_quality",return_value=[("ERROR","missing_evidence",1)]):
        with pytest.raises(CommandError,match="blocked"):call_command("publish_position_overrides")
    assert RankingSnapshot.objects.count()==1 and list(old.entries.values())==frozen
    assert not ScoringFormula.objects.filter(version="1.6").exists()
    assert Player.objects.get(provider_id="19617").primary_position==Position.MID
    assert ScoringFormula.objects.get(is_active=True)==old_formula


def test_source_checksum_failure_cannot_apply_unverified_override(tmp_path):
    setup_period()
    (tmp_path/"scoring_formulas").mkdir();(tmp_path/"docs").mkdir()
    (tmp_path/"scoring_formulas/v1_6.json").write_text(Path("scoring_formulas/v1_6.json").read_text(encoding="utf8"),encoding="utf8")
    (tmp_path/"docs/position_overrides.md").write_text("Changed approval",encoding="utf8")
    with override_settings(BASE_DIR=tmp_path),pytest.raises(CommandError,match="source checksum mismatch"):
        call_command("publish_position_overrides")
    assert not ScoringFormula.objects.exists() and Player.objects.get(provider_id="19617").primary_position==Position.MID


def test_activation_preserves_pinned_v1_5_weekly_replay():
    _,season,cutoff=setup_period();old_formula=formula("1.5")
    recompute_scores(season,old_formula,cutoff);publish(season,old_formula,cutoff)
    call_command("publish_position_overrides")
    week_cutoff=cutoff-timedelta(days=1)
    ProviderSyncState.objects.create(provider="api_football",sync_key=SYNC_KEY,metadata={
        "formula":"1.5","formula_checksum":old_formula.checksum_sha256,"captured_at":cutoff.isoformat(),
        "cadence":"Monday 06:00 UTC","period_order":[season.slug],
        "periods":{season.slug:{"season_id":season.pk,"weeks":[{"cutoff":week_cutoff.isoformat(),"kind":"weekly"}]}}})
    with patch("apps.rankings.services.weekly_replay.retrieval_gate",return_value=None):
        result=WeeklyReplay(now=cutoff,max_weeks=1,report=Mock()).run()
    assert result["formula"]=="1.5" and result["counts"]["published"]==1
    old=RankingSnapshot.objects.get(formula=old_formula,cutoff_at=week_cutoff)
    assert old.entries.get(player__provider_id="19617").position==Position.MID
    assert ScoringFormula.objects.get(is_active=True).version=="1.6"
    assert Player.objects.get(provider_id="19617").primary_position==Position.FWD
