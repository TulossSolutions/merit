import hashlib
import json
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.football.models import Fixture, Player, PlayerFixture, PlayerTeamSeason
from apps.ingestion.models import FixtureStatReconstruction, PlayerIdentityAlias, RawProviderPayload
from apps.ingestion.providers.enpelotas_export import merge_enpelotas_evidence, parse_enpelotas_export


RECONSTRUCTION_VERSION = "hybrid-2017-18-ucl-v1"
SOURCE_PROVIDER = "uefa_official"

# API-Football fixture id -> UEFA match id. The scope is deliberately fixed to
# the 2017/18 Champions League knockout phase approved for reconstruction.
FIXTURE_IDS = (
    ("152633", "2021683"), ("152635", "2021684"), ("152637", "2021687"),
    ("152639", "2021689"), ("152643", "2021685"), ("152641", "2021686"),
    ("152645", "2021688"), ("152647", "2021690"), ("152640", "2021695"),
    ("152638", "2021697"), ("152634", "2021693"), ("152636", "2021694"),
    ("152648", "2021696"), ("152646", "2021698"), ("152642", "2021692"),
    ("152644", "2021691"), ("152625", "2021700"), ("152627", "2021701"),
    ("152629", "2021699"), ("152631", "2021702"), ("152632", "2021704"),
    ("152630", "2021705"), ("152628", "2021703"), ("152626", "2021706"),
    ("152621", "2021707"), ("152623", "2021708"), ("152624", "2021709"),
    ("152622", "2021710"), ("152620", "2021711"),
)

# UEFA field -> Merit provider metric. Fields without a defensible equivalence
# stay absent; they are never imputed as zero.
METRIC_FIELDS = {
    "goals": "goals",
    "assists": "assists",
    "attempts": "shots",
    "attempts_on_target": "shots_on_target",
    "dribbling": "successful_dribbles",
    "passes_attempted": "passes",
    "passes_completed": "accurate_passes",
    "tackles": "tackles",
    "clearance_completed": "clearances",
    "recovered_ball": "recoveries",
    "goals_conceded": "goals_conceded",
    "saves": "saves",
    "saves_on_penalty": "penalties_saved",
    "passes_long_attempted": "long_passes",
    "passes_long_completed": "accurate_long_passes",
}

FORMULA_SOURCES = {
    "goals", "assists", "shots", "shots_on_target", "key_passes",
    "successful_dribbles", "duels", "accurate_passes", "passes",
    "interceptions", "tackles", "aerial_duels", "clearances", "recoveries",
    "errors_leading_to_goal", "saves", "clean_sheet", "goals_conceded",
    "penalties_saved", "long_passes", "accurate_long_passes",
}

TEAM_TOTAL_FIELDS = {
    "goals": "goals_scored",
    "assists": "assists",
    "attempts": "attempts",
    "attempts_on_target": "attempts_on_target",
    "dribbling": "dribbling",
    "passes_attempted": "passes_attempted",
    "passes_completed": "passes_completed",
    "tackles": "tackles",
    "clearance_completed": "clearance_completed",
    "recovered_ball": "recovered_ball",
    "goals_conceded": "goals_conceded",
    "saves": "saves",
    "saves_on_penalty": "saves_on_penalty",
    "passes_long_attempted": "passes_long_attempted",
    "passes_long_completed": "passes_long_completed",
    "minutes_played_official": "minutes_played_official",
}


def _decimal(value):
    try:
        return Decimal(str(value)) if value is not None and value != "" else None
    except (InvalidOperation, TypeError):
        return None


def _stat_map(row):
    result = {}
    for item in row.get("statistics") or []:
        name = item.get("name")
        if name:
            result[name] = _decimal(item.get("value"))
    return result


def _json_decimal(value):
    return str(value) if value is not None else None


