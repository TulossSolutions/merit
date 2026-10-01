import json

import pytest
from django.core.exceptions import ValidationError

from apps.ingestion.providers.enpelotas_export import merge_enpelotas_evidence, parse_enpelotas_export


def _player(player_id, name, minutes, stats):
    rows = "".join(f"<li><span>{label}</span><b>{value}</b></li>" for label, value in stats.items())
    return (
        '<div class="lu-item has-detail"><a class="lu-row" '
        f'href="/en/soccer/players/{name.lower()}-n-{player_id}"><span class="lu-meta"><b>{name}</b></span></a>'
        f'<div class="lu-detail"><span class="pd-min">{minutes}\' played</span><div class="pd-blk"><ul>{rows}</ul></div></div></div>'
    )


def _export_html(*, home_stats=None, away_stats=None, defending_totals=None):
    home_stats = home_stats or {}
    away_stats = away_stats or {}
    defending_totals = defending_totals or {"Duels won": (0, 0), "Interceptions": (0, 0)}
    event = {
        "@graph": [{
            "@type": "SportsEvent",
            "startDate": "2018-04-03T11:45:00-08:00",
            "homeTeam": {"name": "Home", "score": 1},
            "awayTeam": {"name": "Away", "score": 0},
        }]
    }
    total_rows = "".join(
        f'<div class="st-row"><span>{label}</span><b class="tnum">{values[0]}</b><b class="tnum">{values[1]}</b></div>'
        for label, values in defending_totals.items()
    )
    return (
        '<html><head><link rel="canonical" href="https://www.enpelotas.com/en/soccer/matches/home-vs-away-n-99">'
        f'<script type="application/ld+json">{json.dumps(event)}</script></head><body>'
        '<div class="card lu-card"><div class="lu-tname">Home</div>'
        f'{_player("11", "Home Player", 90, home_stats)}</div>'
        '<div class="card lu-card"><div class="lu-tname">Away</div>'
        f'{_player("22", "Away Player", 90, away_stats)}</div>'
        f'<div class="st-group"><div class="st-head"><h3>Defending</h3></div>{total_rows}</div>'
        '</body></html>'
    )


def _match():
    return {
        "id": "2021701",
        "kickOffTime": {"date": "2018-04-03"},
        "homeTeam": {"id": "1", "internationalName": "Home"},
        "awayTeam": {"id": "2", "internationalName": "Away"},
        "score": {"total": {"home": 1, "away": 0}},
    }


def test_parser_maps_metrics_and_treats_omitted_known_metrics_as_zero(tmp_path):
    path = tmp_path / "2021701.html"
    path.write_text(_export_html(
        home_stats={"Key passes": 2, "Interceptions": 1, "Duels won": 3, "Aerials won": 4},
        defending_totals={"Duels won": (3, 0), "Interceptions": (1, 0)},
    ), encoding="utf-8")

    evidence, raw = parse_enpelotas_export(path, _match())

    assert evidence["enpelotas_match_id"] == "99"
    assert evidence["verification"]["team_totals"] == {"duels": [3, 0], "interceptions": [1, 0]}
    home = {metric["key"]: metric["value"] for metric in evidence["participants"][0]["metrics"]}
    away = {metric["key"]: metric["value"] for metric in evidence["participants"][1]["metrics"]}
    assert home == {"key_passes": "2", "interceptions": "1", "duels": "3", "aerial_duels": "4"}
    assert away == {"key_passes": "0", "interceptions": "0", "duels": "0", "aerial_duels": "0"}
    assert raw["canonical_url"].endswith("-n-99")
    assert raw["html"].startswith("<html>")


def test_parser_rejects_team_total_mismatch(tmp_path):
    path = tmp_path / "2021701.html"
    path.write_text(_export_html(
        home_stats={"Duels won": 2},
        defending_totals={"Duels won": (9, 0), "Interceptions": (0, 0)},
    ), encoding="utf-8")

    with pytest.raises(ValidationError, match="does not reconcile duels"):
        parse_enpelotas_export(path, _match())


def test_merge_supplements_matched_players_and_retains_one_minute_gap():
    normalized = {
        "uefa_match_id": "2021701",
        "participants": [
            {"uefa_player_id": "1", "team_side": "home", "name": "Home Player", "minutes": 90,
             "metrics": [{"key": "goals", "value": "0"}]},
            {"uefa_player_id": "2", "team_side": "away", "name": "Away Player", "minutes": 90,
             "metrics": [{"key": "goals", "value": "0"}]},
            {"uefa_player_id": "3", "team_side": "away", "name": "One Minute", "minutes": 1,
             "metrics": [{"key": "goals", "value": "0"}]},
        ],
        "verification": {
            "covered_formula_sources": ["goals"],
            "missing_formula_sources": [
                "aerial_duels", "duels", "errors_leading_to_goal", "interceptions", "key_passes",
            ],
            "complete": False,
        },
    }
    supplemental = {
        "participants": [
            {"enpelotas_player_id": "11", "team_side": "home", "name": "Home Player", "minutes": 90,
             "metrics": [{"key": key, "value": "0"} for key in ("key_passes", "interceptions", "duels", "aerial_duels")]},
            {"enpelotas_player_id": "22", "team_side": "away", "name": "Away Player", "minutes": 89,
             "metrics": [{"key": key, "value": "0"} for key in ("key_passes", "interceptions", "duels", "aerial_duels")]},
        ],
        "verification": {"player_rows": 2, "team_totals": {}, "omitted_known_metrics_are_zero": True},
    }

    result = merge_enpelotas_evidence(normalized, supplemental)

    assert len(result["participants"][0]["metrics"]) == 5
    assert len(result["participants"][2]["metrics"]) == 1
    assert result["verification"]["missing_formula_sources"] == ["errors_leading_to_goal"]
    assert result["verification"]["partial_formula_sources"] == [
        "aerial_duels", "duels", "interceptions", "key_passes",
    ]
    assert result["verification"]["supplemental"]["missing_uefa_players"] == [{
        "uefa_player_id": "3", "name": "One Minute", "minutes": 1,
    }]
    assert result["verification"]["complete"] is False


def test_merge_rejects_missing_material_player():
    normalized = {
        "uefa_match_id": "2021701",
        "participants": [{
            "uefa_player_id": "1", "team_side": "home", "name": "Material Player", "minutes": 45,
            "metrics": [{"key": "goals", "value": "0"}],
        }],
        "verification": {"covered_formula_sources": [], "missing_formula_sources": [], "complete": False},
    }
    supplemental = {
        "participants": [],
        "verification": {"player_rows": 0, "team_totals": {}, "omitted_known_metrics_are_zero": True},
    }

    with pytest.raises(ValidationError, match="omits material UEFA player"):
        merge_enpelotas_evidence(normalized, supplemental)
