import json
import re
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError


METRIC_LABELS = {
    "Key passes": "key_passes",
    "Interceptions": "interceptions",
    "Duels won": "duels",
    "Aerials won": "aerial_duels",
}

PLAYER_NAME_ALIASES = {
    "danieldrinkwater": "dannydrinkwater",
    "geoffroysereydie": "sereydie",
    "konstantinosmanolas": "kostasmanolas",
    "lassdiarra": "lassanadiarra",
    "luisantoniovalencia": "antoniovalencia",
    "marcterstegen": "marcandreterstegen",
    "maximilianopereira": "maxipereira",
    "pedro": "pedrorodriguez",
    "sergiikryvtsov": "serhiykryvtsov",
    "sonheungmin": "heungminson",
    "yaroslavrakitskyi": "yaroslavrakitskyy",
}

TEAM_NAME_ALIASES = {
    "bayernmunchen": "fcbayernmunchen",
    "fcbasel": "basel",
    "mancity": "manchestercity",
    "manutd": "manchesterunited",
    "paris": "parissaintgermain",
    "porto": "fcporto",
    "shakhtar": "shakhtardonetsk",
    "tottenham": "tottenhamhotspur",
}


def _normal(value):
    folded = unicodedata.normalize("NFKD", value or "")
    return "".join(character for character in folded.casefold() if character.isalnum())


def _canonical_team(value):
    normalized = _normal(value)
    return TEAM_NAME_ALIASES.get(normalized, normalized)


def _integer(value, context):
    match = re.search(r"-?\d+", value or "")
    if match is None:
        raise ValidationError(f"EnPelotas export is missing numeric {context}")
    result = int(match.group())
    if result < 0:
        raise ValidationError(f"EnPelotas export has negative {context}")
    return result


def _sports_event(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        rows = payload.get("@graph", []) if isinstance(payload, dict) else []
        for row in rows:
            row_type = row.get("@type") if isinstance(row, dict) else None
            if row_type == "SportsEvent" or isinstance(row_type, list) and "SportsEvent" in row_type:
                return row
    raise ValidationError("EnPelotas export has no SportsEvent identity evidence")


def _summary_team_totals(soup):
    for group in soup.select(".st-group"):
        heading = group.select_one(".st-head h3")
        if heading is None or heading.get_text(" ", strip=True) != "Defending":
            continue
        totals = {}
        for row in group.select(".st-row"):
            label = row.select_one("span")
            values = row.select("b.tnum")
            if label is None or len(values) != 2:
                continue
            metric = METRIC_LABELS.get(label.get_text(" ", strip=True))
            if metric in {"duels", "interceptions"}:
                totals[metric] = [_integer(value.get_text(" ", strip=True), metric) for value in values]
        return totals
    raise ValidationError("EnPelotas export has no defending team totals")


def parse_enpelotas_export(path, match):
    path = Path(path)
    raw_html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "lxml")
    canonical = soup.select_one('link[rel="canonical"]')
    canonical_url = canonical.get("href") if canonical else ""
    source_match = re.search(r"-n-(\d+)$", canonical_url)
    if source_match is None:
        raise ValidationError(f"EnPelotas export {path.name} has no canonical match id")

    event = _sports_event(soup)
    expected_date = str((match.get("kickOffTime") or {}).get("date") or "")
    if str(event.get("startDate") or "")[:10] != expected_date:
        raise ValidationError(f"EnPelotas export {path.name} date does not match UEFA")

    score = (match.get("score") or {}).get("total") or (match.get("score") or {}).get("regular") or {}
    expected_teams = [match.get("homeTeam") or {}, match.get("awayTeam") or {}]
    event_teams = [event.get("homeTeam") or {}, event.get("awayTeam") or {}]
    for index, side in enumerate(("home", "away")):
        if _canonical_team(event_teams[index].get("name")) != _canonical_team(expected_teams[index].get("internationalName")):
            raise ValidationError(f"EnPelotas export {path.name} {side} team does not match UEFA")
        if int(event_teams[index].get("score")) != int(score.get(side)):
            raise ValidationError(f"EnPelotas export {path.name} {side} score does not match UEFA")

    cards = soup.select(".lu-card")
    if len(cards) != 2:
        raise ValidationError(f"EnPelotas export {path.name} does not contain exactly two team cards")
    participants = []
    team_sums = []
    seen_provider_players = set()
    for index, card in enumerate(cards):
        side = ("home", "away")[index]
        team_name = card.select_one(".lu-tname")
        if team_name is None or _canonical_team(team_name.get_text(" ", strip=True)) != _canonical_team(event_teams[index].get("name")):
            raise ValidationError(f"EnPelotas export {path.name} {side} lineup team does not match event identity")
        sums = {metric: 0 for metric in METRIC_LABELS.values()}
        for item in card.select(".lu-item.has-detail"):
            link = item.select_one('.lu-row[href*="/players/"]')
            minute = item.select_one(".pd-min")
            if link is None or minute is None:
                raise ValidationError(f"EnPelotas export {path.name} has an incomplete player detail row")
            player_id_match = re.search(r"-n-(\d+)$", link.get("href") or "")
            if player_id_match is None:
                raise ValidationError(f"EnPelotas export {path.name} has a player without a provider id")
            provider_player_id = player_id_match.group(1)
            if provider_player_id in seen_provider_players:
                raise ValidationError(f"EnPelotas export {path.name} duplicates player {provider_player_id}")
            seen_provider_players.add(provider_player_id)
            name_node = link.select_one(".lu-meta b")
            if name_node is None:
                raise ValidationError(f"EnPelotas export {path.name} has a player without a name")
            displayed = {}
            for row in item.select(".lu-detail .pd-blk li"):
                label = row.select_one("span")
                value = row.select_one("b")
                if label is not None and value is not None:
                    label_text = label.get_text(" ", strip=True)
                    if label_text in METRIC_LABELS:
                        displayed[label_text] = _integer(value.get_text(" ", strip=True), "player metric")
            metrics = []
            for label, metric in METRIC_LABELS.items():
                value = displayed.get(label, 0)
                sums[metric] += value
                metrics.append({
                    "key": metric,
                    "value": str(value),
                    "numerator": None,
                    "denominator": None,
                    "source_type_id": f"enpelotas_export:{source_match.group(1)}:{provider_player_id}:{metric}",
                })
            participants.append({
                "enpelotas_player_id": provider_player_id,
                "team_side": side,
                "name": name_node.get_text(" ", strip=True),
                "minutes": _integer(minute.get_text(" ", strip=True), "player minutes"),
                "metrics": metrics,
            })
        team_sums.append(sums)

    totals = _summary_team_totals(soup)
    for metric in ("duels", "interceptions"):
        actual = [team[metric] for team in team_sums]
        if totals.get(metric) != actual:
            raise ValidationError(
                f"EnPelotas export {path.name} does not reconcile {metric}: {actual} != {totals.get(metric)}"
            )
    evidence = {
        "enpelotas_match_id": source_match.group(1),
        "canonical_url": canonical_url,
        "date": expected_date,
        "home_team": event_teams[0].get("name"),
        "away_team": event_teams[1].get("name"),
        "home_score": int(score.get("home")),
        "away_score": int(score.get("away")),
        "participants": participants,
        "verification": {
            "player_rows": len(participants),
            "team_totals": totals,
            "omitted_known_metrics_are_zero": True,
        },
    }
    return evidence, {"canonical_url": canonical_url, "html": raw_html}


