"""FantasyPros weekly team-defense rankings, free and logged out (SPEC.md §5.2; team defenses added 2026-11-25,
D25).

The sibling of `fp_k`, reading the D/ST page (`dst.php?week=N`) that the `fp_dst` signal already reads: one
request a run serves both (`fp_ecr.SHARED_PAGES`), and `fp_dst` stays exactly as it was (D18). One list, "DEF":
one row per team playing. A row's team code (`player_team_id`, through `fp_teams.json`: JAC → JAX) is the
team's Sleeper DEF id, so it matches by id; FantasyPros' own player ids (8000–8310) are kept as `fp_id`. Past
weeks are served through `?week=N`.
"""

from __future__ import annotations

from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import DEF
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .fp_dst import PAGE, URL
from .fp_ecr import FREE_AGENT, HOST, _float, _int, published, read_page, team_of

NAME = "fp_def"
POSITION = "DST"  # FantasyPros' `player_position_id` for a team defense
__all__ = ["HOST", "NAME", "URL", "fetch"]


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    data, status, error = read_page(ctx, PAGE)
    info = {"status": status, "error": error, "players": len((data or {}).get("players") or [])}
    if data:
        info.update(week=_int(data.get("week")), experts=data.get("total_experts"))
    result.report = {"lists": {DEF: info}, "archive": ctx.archive}
    if status != OK:
        result.status, result.error = status, error
        if status == "not_yet_published":
            result.week = info.get("week")
        return result
    teams = load_alias(ctx.cfg, "fp_teams")
    matcher = ctx.matcher or Matcher(ctx.players, slots=(DEF,))
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    best: dict[str, tuple] = {}  # Sleeper DEF id -> (Match, row)
    ranked = 0
    for p in data["players"]:
        if not isinstance(p, dict) or not isinstance(p.get("rank_ecr"), int | float):
            continue
        if str(p.get("player_position_id") or "").upper() != POSITION:
            continue
        ranked += 1
        code = str(p.get("player_team_id") or "").upper()
        name, team = p.get("player_name"), team_of(code, teams)
        if code and code != FREE_AGENT and team not in (ctx.players or {}):
            unresolved[code] += 1  # a code missing from fp_teams.json (§6.1 code tables)
        m = matcher.match(NAME, source_id=team, id_field="player_id", name=name, team=team, slot=DEF)
        match.add(m, name=name, team=team, slot=DEF)
        if m.pid is None:
            continue
        row = {"rank": p["rank_ecr"], "fp_id": str(p.get("player_id")), "name": name, "team": team,
               "ave": _float(p.get("rank_ave")), "std": _float(p.get("rank_std"))}  # fmt: skip
        prev = best.get(m.pid)
        if prev is not None:  # two rows on one team: keep the higher-precedence match
            kept, dropped = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
            match.conflict(m.pid, *(f"{r[1]['name']} (FP {r[1]['fp_id']}, {r[0].how})" for r in (kept, dropped)))
            best[m.pid] = kept
        else:
            best[m.pid] = (m, row)
    info["depth"] = ranked
    for pid, (_m, r) in best.items():
        result.rows[pid] = {"ranks": {DEF: r["rank"]}, "ave": {DEF: r["ave"]}, "std": {DEF: r["std"]},
                            "fp_id": r["fp_id"], "team": r["team"]}  # fmt: skip
    result.universe = {DEF: {"complete": False, "depth": ranked}}
    result.lists = {DEF: {pid: r["rank"] for pid, (_m, r) in best.items()}}  # whatever its kickoff (§6.4)
    result.published_at = published(data)
    result.report |= {"match": match.summary(), "unresolved_teams": dict(unresolved)}
    if not result.rows:  # a list none of whose rows is a known team defense is never saved as an empty week
        result.status, result.error = SUSPECT, f"none of the {ranked} ranked rows matched a team defense"
        return result
    result.status = OK
    return result
