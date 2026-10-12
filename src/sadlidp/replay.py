"""Rebuild a past week's page from saved data (D22 week picker; owner request 2026-10-12).

Runs the normal sync pipeline offline, as of one minute before the week's first kickoff, against:
- the week's saved projection snapshot (the backfilled Sleeper rows);
- the rosters and lineups of that week (Sleeper's matchups for the week);
- the saved league settings, schedule and last season's σ_out.

Each projected player takes his team from the projection row and is treated as healthy (that week's
injury news isn't known). The result is written as the week's consensus.json, marked `replayed`;
the site build then re-scores it with the week's final stats like any past week.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from . import storage
from .net import Response
from .schedule import NFLVERSE_GAMES, Schedule
from .sleeper import API, V1
from .sync import run_sync

NOTE = (
    "Rebuilt after the fact: tiers and suggestions as they would have looked before the week's first game, "
    "from Sleeper's archived projections and that week's rosters. That week's injury news isn't known, so "
    "everyone projected to play is treated as available."
)


class ReplayClient:
    """Serves saved data for the replayed week; refuses everything else (no live data leaks in)."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes

    def get(self, url, **kw) -> Response:
        body = self.routes.get(url.split("?")[0])
        if body is None:
            return Response(url=url, final_url=url, status=404)
        return Response(url=url, final_url=url, status=200, body=json.dumps(body).encode())


def replay_week(
    cfg: dict,
    data_dir: Path | str,
    season: int,
    week: int,
    matchups: list[dict],
    players_db: dict[str, dict],
    log: Callable[[str], None] = print,
) -> Path:
    data_dir = Path(data_dir)
    wdir = storage.week_dir(data_dir, season, week)
    league = storage.read_json(data_dir / str(season) / "league.json")
    source = storage.read_json(wdir / "sources" / "sleeper_proj.json")
    sched_json = storage.read_json(data_dir / str(season) / "schedule.json")
    if not league or not source or not sched_json:
        raise FileNotFoundError(f"week {week}: league.json, schedule.json or the projection snapshot is missing")
    kicks = [g.kickoff for g in Schedule.from_json(sched_json).week_games(week) if g.kickoff]
    now = min(kicks) - timedelta(minutes=1)

    # rosters of that week: players and starters from its matchups, owners from the latest rosters
    latest = max(
        (p for p in (data_dir / str(season)).glob("week*/rosters.json")), key=lambda p: p.parent.name, default=None
    )
    owners = {r["roster_id"]: r for r in (storage.read_json(latest) if latest else []) or []}
    rosters = [
        {
            "roster_id": m["roster_id"],
            "owner_id": (owners.get(m["roster_id"]) or {}).get("owner_id"),
            "co_owners": (owners.get(m["roster_id"]) or {}).get("co_owners"),
            "players": [str(p) for p in m.get("players") or []],
            "starters": [str(p) for p in m.get("starters") or []],
            "reserve": [],
            "taxi": [],
        }
        for m in matchups
    ]
    rows = source.get("rows") or {}
    players = dict(players_db)
    for pid, row in rows.items():
        players[pid] = {**players.get(pid, {}), "team": row.get("team"), "status": "Active", "active": True,
                        "injury_status": None}  # fmt: skip
    projections = [
        {"player_id": pid, "week": week, "team": r.get("team"), "opponent": r.get("opponent"), "stats": r["stats"]}
        for pid, r in rows.items()
    ] + [  # the snapshot's placeholders stay "no projection" (never zero-filled)
        {"player_id": pid, "week": week, "opponent": None, "stats": {}} for pid in source.get("no_projection_ids") or []
    ]
    lid = league["league_id"]
    routes = {
        f"{V1}/state/nfl": {"season": str(season), "season_type": "regular", "week": week, "display_week": week},
        f"{V1}/user/{cfg['league']['username']}": {"user_id": league["user_id"]},
        f"{V1}/user/{league['user_id']}/leagues/nfl/{season}": [{**league, "name": league.get("name")}],
        f"{V1}/league/{lid}": league,
        f"{V1}/league/{lid}/rosters": rosters,
        f"{V1}/league/{lid}/matchups/{week}": matchups,
        f"{V1}/players/nfl": players,
        f"{API}/projections/nfl/{season}/{week}": projections,
        NFLVERSE_GAMES: None,  # served from the saved schedule below
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / str(season)).mkdir(parents=True)
        storage.write_json(tmp / str(season) / "schedule.json", sched_json)
        prev = data_dir / str(season - 1) / "sigma_out.json"
        if prev.exists():
            (tmp / str(season - 1)).mkdir(parents=True)
            shutil.copy(prev, tmp / str(season - 1) / "sigma_out.json")
        run_sync(cfg, tmp, tmp / "cache", now=now, force=True, client=ReplayClient(routes), log=log)
        data = json.loads((storage.week_dir(tmp, season, week) / "consensus.json").read_text())
    data["replayed"] = {"as_of": now.isoformat(), "note": NOTE}
    data["warnings"] = [w for w in data.get("warnings", []) if not w.startswith(("σ_out", "live stats"))]
    out = wdir / "consensus.json"
    storage.write_json(out, data)
    return out
