"""Sleeper (RotoWire) kicker projections (SPEC.md §5.2; kickers added 2026-11-17).

The sibling of `sleeper_proj`, with its own request (`position[]=K`; the IDP query string is unchanged) and
the same rules: only rows with `stats.gp` and an opponent are real projections, the rest are placeholders
("no projection", never 0), and a response with no real row is `suspect`. Only players whose Sleeper
`position` is K are kept (the K query also returns punters). The line is stored as published, with one
derived key: Sleeper projects misses only by distance band and the league scores only `fgmiss`, so
`fgmiss = max(0, fga − fgm)` is added and marked in the row's `imputed` list. `fgm_yds` is used as published.
The universe is complete for K.
"""

from __future__ import annotations

from ..kicker import line_keys, with_fgmiss
from ..positions import K, slot_of
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .sleeper_proj import HOST

NAME = "sleeper_k"
__all__ = ["HOST", "NAME", "fetch"]  # HOST: the backfill runner's per-host request budget (§5.3)


def fetch(ctx: FetchContext) -> AdapterResult:
    """Uses ctx.sleeper, season, week, now and players; Sleeper's ids need no matching, and nothing is imputed."""
    week = ctx.week
    result = AdapterResult(name=NAME, kind="projection", status="failed", week=week, fetched_at=ctx.now.isoformat())
    payload, error = ctx.sleeper.kicker_projections(ctx.season, week)
    if payload is None:
        client = getattr(ctx.sleeper, "client", None)
        result.status = "blocked" if HOST in (getattr(client, "blocked_hosts", None) or ()) else "failed"  # §7
        result.error = error
        return result
    if not isinstance(payload, list):
        result.status, result.error = "parse_error", "expected a list of projection rows"
        return result
    weeks = {int(r["week"]) for r in payload if isinstance(r, dict) and str(r.get("week", "")).isdigit()}
    if weeks and week not in weeks:
        result.status, result.week = "not_yet_published", max(weeks)
        return result
    kickers = 0
    for r in payload:
        if not isinstance(r, dict) or r.get("player_id") is None:
            continue
        pid = str(r["player_id"])
        known = (ctx.players or {}).get(pid)
        if slot_of(known if known is not None else (r.get("player") or {})) != K:
            continue  # a punter, or anyone else the K query returns
        kickers += 1
        stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
        if stats.get("gp") and r.get("opponent"):
            published = {k: v for k, v in stats.items() if not k.startswith(("pts_", "adp_", "pos_adp_"))}
            line, derived = with_fgmiss(published)
            row = {"stats": line, "team": r.get("team"), "opponent": r.get("opponent")}
            result.rows[pid] = {**row, "imputed": derived} if derived else row
            result.covered_keys.update(k for k in line_keys([published]) if k in published)
        else:
            result.no_projection_ids.add(pid)
    if not result.rows:  # nothing posted yet, caught mid-update, or not a kicker response at all
        why = "only placeholder rows; Sleeper may be mid-update" if kickers else "no kicker rows in the response"
        result.status, result.error = SUSPECT, why
        return result
    result.universe = {K: {"complete": True, "depth": None}}
    result.status = OK
    return result
