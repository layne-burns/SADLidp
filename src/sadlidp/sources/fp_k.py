"""FantasyPros weekly kicker rankings, free and logged out (SPEC.md §5.2 "FantasyPros adapter"; kickers added
2026-11-17).

The sibling of `fp_ecr`: the K page (`k.php?week=N`) embeds the same `ecrData` object, read and week-checked by
`fp_ecr.read_page`. One list, "K": about one kicker per team playing, plus the odd free agent ("FA": no team,
so he matches only through the crosswalk id). Past weeks are served through `?week=N`, with each kicker's
current team, not the team he had that week.
"""

from __future__ import annotations

from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import K
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .fp_ecr import CROSSWALK, FREE_AGENT, HOST, _float, _int, published, read_page, team_of

NAME = "fp_k"
PAGE = "k"
URL = f"https://www.fantasypros.com/nfl/rankings/{PAGE}.php?week={{week}}"  # for the backfill runner's host budget
__all__ = ["HOST", "NAME", "URL", "fetch"]


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    data, status, error = read_page(ctx, PAGE)
    info = {"status": status, "error": error, "players": len((data or {}).get("players") or [])}
    if data:
        info.update(week=_int(data.get("week")), experts=data.get("total_experts"))
    result.report = {"lists": {K: info}, "archive": ctx.archive}
    if status != OK:
        result.status, result.error = status, error
        if status == "not_yet_published":
            result.week = info.get("week")
        return result
    teams = load_alias(ctx.cfg, "fp_teams")
    matcher = ctx.matcher or Matcher(ctx.players, slots=(K,))
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    known = {p.get("team") for p in ctx.players.values() if p.get("team")}
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, row)
    ranked = 0
    for p in data["players"]:
        if not isinstance(p, dict) or not isinstance(p.get("rank_ecr"), int | float):
            continue
        ranked += 1
        code = str(p.get("player_team_id") or "").upper()
        name, team = p.get("player_name"), team_of(code, teams)
        if code and code != FREE_AGENT and team not in known:
            unresolved[code] += 1  # a code missing from fp_teams.json (§6.1 code tables)
        m = matcher.match(NAME, source_id=str(p.get("player_id") or ""), name=name, team=team, slot=K,
                          crosswalk=CROSSWALK)  # fmt: skip
        match.add(m, name=name, team=team, slot=K)
        if m.pid is None:
            continue
        row = {"rank": p["rank_ecr"], "fp_id": str(p.get("player_id")), "name": name, "team": team,
               "ave": _float(p.get("rank_ave")), "std": _float(p.get("rank_std"))}  # fmt: skip
        prev = best.get(m.pid)
        if prev is not None:  # two rows on one Sleeper kicker: keep the higher-precedence match
            kept, dropped = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
            match.conflict(m.pid, *(f"{r[1]['name']} (FP {r[1]['fp_id']}, {r[0].how})" for r in (kept, dropped)))
            best[m.pid] = kept
        else:
            best[m.pid] = (m, row)
    info["depth"] = ranked
    for pid, (_m, r) in best.items():
        result.rows[pid] = {"ranks": {K: r["rank"]}, "ave": {K: r["ave"]}, "std": {K: r["std"]},
                            "fp_id": r["fp_id"], "team": r["team"]}  # fmt: skip
    result.universe = {K: {"complete": False, "depth": ranked}}
    result.lists = {K: {pid: r["rank"] for pid, (_m, r) in best.items()}}  # whatever his kickoff (§6.4)
    result.published_at = published(data)
    result.report |= {"match": match.summary(), "unresolved_teams": dict(unresolved)}
    if not result.rows:  # a list none of whose rows is a known kicker is never saved as an empty week
        result.status, result.error = SUSPECT, f"none of the {ranked} ranked rows matched a kicker"
        return result
    result.status = OK
    return result
