"""Rebuild a past week's page from saved data (D22 week picker; owner request 2026-10-12).

Runs the normal sync pipeline offline, as of one minute before the week's first kickoff, against:
- every source file saved for the week (SPEC §5.3: no refetch; each source's live fetch fails, so the
  sync uses the saved rows, and a source with no file that week is left out);
- the rosters and lineups of that week (Sleeper's matchups for the week);
- the saved league settings, schedule and last season's σ_out.

Each projected player takes his team from a projection row (Sleeper's first) and is treated as healthy
(that week's injury news isn't known). The result is written as the week's consensus.json, marked
`replayed`; the site build then re-scores it with the week's final stats like any past week.
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
from .sleeper import V1
from .sync import SOURCES, run_sync

NOTE = (
    "Rebuilt after the fact: tiers and suggestions as they would have looked before the week's first game, "
    "from the projections saved for that week (from each source's archive) and that week's rosters. That "
    "week's injury news isn't known, so everyone projected to play is treated as available."
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
    saved = {
        name: wdir / "sources" / f"{name}.json" for name in SOURCES if (wdir / "sources" / f"{name}.json").exists()
    }
    sched_json = storage.read_json(data_dir / str(season) / "schedule.json")
    if not league or not saved or not sched_json:
        raise FileNotFoundError(f"week {week}: league.json, schedule.json or every source snapshot is missing")
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
    players = dict(players_db)
    for path in reversed(saved.values()):  # the first source's team wins (Sleeper's rows carry that week's team)
        for pid, row in (storage.load_source(path).get("rows") or {}).items():
            team = row.get("team") or players.get(pid, {}).get("team")
            players[pid] = {**players.get(pid, {}), "team": team, "status": "Active", "active": True,
                            "injury_status": None}  # fmt: skip
    # sources with no file that week are switched off; the backfill runner has nothing to do in a replay
    sources_cfg = {**(cfg.get("sources") or {})}
    for name in SOURCES:
        if name not in saved:
            sources_cfg[name] = {**(sources_cfg.get(name) or {}), "enabled": False}
    run_cfg = {**cfg, "sources": sources_cfg, "backfill": {**(cfg.get("backfill") or {}), "budget_s": 0}}
    lid = league["league_id"]
    routes = {
        f"{V1}/state/nfl": {"season": str(season), "season_type": "regular", "week": week, "display_week": week},
        f"{V1}/user/{cfg['league']['username']}": {"user_id": league["user_id"]},
        f"{V1}/user/{league['user_id']}/leagues/nfl/{season}": [{**league, "name": league.get("name")}],
        f"{V1}/league/{lid}": league,
        f"{V1}/league/{lid}/rosters": rosters,
        f"{V1}/league/{lid}/matchups/{week}": matchups,
        f"{V1}/players/nfl": players,
        NFLVERSE_GAMES: None,  # served from the saved schedule below; projections aren't served at all
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / str(season)).mkdir(parents=True)
        storage.write_json(tmp / str(season) / "schedule.json", sched_json)
        (tmp / str(season - 1)).mkdir(parents=True)
        for name in ("sigma_out.json", "impute_priors.json"):
            if (data_dir / str(season - 1) / name).exists():
                shutil.copy(data_dir / str(season - 1) / name, tmp / str(season - 1) / name)
        (storage.week_dir(tmp, season, week) / "sources").mkdir(parents=True)
        for name, path in saved.items():
            shutil.copy(path, storage.week_dir(tmp, season, week) / "sources" / f"{name}.json")
        run_sync(run_cfg, tmp, tmp / "cache", now=now, force=True, client=ReplayClient(routes), log=log)
        data = json.loads((storage.week_dir(tmp, season, week) / "consensus.json").read_text())
    data["replayed"] = {"as_of": now.isoformat(), "note": NOTE}
    # the refused live fetches ("<source>: failed ...; using earlier rows") are how a replay reads saved rows
    noise = ("σ_out", "live stats", *(f"{name}: failed" for name in SOURCES))
    data["warnings"] = [w for w in data.get("warnings", []) if not w.startswith(noise)]
    out = wdir / "consensus.json"
    storage.write_json(out, data)
    return out
