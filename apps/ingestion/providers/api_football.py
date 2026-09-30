import logging
import random
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from django.conf import settings

from .base import (
    ProviderCompetition,
    ProviderFixture,
    ProviderFixtureBundle,
    ProviderMetric,
    ProviderParticipation,
    ProviderPlayer,
    ProviderRequestLimitReached,
    ProviderSeason,
    ProviderTeam,
)

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN"}
STATUS_MAP = {
    "TBD": "SCHEDULED", "NS": "SCHEDULED", "1H": "LIVE", "HT": "LIVE",
    "2H": "LIVE", "ET": "LIVE", "BT": "LIVE", "P": "LIVE", "INT": "LIVE",
    "FT": "FINISHED", "AET": "FINISHED", "PEN": "FINISHED",
    "PST": "POSTPONED", "SUSP": "POSTPONED", "CANC": "CANCELLED", "ABD": "CANCELLED",
}


def _decimal(value):
    if isinstance(value, str):
        value = value.rstrip("%")
    try:
        return Decimal(str(value)) if value is not None and value != "" else None
    except (InvalidOperation, TypeError):
        return None


def _season_id(league_id, year):
    return f"{league_id}:{year}"


def _split_season_id(value):
    league_id, year = str(value).split(":", 1)
    return league_id, int(year)


