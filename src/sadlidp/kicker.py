"""Kicker (K) pieces shared by the pipeline and the kicker adapters (added 2026-11-17).

SADL scores a kicker 0.1 per yard of each made field goal (`fgm_yds`), −1 per missed field goal (`fgmiss`),
+1 per extra point (`xpm`) and −1 per missed extra point (`xpmiss`); nothing else. The sources publish
different parts of that line, so each adapter stores the line as published and derives what the league
scores but the source leaves out, marking every derived key in the row's `imputed` list (the page shows it
with `*`). Kickers have no form term and no fitted swing; their game-to-game noise is `[sigma_out] K`.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

from . import scoring, storage
from .positions import is_kicker

KICK_KEYS = ("fga", "fgm", "fgm_yds", "fgmiss", "xpa", "xpm", "xpmiss")
BUCKETS = ("0_19", "20_29", "30_39", "40_49", "50p")  # the `fgm_<bucket>` bands `[kicker.yards_per_make]` covers
YARDS_PER_MAKE = {"0_19": 19.0, "20_29": 25.4, "30_39": 34.4, "40_49": 44.4, "50p": 53.9}  # 2024–26, n = 2,398
MAX_ADDED_WEEKS = 4  # settled weeks that gain their kicker lines in one sync (one /stats request each)


def line_keys(lines: list[dict]) -> list[str]:
    """The kicking keys of a kicker's projected lines: `KICK_KEYS` plus every `fgm_<bucket>` key present."""
    buckets = sorted({k for line in lines for k in line if k.startswith("fgm_") and k not in KICK_KEYS})
    return [*KICK_KEYS, *buckets]


def with_fgmiss(line: dict) -> tuple[dict, list[str]]:
    """(line, derived keys): `fgmiss = max(0, fga − fgm)` when the source gives attempts and makes but no
    `fgmiss` (Sleeper projects misses only by distance band, and the league scores only `fgmiss`)."""
    if line.get("fgmiss") is not None or line.get("fga") is None or line.get("fgm") is None:
        return line, []
    miss = max(0.0, scoring.num(line["fga"]) - scoring.num(line["fgm"]))
    return {**line, "fgmiss": round(miss, 4)}, ["fgmiss"]


def yards_per_make(cfg: dict) -> dict[str, float]:
    """`[kicker.yards_per_make]`: the mean distance of a made field goal per bucket, for sources that publish
    makes by bucket and no yards. Accepts the whole config or the `[kicker]` table an adapter gets in `ctx.cfg`."""
    table = cfg.get("kicker") if "kicker" in cfg else cfg
    return {**YARDS_PER_MAKE, **{str(k): float(v) for k, v in ((table or {}).get("yards_per_make") or {}).items()}}


def one_point_edge(sigma_out: float | None) -> float | None:
    """How often a kicker projected 1 point higher outscores the other when both swing by `sigma_out` points
    from game to game: Φ(1 / (√2·σ)). About 0.56 at σ = 5 (the page's kicker note, SPEC §6.8)."""
    if not sigma_out or sigma_out <= 0:
        return None
    return 0.5 * (1.0 + math.erf(1.0 / (2.0 * sigma_out)))


def add_past_actuals(sleeper, data_dir: Path | str, season: int, sched, now: datetime, players: dict,
                     limit: int = MAX_ADDED_WEEKS) -> list[int]:  # fmt: skip
    """Add-only merge of kicker stat lines into settled weeks (SPEC §6.5 Actuals): a week captured after its
    stat-correction window is final and never refetched, so the weeks saved before kickers were tracked hold
    no kicker lines. Each such week is refetched once: lines are added for kickers (by Sleeper `position`,
    never by stat key: `TEAM_*` rows carry kicking keys) that have no line yet, no existing line changes, and
    `k_added_at` marks the week done. A week that already holds a kicker line was captured with kickers and is
    skipped. Newest weeks first, at most `limit` a sync. Returns the weeks written."""
    todo = []
    for week in sorted({g.week for g in sched.games}, reverse=True):
        kicks = [g.kickoff for g in sched.week_games(week) if g.kickoff]
        if not kicks or max(kicks) + timedelta(hours=4) > now:
            continue
        doc = storage.read_json(storage.week_dir(data_dir, season, week) / "actuals.json")
        settles, captured = sched.settles_at(week), (doc or {}).get("captured_at")
        if not doc or doc.get("k_added_at") or not captured or settles is None:
            continue
        if any(is_kicker(players.get(str(pid)) or {}) for pid in doc.get("stats") or {}):
            continue  # captured with kickers (refresh_actuals keeps their lines): nothing to add, no refetch
        if datetime.fromisoformat(captured) >= settles:  # final; an open week is rewritten whole, kickers included
            todo.append((week, doc))
    written = []
    for week, doc in todo[:limit]:
        payload, _ = sleeper.stats(season, week)
        if not isinstance(payload, dict):
            continue  # retried next sync
        have = doc.get("stats") or {}
        added = {str(pid): line for pid, line in payload.items()
                 if isinstance(line, dict) and str(pid) not in have
                 and is_kicker(players.get(str(pid)) or {})}  # fmt: skip
        doc = {**doc, "stats": {**added, **have}, "k_added_at": now.isoformat(), "k_added": len(added)}
        storage.write_json(storage.week_dir(data_dir, season, week) / "actuals.json", doc)
        written.append(week)
    return written
