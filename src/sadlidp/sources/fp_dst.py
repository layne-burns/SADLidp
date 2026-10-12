"""FantasyPros weekly D/ST rankings, kept as a signal only (D18; SPEC.md §5.2 "D/ST signal").

Stored per team (rank and opponent) so PR 6 can backtest whether a team-defense rank says anything the IDP
projections don't. It never enters the consensus. Same page format and week check as the ECR lists.
"""

from __future__ import annotations

from ..config import load_alias
from .base import OK, AdapterResult, FetchContext
from .fp_ecr import HOST, published, read_page, team_of

NAME = "fp_dst"
URL = "https://www.fantasypros.com/nfl/rankings/dst.php?week={week}"  # for the backfill runner's host budget
__all__ = ["HOST", "NAME", "URL", "fetch"]


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="signal", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    data, status, error = read_page(ctx, "dst")
    result.report = {"archive": ctx.archive, "experts": (data or {}).get("total_experts")}
    if status != OK:
        result.status, result.error = status, error
        return result
    teams = load_alias(ctx.cfg, "fp_teams")
    for p in data["players"]:
        team = team_of((p or {}).get("player_team_id"), teams) if isinstance(p, dict) else None
        if team and isinstance(p.get("rank_ecr"), int | float):
            opp = team_of(p.get("player_opponent_id"), teams)
            home = not str(p.get("player_opponent") or "").startswith("at ")
            result.rows[team] = {"rank": p["rank_ecr"], "opponent": opp, "home": home}
    result.universe = {"DST": {"complete": False, "depth": len(result.rows)}}
    result.published_at = published(data)
    result.status = OK if result.rows else "parse_error"
    return result