def _metric_rows(stats, match_id, player_id):
    metrics = []
    for source_field, merit_key in METRIC_FIELDS.items():
        value = stats.get(source_field)
        if value is None:
            continue
        numerator = denominator = None
        if merit_key == "shots":
            goals = stats.get("goals")
            if goals is not None and goals <= value:
                numerator, denominator = goals, value
        elif merit_key == "passes":
            numerator, denominator = stats.get("passes_completed"), value
        elif merit_key == "saves":
            conceded = stats.get("goals_conceded")
            numerator = value
            denominator = value + conceded if conceded is not None else None
        elif merit_key == "long_passes":
            numerator, denominator = stats.get("passes_long_completed"), value
        metrics.append({
            "key": merit_key,
            "value": _json_decimal(value),
            "numerator": _json_decimal(numerator),
            "denominator": _json_decimal(denominator),
            "source_type_id": f"uefa_official:{match_id}:{source_field}",
        })
    return metrics


def _lineup_index(lineups):
    players = {}
    for side in ("homeTeam", "awayTeam"):
        team = lineups.get(side) or {}
        team_id = str((team.get("team") or {}).get("id") or "")
        if not team_id:
            raise ValidationError(f"UEFA lineup is missing {side} identity")
        for started, collection in ((True, team.get("field") or []), (False, team.get("bench") or [])):
            for item in collection:
                profile = item.get("player") or {}
                player_id = str(profile.get("id") or "")
                if player_id:
                    players[player_id] = {
                        "team_id": team_id,
                        "started": started,
                        "jersey_number": item.get("jerseyNumber"),
                        "profile": profile,
                    }
    return players


def normalize_official_evidence(match, evidence):
    match_id = str(match["id"])
    lineups = evidence["lineups"]
    if str(lineups.get("matchId")) != match_id:
        raise ValidationError(f"UEFA lineup identity mismatch for {match_id}")
    home_id = str((match.get("homeTeam") or {}).get("id") or "")
    away_id = str((match.get("awayTeam") or {}).get("id") or "")
    if not home_id or not away_id or home_id == away_id:
        raise ValidationError(f"UEFA match {match_id} has invalid teams")
    sides = {home_id: "home", away_id: "away"}
    lineups_by_player = _lineup_index(lineups)
    supplemental_profiles = {
        str(profile.get("id")): profile
        for profile in evidence.get("player_profiles") or []
        if profile.get("id") is not None
    }
    raw_player_rows = evidence["player_statistics"]
    if not isinstance(raw_player_rows, list):
        raise ValidationError(f"UEFA player statistics are invalid for {match_id}")

    participants = []
    raw_by_team = defaultdict(list)
    starters = defaultdict(int)
    used_lineup_players = set()
    for raw in raw_player_rows:
        player_id = str(raw.get("playerId") or "")
        team_id = str(raw.get("teamId") or "")
        lineup = lineups_by_player.get(player_id)
        if lineup is None:
            profile = supplemental_profiles.get(player_id)
            if profile is None:
                raise ValidationError(f"UEFA player {player_id} is absent from match {match_id} lineup evidence")
            name = _normal_name(profile.get("internationalName"))
            birth_date = profile.get("birthDate")
            candidates = [
                candidate
                for candidate in lineups_by_player.values()
                if (not team_id or candidate["team_id"] == team_id)
                and (candidate["profile"].get("birthDate") == birth_date or _normal_name(candidate["profile"].get("internationalName")) == name)
            ]
            if len(candidates) != 1:
                raise ValidationError(f"UEFA player {player_id} has no unique historical lineup identity in match {match_id}")
            lineup = candidates[0]
            if not team_id:
                team_id = lineup["team_id"]
        lineup_identity = str((lineup["profile"] or {}).get("id") or "")
        if lineup_identity in used_lineup_players:
            raise ValidationError(f"UEFA lineup identity {lineup_identity} is duplicated in match {match_id}")
        used_lineup_players.add(lineup_identity)
        if team_id not in sides or lineup["team_id"] != team_id:
            raise ValidationError(f"UEFA player {player_id} has inconsistent team evidence in match {match_id}")
        stats = _stat_map(raw)
        minutes_value = stats.get("minutes_played_official")
        if minutes_value is None or minutes_value < 0 or minutes_value > 130 or minutes_value != minutes_value.to_integral_value():
            raise ValidationError(f"UEFA player {player_id} has invalid minutes in match {match_id}")
        minutes = int(minutes_value)
        if minutes == 0:
            continue
        profile = supplemental_profiles.get(player_id) or lineup["profile"]
        if lineup["started"]:
            starters[team_id] += 1
        raw_by_team[team_id].append(stats)
        participants.append({
            "uefa_player_id": player_id,
            "team_side": sides[team_id],
            "name": profile.get("internationalName") or str(((profile.get("translations") or {}).get("name") or {}).get("EN") or player_id),
            "birth_date": profile.get("birthDate"),
            "uefa_position": profile.get("fieldPosition") or profile.get("detailedFieldPosition"),
            "started": lineup["started"],
            "minutes": minutes,
            "jersey_number": lineup.get("jersey_number"),
            "metrics": _metric_rows(stats, match_id, player_id),
        })
    if starters != {home_id: 11, away_id: 11}:
        raise ValidationError(f"UEFA match {match_id} does not reconcile to eleven starters per team")

    team_rows = evidence["team_statistics"]
    team_stats = {str(row.get("teamId")): _stat_map(row) for row in team_rows}
    if set(team_stats) != {home_id, away_id}:
        raise ValidationError(f"UEFA team statistics do not match the teams in {match_id}")
    reconciled = {}
    for team_id in (home_id, away_id):
        checks = {}
        for player_field, team_field in TEAM_TOTAL_FIELDS.items():
            expected = team_stats[team_id].get(team_field)
            if expected is None:
                raise ValidationError(f"UEFA match {match_id} is missing team field {team_field}")
            actual = sum((row.get(player_field) or Decimal("0") for row in raw_by_team[team_id]), Decimal("0"))
            if actual != expected:
                raise ValidationError(
                    f"UEFA match {match_id} team {team_id} does not reconcile {player_field}: {actual} != {expected}"
                )
            checks[player_field] = _json_decimal(expected)
        reconciled[sides[team_id]] = checks

    score = match.get("score") or {}
    score = score.get("total") or score.get("regular") or {}
    for team_id, key in ((home_id, "home"), (away_id, "away")):
        official_score = _decimal(score.get(key))
        team_goals = team_stats[team_id].get("goals")
        if official_score is None or team_goals != official_score:
            raise ValidationError(f"UEFA match {match_id} final score does not reconcile")

    covered = set(METRIC_FIELDS.values()) | {"clean_sheet"}
    missing = sorted(FORMULA_SOURCES - covered)
    return {
        "uefa_match_id": match_id,
        "date": str(((match.get("kickOffTime") or {}).get("date") or "")),
        "home_team": (match.get("homeTeam") or {}).get("internationalName"),
        "away_team": (match.get("awayTeam") or {}).get("internationalName"),
        "home_score": int(_decimal(score.get("home"))),
        "away_score": int(_decimal(score.get("away"))),
        "participants": participants,
        "verification": {
            "player_rows": len(participants),
            "team_totals": reconciled,
            "covered_formula_sources": sorted(covered),
            "missing_formula_sources": missing,
            "complete": not missing,
        },
    }


