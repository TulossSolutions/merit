import hashlib
import json
import re
from decimal import Decimal

VALID_AGGREGATIONS={"COUNT_PER90","RATE","NEGATIVE_COUNT_PER90","DERIVED_RATE"}
VALID_DIRECTIONS={"positive","negative"}
KNOWN_METRICS={"goals_per90","assists_per90","shots_on_target_per90","key_passes_per90","successful_dribbles_per90","duel_win_rate","goal_conversion_rate","accurate_passes_per90","pass_accuracy","interceptions_per90","tackles_per90","tackles_won_per90","aerial_duel_win_rate","clearances_per90","recoveries_per90","errors_leading_to_goal_per90","save_percentage","saves_per90","clean_sheet_rate","goals_conceded_per90","penalties_saved_per90","long_pass_accuracy","accurate_long_passes_per90"}
SEMVER=re.compile(r"^\d+\.\d+$")

def canonical_bytes(config): return json.dumps(config,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def validate_formula(config):
    from .achievements import validate_achievement_config
    validate_achievement_config(config.get("achievements"))
    if config.get("position_source","match") not in ("match","api_football_profile"):
        raise ValueError("Unsupported award position source")
    if not SEMVER.match(config.get("version","")): raise ValueError("Formula version must use major.minor format")
    if set(config.get("positions",{})) != {"GK","DEF","MID","FWD"}: raise ValueError("Formula requires exactly GK, DEF, MID and FWD")
    if not 0 <= Decimal(str(config.get("coverage_threshold"))) <= 1: raise ValueError("Invalid coverage threshold")
    if abs(Decimal(str(config["performance_weight"]))+Decimal(str(config["availability_weight"]))-1)>Decimal("1e-9"): raise ValueError("Final weights must sum to 1")
    for position,data in config["positions"].items():
        total=Decimal("0")
        for metric in data["metrics"]:
            weight=Decimal(str(metric["weight"])); total+=weight
            if metric.get("key") not in KNOWN_METRICS: raise ValueError(f"Unknown metric {metric.get('key')} in {position}")
            if weight<0 or metric["aggregation"] not in VALID_AGGREGATIONS or metric["direction"] not in VALID_DIRECTIONS: raise ValueError(f"Invalid metric in {position}")
        if abs(total-1)>Decimal("1e-9"): raise ValueError(f"Weights for {position} sum to {total}")
    return hashlib.sha256(canonical_bytes(config)).hexdigest()