def merge_enpelotas_evidence(normalized, supplemental):
    official = {}
    for participant in normalized["participants"]:
        key = (participant["team_side"], _normal(participant["name"]))
        if key in official:
            raise ValidationError(f"UEFA evidence duplicates player identity {participant['name']}")
        official[key] = participant

    matched = set()
    for participant in supplemental["participants"]:
        name = _normal(participant["name"])
        name = PLAYER_NAME_ALIASES.get(name, name)
        key = (participant["team_side"], name)
        target = official.get(key)
        if target is None:
            raise ValidationError(
                f"EnPelotas player {participant['name']} has no unique UEFA identity in match {normalized['uefa_match_id']}"
            )
        if target["uefa_player_id"] in matched:
            raise ValidationError(f"EnPelotas evidence duplicates UEFA player {target['uefa_player_id']}")
        if abs(target["minutes"] - participant["minutes"]) > 1:
            raise ValidationError(
                f"EnPelotas player {participant['name']} minutes do not reconcile in match {normalized['uefa_match_id']}"
            )
        existing_keys = {metric["key"] for metric in target["metrics"]}
        if existing_keys & set(METRIC_LABELS.values()):
            raise ValidationError(f"Supplemental metrics already exist for UEFA player {target['uefa_player_id']}")
        target["metrics"].extend(participant["metrics"])
        target["enpelotas_player_id"] = participant["enpelotas_player_id"]
        matched.add(target["uefa_player_id"])

    missing = [
        participant
        for participant in normalized["participants"]
        if participant["uefa_player_id"] not in matched
    ]
    for participant in missing:
        if participant["minutes"] > 1 or any(metric["value"] != "0" for metric in participant["metrics"]):
            raise ValidationError(
                f"EnPelotas evidence omits material UEFA player {participant['name']} in match {normalized['uefa_match_id']}"
            )

    supplemental_sources = set(METRIC_LABELS.values())
    verification = normalized["verification"]
    verification["covered_formula_sources"] = sorted(set(verification["covered_formula_sources"]) | supplemental_sources)
    verification["missing_formula_sources"] = sorted(set(verification["missing_formula_sources"]) - supplemental_sources)
    verification["partial_formula_sources"] = sorted(supplemental_sources if missing else set())
    verification["supplemental"] = {
        **supplemental["verification"],
        "matched_player_rows": len(matched),
        "missing_uefa_players": [
            {
                "uefa_player_id": participant["uefa_player_id"],
                "name": participant["name"],
                "minutes": participant["minutes"],
            }
            for participant in missing
        ],
    }
    verification["complete"] = not verification["missing_formula_sources"] and not verification["partial_formula_sources"]
    return normalized