def _normal_name(value):
    folded = unicodedata.normalize("NFKD", value or "")
    return "".join(character for character in folded.casefold() if character.isalnum())


class LocalPlayerResolver:
    def __init__(self, season):
        self.season = season
        self._team_players = {}
        self._resolved = {}
        self._aliases = {
            row.alias_player_id: row.canonical_player
            for row in PlayerIdentityAlias.objects.select_related("canonical_player")
        }

    def _pool(self, team):
        if team.pk not in self._team_players:
            ids = set(PlayerTeamSeason.objects.filter(team=team, season=self.season).values_list("player_id", flat=True))
            ids.update(PlayerFixture.objects.filter(
                team=team,
                fixture__competition_season__season=self.season,
            ).values_list("player_id", flat=True))
            self._team_players[team.pk] = list(Player.objects.filter(pk__in=ids, provider="api_football"))
        return self._team_players[team.pk]

    @staticmethod
    def _names(player):
        return {
            _normal_name(value)
            for value in (
                player.name, player.common_name, player.first_name, player.last_name,
                f"{player.first_name or ''} {player.last_name or ''}",
            )
            if value
        }

    @staticmethod
    def _unique(rows):
        unique = {row.pk: row for row in rows}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def resolve(self, team, participant):
        key = (team.pk, participant["uefa_player_id"])
        if key in self._resolved:
            return self._resolved[key]
        pool = self._pool(team)
        birth = date.fromisoformat(participant["birth_date"]) if participant.get("birth_date") else None
        name = _normal_name(participant["name"])
        candidates = []
        if birth:
            candidates = [player for player in pool if player.birth_date == birth]
        player = self._unique(candidates)
        if player is None:
            player = self._unique([candidate for candidate in pool if name in self._names(candidate)])
        if player is None and birth:
            global_birth = Player.objects.filter(provider="api_football", birth_date=birth)
            player = self._unique([candidate for candidate in global_birth if name in self._names(candidate)])
        if player is None:
            global_name = [candidate for candidate in Player.objects.filter(provider="api_football", name__iexact=participant["name"])]
            player = self._unique(global_name)
        if player is None:
            return None
        player = self._aliases.get(player.pk, player)
        self._resolved[key] = player
        return player


