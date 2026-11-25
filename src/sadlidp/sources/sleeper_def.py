"""Sleeper (RotoWire) team-defense projections (SPEC.md §5.2; team defenses added 2026-11-25, D25).

The sibling of `sleeper_k`, with its own request (`position[]=DEF`; the IDP and K query strings are unchanged)
and the same rules: only rows with `stats.gp` and an opponent are real projections, the rest are placeholders
("no projection", never 0), and a response with no real row is `suspect`. Only records whose Sleeper `position`
is DEF are kept. A DEF's Sleeper id is its team code ("KC"), so nothing needs matching.

The line is stored as published. Sleeper projects a numeric `pts_allow` and sets the one-hot `pts_allow_<tier>`
key of the tier that number falls in, so the league's points-allowed tiers score the tier of the projected mean
(Sleeper's own `pts_*` totals are dropped, like `adp_*`: they don't always count the tier). Whether the expected tier
points (a spread of points allowed instead of one tier) rank defenses better is registered test T1
(`docs/status.md` §8; run 2026-11-25, it did not pass), so nothing is derived. The universe is complete for DEF:
a team with a game and no row counts as 0 under the zero-fill rule (§6.4; never seen in 48 weeks of Sleeper's
DEF responses, which list every team with a game), and a team on bye has no row and no game.
"""

from __future__ import annotations

from ..positions import DEF, slot_of
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .sleeper_proj import HOST

NAME = "sleeper_def"
__all__ = ["HOST", "NAME", "fetch", "published_line"]  # HOST: the backfill runner's per-host request budget (§5.3)
TOTALS = ("pts_std", "pts_ppr", "pts_half_ppr")  # Sleeper's own fantasy-point totals: never a stat


def published_line(stats: dict) -> dict:
    """The stat line as Sleeper published it, less its own point totals and draft data. `pts_allow` and every
    `pts_allow_<tier>` key stay: the league scores the tiers (D25)."""
    return {k: v for k, v in stats.items() if not k.startswith(TOTALS) and not k.startswith(("adp_", "pos_adp_"))}


def fetch(ctx: FetchContext) -> AdapterResult:
    """Uses ctx.sleeper, season, week, now and players; Sleeper's ids need no matching, and nothing is imputed."""
    week = ctx.week
    result = AdapterResult(name=NAME, kind="projection", status="failed", week=week, fetched_at=ctx.now.isoformat())
    payload, error = ctx.sleeper.def_projections(ctx.season, week)
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
    teams = 0
    for r in payload:
        if not isinstance(r, dict) or r.get("player_id") is None:
            continue
        pid = str(r["player_id"])
        known = (ctx.players or {}).get(pid)
        if slot_of(known if known is not None else (r.get("player") or {})) != DEF:
            continue  # anyone the DEF query returns who isn't a team defense
        teams += 1
        stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
        if stats.get("gp") and r.get("opponent"):
            line = published_line(stats)
            result.rows[pid] = {"stats": line, "team": r.get("team"), "opponent": r.get("opponent")}
            result.covered_keys.update(k for k in line if k != "gp")
        else:
            result.no_projection_ids.add(pid)
    if not result.rows:  # nothing posted yet, caught mid-update, or not a DEF response at all
        why = "only placeholder rows; Sleeper may be mid-update" if teams else "no team-defense rows in the response"
        result.status, result.error = SUSPECT, why
        return result
    result.universe = {DEF: {"complete": True, "depth": None}}
    result.status = OK
    return result
