"""Sleeper API (SPEC.md §4.1, §4.2).

The documented /v1 endpoints are fatal when they fail (§7): the sync stops and the last good site
stays live. Projections and stats (undocumented, api.sleeper.com) are optional.
"""

from __future__ import annotations

import json
from pathlib import Path

from .net import Client

V1 = "https://api.sleeper.app/v1"
API = "https://api.sleeper.com"
IDP_POSITIONS_QUERY = "position[]=DL&position[]=LB&position[]=DB"


class SleeperError(RuntimeError):
    """A required Sleeper endpoint failed; the sync must stop."""


class Sleeper:
    def __init__(self, client: Client, cache_dir: Path | str = ".cache"):
        self.client = client
        self.cache_dir = Path(cache_dir)

    def _required(self, label: str, url: str):
        r = self.client.get(url)
        if not r.ok:
            raise SleeperError(f"{label}: HTTP {r.status}" + (f" ({r.error})" if r.error else ""))
        try:
            return r.json()
        except ValueError as e:
            raise SleeperError(f"{label}: response is not JSON") from e

    def _optional(self, url: str):
        r = self.client.get(url)
        if not r.ok:
            return None, f"HTTP {r.status}" + (f" ({r.error})" if r.error else "")
        try:
            return r.json(), None
        except ValueError:
            return None, "response is not JSON"

    # documented endpoints (fatal)
    def state(self) -> dict:
        return self._required("state", f"{V1}/state/nfl") or {}

    def user(self, username: str) -> dict | None:
        r = self.client.get(f"{V1}/user/{username}")
        return r.json() if r.ok and r.body.strip() not in (b"", b"null") else None

    def leagues(self, user_id: str, season: int) -> list[dict]:
        return self._required("leagues", f"{V1}/user/{user_id}/leagues/nfl/{season}") or []

    def league(self, league_id: str) -> dict:
        return self._required("league", f"{V1}/league/{league_id}")

    def rosters(self, league_id: str) -> list[dict]:
        return self._required("rosters", f"{V1}/league/{league_id}/rosters") or []

    def players(self, day: str) -> dict:
        """The full players DB, fetched at most once per (America/New_York) day via a file cache."""
        path = self.cache_dir / f"players_nfl_{day}.json"
        if path.exists():
            return json.loads(path.read_text())
        players = self._required("players", f"{V1}/players/nfl") or {}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for old in self.cache_dir.glob("players_nfl_*.json"):
            old.unlink()
        path.write_text(json.dumps(players))
        return players

    # optional endpoints
    def matchups(self, league_id: str, week: int):
        return self._optional(f"{V1}/league/{league_id}/matchups/{week}")

    def projections(self, season: int, week: int):
        return self._optional(f"{API}/projections/nfl/{season}/{week}?season_type=regular&{IDP_POSITIONS_QUERY}")

    def stats(self, season: int, week: int):
        """Weekly actual stats for every player, as {player_id: stats}."""
        return self._optional(f"{V1}/stats/nfl/regular/{season}/{week}")


def season_of(state: dict, fallback: int) -> int:
    return int(state.get("league_season") or state.get("season") or fallback)


def resolve_league(sleeper: Sleeper, cfg: dict, season: int, cached_user_id: str | None = None) -> tuple[dict, str]:
    """SPEC §4.2: the user's one league named in config for this season. Returns (league, user_id)."""
    lcfg = cfg.get("league") or {}
    user = sleeper.user(lcfg.get("username", ""))
    user_id = (user or {}).get("user_id") or cached_user_id
    if not user_id:
        raise SleeperError(f"Sleeper user {lcfg.get('username')!r} not found and no cached user_id")
    if lcfg.get("league_id"):
        return sleeper.league(str(lcfg["league_id"])), user_id
    wanted = {str(n).strip().lower() for n in lcfg.get("names", [])}
    leagues = sleeper.leagues(user_id, season)
    matched = [lg for lg in leagues if str(lg.get("name") or "").strip().lower() in wanted]
    if len(matched) != 1:
        found = ", ".join(f"{lg.get('name')} ({lg.get('league_id')})" for lg in leagues) or "none"
        raise SleeperError(f"expected exactly 1 league named {sorted(wanted)} in {season}, got {len(matched)}: {found}")
    return sleeper.league(matched[0]["league_id"]), user_id


def position_limits(settings: dict, fallback: int) -> dict[str, int]:
    """{'DL': 2, 'LB': 2, 'DB': 2} from settings.position_limit_*; missing keys use the config fallback."""
    out = {}
    for slot in ("DL", "LB", "DB"):
        v = (settings or {}).get(f"position_limit_{slot.lower()}")
        out[slot] = int(v) if isinstance(v, (int, float)) and v else fallback
    return out


def my_roster(rosters: list[dict], user_id: str) -> dict | None:
    for r in rosters:
        if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or []):
            return r
    return None
