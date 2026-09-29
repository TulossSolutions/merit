from datetime import date
from copy import deepcopy
from decimal import Decimal
import json
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from apps.football.models import PlayerFixture, Season
from apps.rankings.models import RankingSnapshot
from apps.rankings.services.queries import latest_snapshot
from apps.scoring.models import ScoringFormula
from apps.scoring.services.calculate import average_percentiles
from apps.scoring.services.context import league_factor, match_context, opponent_factor, stage_factor
from apps.scoring.services.elo import expected
from apps.scoring.services.formulas import validate_formula

pytestmark = pytest.mark.django_db

def test_season_thresholds_clamp():
    season = Season(name="2026/27", slug="x", starts_on=date(2026, 8, 1), ends_on=date(2027, 5, 31))
    assert season.progress(date(2020, 1, 1)) == 0
    assert season.progress(date(2030, 1, 1)) == 1
    assert season.eligibility_minutes(season.ends_on) == 1800
    assert season.availability_target_minutes(season.ends_on) == 2700

def test_fixture_minutes_validation():
    with pytest.raises(ValidationError): PlayerFixture(minutes=131).full_clean()

def test_context_clamps_and_stage():
    assert opponent_factor(1100) == Decimal("0.90")
    assert opponent_factor(1900) == Decimal("1.10")
    assert league_factor(1700, 1500) == Decimal("1.05")
    assert stage_factor("Final") == Decimal("1.10")
    assert Decimal("0.85") <= match_context(1100, .95, 1) <= Decimal("1.20")

def test_home_advantage_changes_expectation(): assert expected(Decimal(1500), Decimal(1500), True) > Decimal("0.5")

def test_average_tie_percentiles_are_deterministic():
    result = average_percentiles({2: Decimal(1), 1: Decimal(1), 3: Decimal(2)})
    assert result[1] == result[2] == Decimal("25")
    assert result[3] == Decimal("100")

def test_formula_weights_validate():
    with open("scoring_formulas/v1.json", encoding="utf8") as handle: config = json.load(handle)
    assert len(validate_formula(config)) == 64
    config["positions"]["FWD"]["metrics"][0]["weight"] = .31
    with pytest.raises(ValueError): validate_formula(config)

def test_15_percent_successor_preserves_published_v1_formula():
    with open("scoring_formulas/v1.json", encoding="utf8") as handle: old_config = json.load(handle)
    with open("scoring_formulas/v1_1.json", encoding="utf8") as handle: new_config = json.load(handle)
    assert new_config["version"] == "1.1"
    assert new_config["coverage_threshold"] == 0.15
    unchanged = new_config.copy()
    for key in ("version", "name", "notes", "coverage_threshold"):
        unchanged[key] = old_config[key]
    assert unchanged == old_config
    old = ScoringFormula.objects.create(version="1.0",name="Initial public model",config=old_config,checksum_sha256=validate_formula(old_config),is_active=True)
    season = Season.objects.create(name="2024/25",slug="2024-25",starts_on=date(2024,8,1),ends_on=date(2025,5,31))
    snapshot = RankingSnapshot.objects.create(season=season,formula=old,published_at=timezone.now(),cutoff_at=timezone.now(),is_public=True)
    with override_settings(FORMULA_VERSION="1.1"):
        call_command("import_scoring_formula","scoring_formulas/v1_1.json",verbosity=0)
    old.refresh_from_db()
    successor = ScoringFormula.objects.get(version="1.1")
    assert old.config == old_config and not old.is_active
    assert successor.is_active and successor.config == new_config
    assert successor.checksum_sha256 == validate_formula(new_config)
    assert RankingSnapshot.objects.get(pk=snapshot.pk).formula_id == old.pk

def test_v1_2_uses_api_football_tackles_without_changing_weights():
    with open("scoring_formulas/v1_1.json", encoding="utf8") as handle: old_config = json.load(handle)
    with open("scoring_formulas/v1_2.json", encoding="utf8") as handle: new_config = json.load(handle)
    assert new_config["version"] == "1.2"
    assert len(validate_formula(new_config)) == 64
    expected = deepcopy(old_config)
    expected.update(version=new_config["version"], name=new_config["name"], notes=new_config["notes"])
    for position in ("MID", "DEF"):
        old_metric = next(metric for metric in old_config["positions"][position]["metrics"] if metric["key"] == "tackles_won_per90")
        new_metric = next(metric for metric in new_config["positions"][position]["metrics"] if metric["key"] == "tackles_per90")
        assert new_metric["label"] == "Tackles / 90"
        assert new_metric["source"] == "tackles"
        assert new_metric["weight"] == old_metric["weight"]
        assert new_metric["aggregation"] == old_metric["aggregation"]
        assert new_metric["direction"] == old_metric["direction"]
        assert new_metric["context_adjust"] == old_metric["context_adjust"]
        index = next(index for index, metric in enumerate(expected["positions"][position]["metrics"]) if metric["key"] == "tackles_won_per90")
        expected["positions"][position]["metrics"][index] = new_metric
    assert new_config == expected
    old = ScoringFormula.objects.create(version="1.1", name="15% coverage model", config=old_config, checksum_sha256=validate_formula(old_config), is_active=True)
    season = Season.objects.create(name="2024/25",slug="tackles-formula",starts_on=date(2024,8,1),ends_on=date(2025,5,31))
    snapshot = RankingSnapshot.objects.create(season=season,formula=old,published_at=timezone.now(),cutoff_at=timezone.now(),is_public=True)
    with override_settings(FORMULA_VERSION="1.2"):
        call_command("import_scoring_formula","scoring_formulas/v1_2.json",verbosity=0)
    old.refresh_from_db()
    successor = ScoringFormula.objects.get(version="1.2")
    assert not old.is_active and successor.is_active
    assert RankingSnapshot.objects.get(pk=snapshot.pk).formula_id == old.pk

def test_latest_snapshot_prefers_new_publication_at_same_cutoff():
    with open("scoring_formulas/v1_1.json", encoding="utf8") as handle: old_config = json.load(handle)
    with open("scoring_formulas/v1_2.json", encoding="utf8") as handle: new_config = json.load(handle)
    season = Season.objects.create(name="2024/25", slug="same-cutoff", starts_on=date(2024,8,1), ends_on=date(2025,5,31), is_published=True)
    old = ScoringFormula.objects.create(version="1.1", name="V1.1", config=old_config, checksum_sha256=validate_formula(old_config))
    new = ScoringFormula.objects.create(version="1.2", name="V1.2", config=new_config, checksum_sha256=validate_formula(new_config), is_active=True)
    cutoff = timezone.now()
    RankingSnapshot.objects.create(season=season, formula=old, published_at=cutoff, cutoff_at=cutoff, is_public=True)
    newest = RankingSnapshot.objects.create(season=season, formula=new, published_at=cutoff, cutoff_at=cutoff, is_public=True)
    assert latest_snapshot(season).pk == newest.pk
