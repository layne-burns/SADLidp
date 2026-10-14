"""FantasyData's weekly DL, LB and DB rankings (SPEC.md §5.2 "FantasyData adapter", PR 5).

Each page has one table of 100 rows: rank, player (link `/nfl/<slug>-fantasy/<id>`), team, opponent, position
rank and FantasyData's own projection (its scoring, not SADL's: ignored). The week comes from the page title
("Week 6 DL Fantasy Football Rankings"); a page showing another week is that list `not_yet_published`. Players
match through Sleeper's `fantasy_data_id` first (§6.1 step 2). FantasyData keeps no archive: live weeks only.
"""

from __future__ import annotations

import re
from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from .base import OK, AdapterResult, FetchContext
from .htmltables import tables

NAME = "fantasydata"
HOST = "fantasydata.com"
URL = "https://fantasydata.com/nfl/rankings/{page}"
LISTS = ("DL", "LB", "DB")
TITLE = re.compile(r"<title>\s*Week\s+(\d+)\s+(DL|LB|DB)\b", re.IGNORECASE)
PLAYER = re.compile(r"/nfl/[a-z0-9-]+-fantasy/(\d+)")
MIN_ROWS = 20


def read_list(ctx: FetchContext, slot: str) -> tuple[list[list], str, str | None, int | None]:
    """(rows of (rank, name, fd id, team), status, error, week shown) for one list."""
    resp = ctx.client.get(URL.format(page=slot.lower()))
    if getattr(resp, "challenged", False) or resp.status in (403, 429, 503):
        return [], "blocked", "bot challenge" if getattr(resp, "challenged", False) else f"HTTP {resp.status}", None
    if not resp.ok:
        return [], "failed", resp.error or f"HTTP {resp.status}", None
    html = resp.text()
    m = TITLE.search(html)
    if not m or m.group(2).upper() != slot:
        return [], "parse_error", f"{slot}: no 'Week N {slot}' title", None
    week = int(m.group(1))
    if week != ctx.week:
        return [], "not_yet_published", f"{slot} list shows week {week}", week
    rows = []
    for t in tables(html):
        if not t.rows or [c.text.upper() for c in t.rows[0][:3]] != ["RK", "NAME", "TEAM"]:
            continue
        for r in t.rows[1:]:
            if len(r) < 3:
                continue
            fd = next((m.group(1) for h in r[1].hrefs if (m := PLAYER.search(h))), None)
            try:
                rows.append((int(r[0].text), r[1].text, fd, r[2].text.strip().upper()))
            except ValueError:
                continue
        break
    if len(rows) < MIN_ROWS:
        return [], "parse_error", f"{slot}: {len(rows)} rows", week
    return rows, OK, None, week


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    teams = load_alias(ctx.cfg, "fp_teams")  # JAC/WSH/LA spellings, shared with FantasyPros
    matcher = ctx.matcher or Matcher(ctx.players)
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    known = {p.get("team") for p in ctx.players.values() if p.get("team")}
    lists: dict[str, dict] = {}
    best: dict[str, dict] = {}
    for slot in LISTS:
        rows, status, error, week = read_list(ctx, slot)
        lists[slot] = {"status": status, "error": error, "players": len(rows), "week": week}
        if status == "blocked":
            break
        if status != OK:
            continue
        for rank, name, fd, code in rows:
            team = teams.get(code, code) if code else None
            if team and team not in known:
                unresolved[code] += 1
            m = matcher.match(NAME, source_id=fd, id_field="fantasy_data_id", name=name, team=team, slot=slot)
            match.add(m, name=name, team=team, slot=slot)
            if m.pid is None:
                continue
            row = {"rank": rank, "fd_id": fd, "name": name, "team": team}
            prev = best.setdefault(m.pid, {}).get(slot)
            if prev is not None:
                kept, other = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
                match.conflict(m.pid, *(f"{r[1]['name']} (FD {r[1]['fd_id']}, {r[0].how})" for r in (kept, other)))
                best[m.pid][slot] = kept
            else:
                best[m.pid][slot] = (m, row)
        lists[slot]["depth"] = len(rows)

    read = [s for s in LISTS if lists.get(s, {}).get("status") == OK]
    for pid, by_list in best.items():
        first = next(iter(by_list.values()))[1]
        result.rows[pid] = {"ranks": {s: r["rank"] for s, (_m, r) in by_list.items()}, "fd_id": first["fd_id"],
                            "team": first["team"]}  # fmt: skip
    result.universe = {s: {"complete": False, "depth": lists[s].get("depth")} for s in read}
    result.lists = {s: {pid: by[s][1]["rank"] for pid, by in best.items() if s in by} for s in read}
    result.report = {"lists": lists, "match": match.summary(), "unresolved_teams": dict(unresolved),
                     "archive": ctx.archive}  # fmt: skip
    if read:
        result.status = OK
        return result
    statuses = {v["status"] for v in lists.values()}
    for status in ("blocked", "parse_error", "failed", "not_yet_published"):
        if status in statuses:
            result.status = status
            break
    result.error = "; ".join(v["error"] for v in lists.values() if v.get("error")) or None
    if result.status == "not_yet_published":
        weeks = [v["week"] for v in lists.values() if v.get("week")]
        result.week = max(weeks) if weeks else None
    return result
