"""FantasyPros weekly expert consensus rankings, free and logged out (SPEC.md §5.2 "FantasyPros adapter", D9).

The DL, LB and DB pages embed an `ecrData` JSON object: the week, the expert count and the ranked players.
Each list is read and checked on its own: a list that isn't posted for the target week (or can't be read)
is reported per list, and the sync keeps that list's stored ranks (list-scoped merge, §5.2). Ranks become
SADL points later, in the consensus step (rankmap.py, §6.4). Past weeks are served through `?week=N`.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import POSITION_TO_SLOT
from .base import OK, AdapterResult, FetchContext

NAME = "fp_ecr"
HOST = "www.fantasypros.com"
URL = "https://www.fantasypros.com/nfl/rankings/{page}.php?week={week}"
LISTS = ("DL", "LB", "DB")  # the combined IDP page repeats the same experts' ranks, so it isn't read (§5.2)
CROSSWALK = "fantasypros"  # DynastyProcess column (§6.1 step 3)
MARKER = "var ecrData = "
FREE_AGENT = "FA"


def parse_ecr(text: str) -> dict | None:
    """The page's embedded `ecrData` object, or None if the page has none."""
    at = text.find(MARKER)
    if at < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text, at + len(MARKER))
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("players"), list) else None


def read_page(ctx: FetchContext, page: str) -> tuple[dict | None, str, str | None]:
    """(ecrData, status, error) for one ranking page of the target week: status is ok, or why it isn't."""
    resp = ctx.client.get(URL.format(page=page, week=ctx.week))
    if getattr(resp, "challenged", False) or resp.status in (403, 429, 503):
        return None, "blocked", "bot challenge" if getattr(resp, "challenged", False) else f"HTTP {resp.status}"
    if not resp.ok:
        return None, "failed", resp.error or f"HTTP {resp.status}"
    data = parse_ecr(resp.text())
    if data is None:
        return None, "parse_error", "no ecrData on the page"
    try:
        week, year = int(data.get("week")), int(data.get("year"))
    except (TypeError, ValueError):
        return None, "parse_error", "ecrData has no week or year"
    if (year, week) != (ctx.season, ctx.week) or not data["players"]:
        return data, "not_yet_published", f"{page.upper()} list shows week {week}, {len(data['players'])} players"
    return data, OK, None


def published(data: dict) -> str | None:
    """`last_updated_ts` (epoch seconds) as ISO time, if present."""
    ts = data.get("last_updated_ts")
    return datetime.fromtimestamp(int(ts), UTC).isoformat() if isinstance(ts, int | float) and ts > 0 else None


def team_of(code, teams: dict[str, str]) -> str | None:
    code = str(code or "").strip().upper()
    return None if not code or code == FREE_AGENT else teams.get(code, code)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    teams = load_alias(ctx.cfg, "fp_teams")
    matcher = ctx.matcher or Matcher(ctx.players)
    match = MatchReport()
    lists: dict[str, dict] = {}
    best: dict[str, dict] = {}  # Sleeper id -> {list: (Match, row)}
    unresolved: Counter[str] = Counter()
    known = {p.get("team") for p in ctx.players.values() if p.get("team")}
    stamps = []
    for slot in LISTS:
        data, status, error = read_page(ctx, slot.lower())
        lists[slot] = {"status": status, "error": error, "players": len((data or {}).get("players") or [])}
        if data:
            lists[slot].update(week=_int(data.get("week")), experts=data.get("total_experts"))
        if status != OK:
            if status == "blocked":
                break  # the host is blocked for the rest of the run (§7): the other lists can't be read either
            continue
        if stamp := published(data):
            stamps.append(stamp)
        ranked = 0
        for p in data["players"]:
            if not isinstance(p, dict) or not isinstance(p.get("rank_ecr"), int | float):
                continue
            ranked += 1
            code = str(p.get("player_team_id") or "").upper()
            if code and code != FREE_AGENT and team_of(code, teams) not in known:
                unresolved[code] += 1  # a code missing from fp_teams.json (§6.1 code tables)
            name, team = p.get("player_name"), team_of(code, teams)
            pslot = POSITION_TO_SLOT.get(str(p.get("player_position_id") or "").upper())
            m = matcher.match(NAME, source_id=str(p.get("player_id") or ""), name=name, team=team, slot=pslot,
                              crosswalk=CROSSWALK)  # fmt: skip
            match.add(m, name=name, team=team, slot=pslot)
            if m.pid is None:
                continue
            row = {"rank": p["rank_ecr"], "fp_id": str(p.get("player_id")), "name": name, "team": team,
                   "ave": _float(p.get("rank_ave")), "std": _float(p.get("rank_std"))}  # fmt: skip
            prev = best.setdefault(m.pid, {}).get(slot)
            if prev is not None:  # two rows of one list on one Sleeper player: keep the higher-precedence match
                kept, dropped = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
                match.conflict(m.pid, *(f"{r[1]['name']} (FP {r[1]['fp_id']}, {r[0].how})" for r in (kept, dropped)))
                best[m.pid][slot] = kept
            else:
                best[m.pid][slot] = (m, row)
        lists[slot]["depth"] = ranked

    read = [s for s in LISTS if lists.get(s, {}).get("status") == OK]
    for pid, by_list in best.items():
        first = next(iter(by_list.values()))[1]
        result.rows[pid] = {
            "ranks": {s: r["rank"] for s, (_m, r) in by_list.items()},
            "ave": {s: r["ave"] for s, (_m, r) in by_list.items()},
            "std": {s: r["std"] for s, (_m, r) in by_list.items()},
            "fp_id": first["fp_id"],
            "team": first["team"],
        }
    result.universe = {s: {"complete": False, "depth": lists[s].get("depth")} for s in read}
    result.lists = {s: {} for s in read}  # every matched player of each list read, whatever his kickoff (§6.4)
    for pid, by_list in best.items():
        for s, (_m, r) in by_list.items():
            result.lists[s][pid] = r["rank"]
    result.published_at = min(stamps) if stamps else None
    result.report = {
        "lists": lists,
        "match": match.summary(),
        "unresolved_teams": dict(unresolved),
        "archive": ctx.archive,
    }
    if read:
        result.status = OK
        return result
    statuses = {lists[s]["status"] for s in LISTS if s in lists}
    for status in ("blocked", "parse_error", "failed", "not_yet_published"):
        if status in statuses:
            result.status = status
            break
    result.error = "; ".join(f"{s}: {v['error']}" for s, v in lists.items() if v.get("error")) or None
    if result.status == "not_yet_published":
        weeks = [w for s in LISTS if (w := lists.get(s, {}).get("week"))]
        result.week = max(weeks) if weeks else None
    return result
