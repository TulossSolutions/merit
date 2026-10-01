import random
import time

import httpx


class UefaOfficialClient:
    """Small client for the public endpoints used by UEFA's own match pages."""

    MATCHES_URL = "https://match.uefa.com/v5/matches"
    LINEUPS_URL = "https://match.uefa.com/v5/matches/{match_id}/lineups"
    PLAYER_STATS_URL = "https://matchstats.uefa.com/v1/player-statistics/{match_id}"
    TEAM_STATS_URL = "https://matchstats.uefa.com/v1/team-statistics/{match_id}"
    PLAYERS_URL = "https://comp.uefa.com/v2/players"

    def __init__(self, client=None):
        self.client = client or httpx.Client(
            timeout=30,
            headers={"User-Agent": "Merit historical evidence importer/1.0"},
        )

    def _get(self, url, params=None):
        for attempt in range(4):
            try:
                response = self.client.get(url, params=params or {})
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt == 3:
                        response.raise_for_status()
                    delay = float(response.headers.get("Retry-After", 0) or 0) or (2 ** attempt + random.random())
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt + random.random())
        raise RuntimeError("UEFA request retry loop exhausted")

    def list_2017_18_ucl_matches(self):
        rows = self._get(
            self.MATCHES_URL,
            {"competitionId": 1, "seasonYear": 2018, "limit": 250, "offset": 0, "order": "ASC"},
        )
        if not isinstance(rows, list):
            raise ValueError("UEFA match index did not return a list")
        return {str(row["id"]): row for row in rows}

    def get_match_evidence(self, match_id):
        match_id = str(match_id)
        lineups = self._get(self.LINEUPS_URL.format(match_id=match_id))
        player_statistics = self._get(self.PLAYER_STATS_URL.format(match_id=match_id))
        lineup_ids = {
            str(((item.get("player") or {}).get("id")))
            for side in ("homeTeam", "awayTeam")
            for collection in ("field", "bench")
            for item in ((lineups.get(side) or {}).get(collection) or [])
        }
        missing_ids = sorted({str(row.get("playerId")) for row in player_statistics} - lineup_ids)
        player_profiles = self._get(self.PLAYERS_URL, {"playerIds": ",".join(missing_ids)}) if missing_ids else []
        return {
            "lineups": lineups,
            "player_statistics": player_statistics,
            "team_statistics": self._get(self.TEAM_STATS_URL.format(match_id=match_id)),
            "player_profiles": player_profiles,
        }
