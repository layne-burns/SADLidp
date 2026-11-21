"""FantasyData's weekly kicker rankings (SPEC.md §5.2 "FantasyData adapter"; kickers added 2026-11-17).

The sibling of `fantasydata`: the same page family (`/nfl/rankings/k`), one table (RK, NAME, TEAM, OPP, POS RK,
PROJECTED) and the week in the title ("Week 11 K Fantasy Football Rankings"); a page showing another week is
`not_yet_published`. The page also lists backup kickers, each with a projection of 0.0: those rows are dropped
and the rest re-ranked 1..n in page order, so two kickers of one team never both hold a rank. PROJECTED is
FantasyData's own scoring and is used for nothing else. Players match through Sleeper's `fantasy_data_id`
first (§6.1 step 2). FantasyData keeps no archive: live weeks only.
"""

from __future__ import annotations

import re
from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import K
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .fantasydata import HOST, PLAYER
from .fantasydata import URL as PAGES
from .htmltables import tables

NAME = "fantasydata_k"
URL = PAGES.format(page="k")
__all__ = ["HOST", "NAME", "URL", "fetch"]
TITLE = re.compile(r"<title>\s*Week\s+(\d+)\s+K\b", re.IGNORECASE)
HEADER = ["RK", "NAME", "TEAM"]
PROJECTED = "PROJECTED"
MIN_ROWS = 10  # a table with fewer projected kickers is not the week's ranking (a full week has 24 to 32)


def read_list(ctx: FetchContext) -> tuple[list[tuple], str, str | None, int | None, int]:
    """(rows of (rank, name, fd id, team), status, error, week shown, backup rows dropped): the projected
    kickers in page order, ranked 1..n."""
    resp = ctx.client.get(URL)
    if getattr(resp, "challenged", False) or resp.status in (403, 429, 503):
        why = "bot challenge" if getattr(resp, "challenged", False) else f"HTTP {resp.status}"
        return [], "blocked", why, None, 0
    if not resp.ok:
        return [], "failed", resp.error or f"HTTP {resp.status}", None, 0
    html = resp.text()
    m = TITLE.search(html)
    if not m:
        return [], "parse_error", "no 'Week N K' title", None, 0
    week = int(m.group(1))
    if week != ctx.week:
        return [], "not_yet_published", f"K list shows week {week}", week, 0
    rows, dropped = [], 0
    for t in tables(html):
        head = [c.text.upper() for c in t.rows[0]] if t.rows else []
        if head[:3] != HEADER:
            continue
        if PROJECTED not in head:  # without it a backup can't be told from a starter
            return [], "parse_error", "K: no PROJECTED column", week, 0
        at = head.index(PROJECTED)
        for r in t.rows[1:]:
            if len(r) <= at or not r[0].text.isdigit():
                continue
            try:
                projected = float(r[at].text)
            except ValueError:
                projected = 0.0
            if projected <= 0:  # a backup, or a kicker without a game: not ranked
                dropped += 1
                continue
            fd = next((hit.group(1) for h in r[1].hrefs if (hit := PLAYER.search(h))), None)
            rows.append((len(rows) + 1, r[1].text, fd, r[2].text.strip().upper()))
        break
    if len(rows) < MIN_ROWS:
        return [], "parse_error", f"K: {len(rows)} rows", week, dropped
    return rows, OK, None, week, dropped


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    rows, status, error, week, dropped = read_list(ctx)
    info = {"status": status, "error": error, "players": len(rows), "week": week, "dropped_unprojected": dropped}
    result.report = {"lists": {K: info}, "archive": ctx.archive}
    if status != OK:
        result.status, result.error = status, error
        if status == "not_yet_published":
            result.week = week
        return result
    teams = load_alias(ctx.cfg, "fp_teams")  # JAC/WSH/LA spellings, shared with FantasyPros
    matcher = ctx.matcher or Matcher(ctx.players, slots=(K,))
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    known = {p.get("team") for p in ctx.players.values() if p.get("team")}
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, row)
    for rank, name, fd, code in rows:
        team = teams.get(code, code) if code else None
        if team and team not in known:
            unresolved[code] += 1
        m = matcher.match(NAME, source_id=fd, id_field="fantasy_data_id", name=name, team=team, slot=K)
        match.add(m, name=name, team=team, slot=K)
        if m.pid is None:
            continue
        row = {"rank": rank, "fd_id": fd, "name": name, "team": team}
        prev = best.get(m.pid)
        if prev is not None:
            kept, other = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
            match.conflict(m.pid, *(f"{r[1]['name']} (FD {r[1]['fd_id']}, {r[0].how})" for r in (kept, other)))
            best[m.pid] = kept
        else:
            best[m.pid] = (m, row)
    info["depth"] = len(rows)
    for pid, (_m, r) in best.items():
        result.rows[pid] = {"ranks": {K: r["rank"]}, "fd_id": r["fd_id"], "team": r["team"]}
    result.universe = {K: {"complete": False, "depth": len(rows)}}
    result.lists = {K: {pid: r["rank"] for pid, (_m, r) in best.items()}}
    result.report |= {"match": match.summary(), "unresolved_teams": dict(unresolved)}
    if not result.rows:  # a list none of whose rows is a known kicker is never saved as an empty week
        result.status, result.error = SUSPECT, f"none of the {len(rows)} ranked rows matched a kicker"
        return result
    result.status = OK
    return result