class ApiFootballProvider:
    provider_name = "api_football"

    def __init__(self, client=None):
        if not settings.API_FOOTBALL_KEY:
            raise ValueError("API_FOOTBALL_KEY is required")
        self.base_url = settings.API_FOOTBALL_BASE_URL.rstrip("/")
        self.client = client or httpx.Client(timeout=20)
        self.quota_remaining = None
        self.quota_reserve = 0
        self.requests_made = 0
        self.request_budget = None
        self.min_request_interval = 0.0
        self._last_request_at = None

    def configure_request_limits(self, max_requests=None, min_interval=0.0):
        self.request_budget = max_requests
        self.min_request_interval = max(0.0, float(min_interval))

    def _before_request(self):
        if self.quota_remaining is not None and self.quota_remaining <= self.quota_reserve:
            raise ProviderRequestLimitReached("API-Football daily reserve reached")
        if self.request_budget is not None and self.requests_made >= self.request_budget:
            raise ProviderRequestLimitReached("Configured API-Football request budget reached")
        if self._last_request_at is not None and self.min_request_interval:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
        self._last_request_at = time.monotonic()
        self.requests_made += 1

    def _request(self, path, params=None):
        if self.quota_remaining == 0:
            raise ProviderRequestLimitReached("API-Football daily request quota exhausted; resume on the next scheduled run")
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(4):
            try:
                self._before_request()
                response = self.client.get(
                    url,
                    params=params or {},
                    headers={"x-apisports-key": settings.API_FOOTBALL_KEY},
                )
                remaining = response.headers.get("x-ratelimit-requests-remaining")
                if remaining is not None:
                    self.quota_remaining = int(remaining)
                    logger.info("api_football_quota remaining=%s", remaining)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt == 3:
                        response.raise_for_status()
                    delay = float(response.headers.get("Retry-After", 0) or 0) or (2 ** attempt + random.random())
                    logger.warning("provider_retry path=%s status=%s attempt=%s", path, response.status_code, attempt + 1)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise ValueError(f"API-Football response errors for {path}: {payload['errors']}")
                return payload
            except httpx.TransportError:
                if attempt == 3:
                    raise
                logger.warning("provider_retry path=%s transport_error attempt=%s", path, attempt + 1)
                time.sleep(2 ** attempt + random.random())
        raise RuntimeError("Provider retry loop exhausted")

    def _all(self, path, params=None):
        payload = self._request(path, params or {})
        rows = list(payload.get("response") or [])
        paging = payload.get("paging") or {}
        total = int(paging.get("total") or 1)
        for page in range(2, total + 1):
            payload = self._request(path, {**(params or {}), "page": page})
            rows.extend(payload.get("response") or [])
        return rows

    def get_profile_page(self, page=1):
        if page < 1: raise ValueError("Profile page must be positive")
        payload=self._request("players/profiles",{"page":page})
        paging=payload.get("paging") or {}
        rows=payload.get("response")
        if not isinstance(rows,list) or not rows or paging.get("current")!=page or not isinstance(paging.get("total"),int) or paging["total"]<page:
            raise ValueError("Invalid player-profile pagination response")
        if len(rows)>250 or payload.get("results")!=len(rows):
            raise ValueError("Invalid player-profile page size")
        ids=[]
        for row in rows:
            player=row.get("player") or {}
            if not isinstance(player.get("id"),int) or player["id"]<1:
                raise ValueError("Invalid player-profile identity")
            position=player.get("position")
            if position is not None and (not isinstance(position,str) or len(position)>100):
                raise ValueError("Invalid player-profile position")
            ids.append(player["id"])
        if len(ids)!=len(set(ids)): raise ValueError("Duplicate players in profile page")
        return payload

    def list_competitions(self):
        output = []
        for row in self._all("leagues"):
            league = row.get("league") or {}
            country = row.get("country") or {}
            if not league.get("id"):
                continue
            if league.get("name") == "UEFA Champions League":
                kind = "UCL"
            elif league.get("type") == "League":
                kind = "DOMESTIC_LEAGUE"
            else:
                kind = "CUP" if league.get("type") == "Cup" else "UNKNOWN"
            country_code = country.get("code")
            if country_code and len(country_code) > 3:
                country_code = None
            output.append(ProviderCompetition(str(league["id"]), league.get("name") or str(league["id"]), country_code, kind))
        return output

    def list_seasons(self, competition_id):
        rows = self._all("leagues", {"id": competition_id})
        output = []
        for row in rows:
            for season in row.get("seasons") or []:
                start = date.fromisoformat(season["start"])
                end = date.fromisoformat(season["end"])
                year = season["year"]
                output.append(ProviderSeason(_season_id(competition_id, year), f"{year}/{str(year + 1)[-2:]}", start, end))
        return output

    def list_teams(self, provider_season_id):
        league_id, year = _split_season_id(provider_season_id)
        return [
            ProviderTeam(str(row["team"]["id"]), row["team"]["name"], row["team"].get("code"), row["team"].get("country"), row["team"].get("logo"))
            for row in self._all("teams", {"league": league_id, "season": year})
        ]

    def list_players(self, team_id, provider_season_id):
        league_id, year = _split_season_id(provider_season_id)
        output = []
        for row in self._all("players", {"team": team_id, "league": league_id, "season": year}):
            player = row.get("player") or {}
            statistics = row.get("statistics") or []
            games = (statistics[0].get("games") or {}) if statistics else {}
            birth = player.get("birth") or {}
            output.append(ProviderPlayer(
                str(player["id"]), player.get("name") or str(player["id"]), games.get("position"),
                nationality_code=player.get("nationality"), first_name=player.get("firstname"),
                last_name=player.get("lastname"), birth_date=date.fromisoformat(birth["date"]) if birth.get("date") else None,
                image_url=player.get("photo"), height_cm=self._height_cm(player.get("height")),
            ))
        return output

    @staticmethod
    def _height_cm(value):
        if not value:
            return None
        parsed = _decimal(str(value).split()[0])
        return int(parsed) if parsed is not None else None

    def list_fixtures(self, provider_season_id, start, end):
        league_id, year = _split_season_id(provider_season_id)
        rows = self._all("fixtures", {"league": league_id, "season": year, "from": start.isoformat(), "to": end.isoformat()})
        return [self._normalize_fixture_meta(row, provider_season_id) for row in rows]

    def get_fixture_details(self, fixture):
        if isinstance(fixture, ProviderFixture) and fixture.raw_payload:
            fixture_data = fixture.raw_payload
            fixture_id = fixture.id
        else:
            fixture_id = fixture.id if isinstance(fixture, ProviderFixture) else str(fixture)
            fixture_rows = self._all("fixtures", {"id": fixture_id})
            if len(fixture_rows) != 1:
                raise ValueError(f"Expected one API-Football fixture for {fixture_id}")
            fixture_data = fixture_rows[0]
        player_payload = self._request("fixtures/players", {"fixture": fixture_id})
        payload = {"fixture": fixture_data, "players": player_payload}
        return self.normalize_fixture(payload)

    def get_fixture_batch(self, fixtures):
        ids=[item.id for item in fixtures]
        if not ids or len(ids)>20 or len(set(ids))!=len(ids):
            raise ValueError("Fixture batches require 1–20 unique IDs")
        rows=self._all("fixtures",{"ids":"-".join(ids)})
        by_id={str(row["fixture"]["id"]):row for row in rows}
        if len(rows)!=len(ids) or set(by_id)!=set(ids):
            raise ValueError("Batch response does not match the requested fixture IDs")
        return [self.normalize_fixture({"fixture":by_id[value],"players":{"response":by_id[value].get("players") or []}}) for value in ids]

    def _normalize_fixture_meta(self, data, provider_season_id=None):
        fixture = data.get("fixture") or {}
        league = data.get("league") or {}
        teams = data.get("teams") or {}
        goals = data.get("goals") or {}
        status = (fixture.get("status") or {}).get("short") or "NS"
        starts_at = datetime.fromisoformat(fixture["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        season_id = provider_season_id or _season_id(league["id"], league["season"])
        return ProviderFixture(
            str(fixture["id"]), season_id,
            ProviderTeam(str(teams["home"]["id"]), teams["home"]["name"], logo_url=teams["home"].get("logo")),
            ProviderTeam(str(teams["away"]["id"]), teams["away"]["name"], logo_url=teams["away"].get("logo")),
            starts_at, STATUS_MAP.get(status, "SCHEDULED"), goals.get("home"), goals.get("away"),
            league.get("round"), league.get("round"), data,
            available_minutes=90 if status=="FT" else 120 if status=="AET" else (fixture.get("status") or {}).get("elapsed") if status=="PEN" and (fixture.get("status") or {}).get("elapsed") in (90,120) else None,
        )

    def normalize_fixture(self, payload):
        fixture_data = payload["fixture"]
        fixture = self._normalize_fixture_meta(fixture_data)
        team_ids = {fixture.home_team.id, fixture.away_team.id}
        participations = []
        for team_row in (payload.get("players") or {}).get("response") or []:
            team_id = str((team_row.get("team") or {}).get("id"))
            if team_id not in team_ids:
                continue
            opponent_id = next(value for value in team_ids if value != team_id)
            for row in team_row.get("players") or []:
                player_data = row.get("player") or {}
                stats = (row.get("statistics") or [{}])[0]
                games = stats.get("games") or {}
                player = ProviderPlayer(str(player_data["id"]), player_data.get("name") or str(player_data["id"]), games.get("position"), image_url=player_data.get("photo"))
                participations.append(ProviderParticipation(
                    player, team_id, opponent_id, games.get("position"), not bool(games.get("substitute")),
                    int(games.get("minutes") or 0), tuple(self._metrics(stats)),
                ))
        for team_id, team_score in ((fixture.home_team.id, fixture.home_score), (fixture.away_team.id, fixture.away_score)):
            team_rows = [row for row in participations if row.team_id == team_id]
            reported_goals = sum(
                (metric.value for row in team_rows for metric in row.metrics if metric.key == "goals"),
                Decimal("0"),
            )
            if fixture.status != "FINISHED" or team_score is None or reported_goals != team_score:
                continue
            for index, row in enumerate(participations):
                if row.team_id == team_id and row.minutes > 0 and not any(metric.key == "goals" for metric in row.metrics):
                    participations[index] = replace(row, metrics=(*row.metrics, ProviderMetric("goals", Decimal("0"))))
        return ProviderFixtureBundle(fixture, tuple(participations), payload)

    def _metrics(self, stats):
        paths = {
            "goals": ("goals", "total"), "assists": ("goals", "assists"),
            "shots": ("shots", "total"), "shots_on_target": ("shots", "on"),
            "key_passes": ("passes", "key"), "passes": ("passes", "total"),
            "tackles": ("tackles", "total"), "interceptions": ("tackles", "interceptions"),
            "duels": ("duels", "total"), "duels_won": ("duels", "won"),
            "dribble_attempts": ("dribbles", "attempts"), "successful_dribbles": ("dribbles", "success"),
            "goals_conceded": ("goals", "conceded"), "saves": ("goals", "saves"),
            "penalties_saved": ("penalty", "saved"),
        }
        values = {key: _decimal((stats.get(group) or {}).get(field)) for key, (group, field) in paths.items()}
        accuracy_raw = (stats.get("passes") or {}).get("accuracy")
        accuracy = _decimal(accuracy_raw)
        if values["passes"] is not None and accuracy is not None:
            values["accurate_passes"] = values["passes"] * accuracy / Decimal("100") if isinstance(accuracy_raw,str) and accuracy_raw.endswith("%") else accuracy
        metrics = []
        for key, value in values.items():
            if value is None:
                continue
            numerator = denominator = None
            if key == "passes":
                numerator, denominator = values.get("accurate_passes"), value
            elif key == "duels":
                numerator, denominator = values.get("duels_won"), value
            elif key == "shots":
                numerator, denominator = values.get("goals"), value
                if numerator is not None and value>=0 and numerator>value:
                    # Retain both provider counts, but never score an impossible conversion ratio.
                    numerator = denominator = None
            elif key == "saves":
                conceded = values.get("goals_conceded")
                numerator, denominator = value, value + conceded if conceded is not None else None
            metrics.append(ProviderMetric(key, value, numerator, denominator, True))
        return metrics
