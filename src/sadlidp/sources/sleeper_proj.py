"""Sleeper (RotoWire) IDP projections: the MVP projection source (SPEC.md §5.2, §13).

Only rows with `stats.gp` and an opponent are real projections; the rest are placeholders and mean
"no projection", never 0 (§4.1). The universe is complete for DL/LB/DB. A response with no real rows
at all is `suspect` (Sleeper rewrites a week's rows in a batch; mid-batch it serves only placeholders).
"""

from __future__ import annotations

from ..positions import IDP_SLOTS
from .base import OK, SUSPECT, AdapterResult, FetchContext

NAME = "sleeper_proj"
HOST = "api.sleeper.com"  # for the backfill runner's per-host request budget (§5.3)


def fetch(ctx: FetchContext) -> AdapterResult:
    """Uses ctx.sleeper, season, week and now; Sleeper's ids need no matching and its lines no imputation."""
    week = ctx.week
    result = AdapterResult(name=NAME, kind="projection", status="failed", week=week, fetched_at=ctx.now.isoformat())
    payload, error = ctx.sleeper.projections(ctx.season, week)
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
    for r in payload:
        if not isinstance(r, dict) or r.get("player_id") is None:
            continue
        pid = str(r["player_id"])
        stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
        if stats.get("gp") and r.get("opponent"):
            # Same full line the acceptance test scores exactly; only Sleeper's own point totals and ADP are dropped.
            line = {k: v for k, v in stats.items() if not k.startswith(("pts_", "adp_", "pos_adp_"))}
            result.rows[pid] = {"stats": line, "team": r.get("team"), "opponent": r.get("opponent")}
            result.covered_keys.update(k for k in line if k.startswith("idp_"))
        else:
            result.no_projection_ids.add(pid)
    if not result.rows:  # all placeholders: not posted yet, or caught mid-update (seen 2026-10-12 09:10 UTC)
        result.status, result.error = SUSPECT, "only placeholder rows; Sleeper may be mid-update"
        return result
    result.universe = {slot: {"complete": True, "depth": None} for slot in IDP_SLOTS}
    result.status = OK
    return result
