"""FantasyPros weekly D/ST ECR as a signal only (SPEC.md §5.2 "D/ST signal", D18).

`dst.php` is read like the IDP pages (sources.fantasypros) into team -> {rank, experts, name}, keyed by Sleeper
team code. Never blended into μ: the breakdown shows it as "team defense #k (not used)" and the PR 6 backtest
tests it. No player matching.
"""

from __future__ import annotations

from collections import Counter

from ..config import load_alias
from .base import OK, AdapterResult, FetchContext
from .fantasypros import _float, _int, failures, read_page

NAME = "fp_dst"
LIST = "DST"


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="signal", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    teams = load_alias(ctx.cfg, "fantasypros_teams")
    if not teams:
        result.error = "config/aliases/fantasypros_teams.json is missing"
        return result
    page = read_page(ctx, "dst", LIST)
    result.report = {"lists": {LIST: page.report()}, "archive": ctx.archive}
    result.status, result.error = page.status, failures({LIST: page})
    if page.status != OK:
        if page.status == "not_yet_published":
            result.week = page.week
        return result
    unresolved: Counter[str] = Counter()
    experts = _int(page.ecr.get("total_experts"))
    for p in page.ecr["players"]:
        rank = _float(p.get("rank_ecr")) if isinstance(p, dict) else None
        code = str(p.get("player_team_id") or "").upper() if isinstance(p, dict) else ""
        team = teams.get(code)
        if code not in teams:
            unresolved[code] += 1
        if rank is None or team is None or (team in result.rows and result.rows[team]["rank"] <= rank):
            continue
        result.rows[team] = {"rank": rank, "experts": experts, "name": p.get("player_name")}
    result.published_at = page.published_at
    result.universe = {LIST: {"complete": False, "depth": len(result.rows), "ok": True}}
    result.report["unresolved_teams"] = dict(unresolved)
    return result