def _payload_digest(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _retain_raw(resource_type, resource_id, request_path, payload, *, provider=SOURCE_PROVIDER):
    digest = _payload_digest(payload)
    existing = RawProviderPayload.objects.filter(
        provider=provider,
        resource_type=resource_type,
        provider_resource_id=resource_id,
        payload_sha256=digest,
    ).order_by("pk").first()
    if existing:
        return existing
    return RawProviderPayload.objects.create(
        provider=provider,
        resource_type=resource_type,
        provider_resource_id=resource_id,
        request_path=request_path,
        payload=payload,
        payload_sha256=digest,
        http_status=200,
    )


def collect_reconstruction(client, fixture_ids=FIXTURE_IDS, *, stage=False, enpelotas_directory=None):
    enpelotas_directory = Path(enpelotas_directory) if enpelotas_directory else None
    if enpelotas_directory and not enpelotas_directory.is_dir():
        raise ValidationError(f"EnPelotas export directory does not exist: {enpelotas_directory}")
    matches = client.list_2017_18_ucl_matches()
    local_ids = [api_id for api_id, _ in fixture_ids]
    fixtures = {
        fixture.provider_id: fixture
        for fixture in Fixture.objects.filter(provider="api_football", provider_id__in=local_ids).select_related(
            "home_team", "away_team", "competition_season__season", "competition_season__competition"
        )
    }
    missing_fixtures = sorted(set(local_ids) - set(fixtures))
    if missing_fixtures:
        raise ValidationError(f"Missing API-Football fixtures: {missing_fixtures}")
    season_ids = {fixture.competition_season.season_id for fixture in fixtures.values()}
    if len(season_ids) != 1 or {fixture.competition_season.season.slug for fixture in fixtures.values()} != {"2017-18"}:
        raise ValidationError("Reconstruction fixtures must belong to award season 2017/18")
    if any(fixture.competition_season.competition.provider_id != "2" for fixture in fixtures.values()):
        raise ValidationError("Reconstruction scope contains a non-Champions-League fixture")
    resolver = LocalPlayerResolver(next(iter(fixtures.values())).competition_season.season)

    staged = []
    unresolved = []
    raw_evidence = {}
    for api_id, uefa_id in fixture_ids:
        match = matches.get(uefa_id)
        if match is None:
            raise ValidationError(f"UEFA match {uefa_id} is absent from the 2017/18 index")
        fixture = fixtures[api_id]
        evidence = client.get_match_evidence(uefa_id)
        normalized = normalize_official_evidence(match, evidence)
        enpelotas_raw = None
        if enpelotas_directory:
            export_path = enpelotas_directory / f"{uefa_id}.html"
            if not export_path.is_file():
                raise ValidationError(f"Missing EnPelotas export: {export_path}")
            supplemental, enpelotas_raw = parse_enpelotas_export(export_path, match)
            normalized = merge_enpelotas_evidence(normalized, supplemental)
        if fixture.starts_at.date().isoformat() != normalized["date"]:
            raise ValidationError(f"Fixture date mismatch for API-Football {api_id} / UEFA {uefa_id}")
        if (fixture.home_score, fixture.away_score) != (normalized["home_score"], normalized["away_score"]):
            raise ValidationError(f"Fixture score mismatch for API-Football {api_id} / UEFA {uefa_id}")
        for participant in normalized["participants"]:
            team = fixture.home_team if participant["team_side"] == "home" else fixture.away_team
            player = resolver.resolve(team, participant)
            if player is None:
                unresolved.append({
                    "api_fixture_id": api_id,
                    "uefa_match_id": uefa_id,
                    "uefa_player_id": participant["uefa_player_id"],
                    "name": participant["name"],
                    "birth_date": participant.get("birth_date"),
                    "team": team.name,
                })
            else:
                participant["player_id"] = player.pk
                participant["player_provider_id"] = player.provider_id
        normalized["api_football_fixture_id"] = api_id
        staged.append((fixture, normalized))
        raw_evidence[api_id] = (match, evidence, enpelotas_raw)
    if unresolved:
        sample = "; ".join(f"{row['name']} ({row['team']}, UEFA {row['uefa_player_id']})" for row in unresolved[:10])
        raise ValidationError(f"{len(unresolved)} UEFA player identities are unresolved: {sample}")

    if stage:
        with transaction.atomic():
            for fixture, normalized in staged:
                api_id = fixture.provider_id
                uefa_id = normalized["uefa_match_id"]
                match, evidence, enpelotas_raw = raw_evidence[api_id]
                raw = [
                    _retain_raw("uefa_match", uefa_id, f"{client.MATCHES_URL}?competitionId=1&seasonYear=2018", match),
                    _retain_raw("uefa_lineup", uefa_id, client.LINEUPS_URL.format(match_id=uefa_id), evidence["lineups"]),
                    _retain_raw("uefa_player_statistics", uefa_id, client.PLAYER_STATS_URL.format(match_id=uefa_id), evidence["player_statistics"]),
                    _retain_raw("uefa_team_statistics", uefa_id, client.TEAM_STATS_URL.format(match_id=uefa_id), evidence["team_statistics"]),
                ]
                player_profiles = evidence.get("player_profiles") or []
                if player_profiles:
                    profile_ids = ",".join(str(profile["id"]) for profile in player_profiles)
                    raw.append(_retain_raw(
                        "uefa_player_profiles",
                        uefa_id,
                        f"{client.PLAYERS_URL}?playerIds={profile_ids}",
                        player_profiles,
                    ))
                if enpelotas_raw:
                    raw.append(_retain_raw(
                        "enpelotas_export",
                        enpelotas_raw["canonical_url"].rsplit("-n-", 1)[-1],
                        enpelotas_raw["canonical_url"],
                        enpelotas_raw,
                        provider="enpelotas",
                    ))
                digest = _payload_digest(normalized)
                status = FixtureStatReconstruction.Status.VERIFIED if normalized["verification"]["complete"] else FixtureStatReconstruction.Status.PARTIAL
                existing = FixtureStatReconstruction.objects.filter(version=RECONSTRUCTION_VERSION, fixture=fixture).first()
                if existing and existing.payload_sha256 != digest:
                    raise ValidationError(f"Staged evidence changed for fixture {api_id}; create a new reconstruction version")
                if existing and existing.status == FixtureStatReconstruction.Status.APPLIED:
                    raise ValidationError(f"Fixture {api_id} reconstruction is already applied and immutable")
                FixtureStatReconstruction.objects.update_or_create(
                    version=RECONSTRUCTION_VERSION,
                    fixture=fixture,
                    defaults={
                        "source_provider": "uefa_official+enpelotas" if enpelotas_raw else SOURCE_PROVIDER,
                        "source_fixture_id": uefa_id,
                        "status": status,
                        "normalized_payload": normalized,
                        "verification": normalized["verification"],
                        "payload_sha256": digest,
                        "raw_payload_ids": [row.pk for row in raw],
                    },
                )
    missing_sources = sorted({source for _, row in staged for source in row["verification"]["missing_formula_sources"]})
    partial_sources = sorted({source for _, row in staged for source in row["verification"].get("partial_formula_sources", [])})
    return {
        "fixtures": len(staged),
        "player_rows": sum(len(row["participants"]) for _, row in staged),
        "covered_formula_sources": sorted(FORMULA_SOURCES - set(missing_sources)),
        "missing_formula_sources": missing_sources,
        "partial_formula_sources": partial_sources,
        "status": "VERIFIED" if not missing_sources and not partial_sources else "PARTIAL",
        "staged": stage,
    }
