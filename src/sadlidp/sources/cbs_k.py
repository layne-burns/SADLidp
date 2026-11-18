"""CBS Sports weekly kicker projections, free and logged out (SPEC.md §5.2; kickers added 2026-11-17).

One server-rendered page per week, read at its plain URL only (robots.txt disallows the table's sort links).
The page ignores the week in its URL and always serves the current week, so the week is read from the title
("Week 11 Proj ...") and any other week is `not_yet_published`; there is no archive. Each row gives field
goals made and attempted, makes per distance band, and extra points made and attempted. CBS publishes no
yards and no misses, so three keys the league scores are derived and marked in the row's `imputed` list:
`fgmiss = fga − fgm`, `xpmiss = xpa − xpm`, and `fgm_yds` = the makes in each band times that band's mean made
distance (`[kicker.yards_per_make]`). Numbers come to one decimal, so the bands need not add up to `fgm`.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser

from ..config import load_alias
from ..kicker import BUCKETS, line_keys, with_fgmiss, yards_per_make
from ..matching import Matcher, MatchReport
from ..positions import K
from .base import OK, SUSPECT, AdapterResult, FetchContext

NAME = "cbs_k"
HOST = "www.cbssports.com"
URL = "https://www.cbssports.com/fantasy/football/stats/K/{season}/{week}/projections/nonppr/"
TITLE = re.compile(r"<title>\s*Week\s+(\d+)\s+Proj\b", re.IGNORECASE)
PLAYER = re.compile(r"/players/(\d+)/")
# column (the first word of its header cell) -> Sleeper key, in page order; the attempts per band are not kept
COLUMNS = {
    "gp": "gp",
    "fgm": "fgm",
    "fga": "fga",
    "1-19": "fgm_0_19",
    "20-29": "fgm_20_29",
    "30-39": "fgm_30_39",
    "40-49": "fgm_40_49",
    "50+": "fgm_50p",
    "xpm": "xpm",
    "xpa": "xpa",
}
DERIVED = ["fgm_yds", "fgmiss", "xpmiss"]
MIN_ROWS = 10  # a table with fewer kickers is not the week's projections (a full week has 24 to 32)


class _Table(HTMLParser):
    """The stat table (`table.TableBase-table`): `header` holds the first word of each cell of its last header
    row, `rows` one dict per body row: the number cells' text, and the name, position, team and link from the
    player cell's `span.CellPlayerName--long` (the cell also holds a short form: "K. Fairbairn")."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.header: list[str] = []
        self.rows: list[dict] = []
        self._state = 0  # 0 before the table, 1 inside it, 2 after it
        self._row: dict | None = None
        self._cell: list[str] | None = None
        self._long = 0  # span depth inside the long-name span
        self._field: str | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        cls = attr.get("class") or ""
        if tag == "table" and self._state == 0 and "TableBase-table" in cls:
            self._state = 1
        if self._state != 1:
            return
        if tag == "tr":
            self._row = {"head": [], "cells": [], "name": "", "pos": "", "team": "", "href": None}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "span" and self._row is not None:
            if self._long:
                self._long += 1
                self._field = {"CellPlayerName-position": "pos", "CellPlayerName-team": "team"}.get(cls.strip())
            elif "CellPlayerName--long" in cls:
                self._long = 1
        elif tag == "a" and self._long and self._row is not None:
            self._field, self._row["href"] = "name", attr.get("href")

    def handle_endtag(self, tag):
        if self._state != 1:
            return
        if tag == "table":
            self._state = 2
        elif tag == "span" and self._long:
            self._long -= 1
            self._field = None
        elif tag == "a":
            self._field = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell).replace("\xa0", " ")).strip()
            self._row["head" if tag == "th" else "cells"].append(text)
            self._cell, self._long, self._field = None, 0, None
        elif tag == "tr" and self._row is not None:
            if self._row["cells"]:
                for key in ("name", "pos", "team"):
                    self._row[key] = re.sub(r"\s+", " ", self._row[key]).strip()
                self.rows.append(self._row)
            elif self._row["head"]:
                self.header = [(h.split() or [""])[0].lower() for h in self._row["head"]]
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._field and self._row is not None:
            self._row[self._field] += data


def read_page(ctx: FetchContext) -> tuple[list[dict], str, str | None, int | None, int]:
    """(rows of {name, team, cbs_id, line}, status, error, week shown, rows that could not be read)."""
    resp = ctx.client.get(URL.format(season=ctx.season, week=ctx.week))
    if getattr(resp, "challenged", False) or resp.status in (403, 429, 503):
        why = "bot challenge" if getattr(resp, "challenged", False) else f"HTTP {resp.status}"
        return [], "blocked", why, None, 0
    if not resp.ok:
        return [], "failed", resp.error or f"HTTP {resp.status}", None, 0
    html = resp.text()
    m = TITLE.search(html)
    if not m:
        return [], "parse_error", "no 'Week N Proj' title", None, 0
    week = int(m.group(1))
    if week != ctx.week:  # the page serves the current week whatever the URL asks for
        return [], "not_yet_published", f"page shows week {week}", week, 0
    table = _Table()
    table.feed(html)
    table.close()
    missing = [c for c in COLUMNS if c not in table.header]
    if missing:
        return [], "parse_error", f"no stat table with the columns {', '.join(missing)}", week, 0
    at = {c: table.header.index(c) for c in COLUMNS}
    rows, unread = [], 0
    for r in table.rows:
        try:
            line = {key: float(r["cells"][at[c]]) for c, key in COLUMNS.items()}
        except (ValueError, IndexError):
            line = None
        if line is None or not r["name"] or r["pos"].upper() != K:
            unread += 1
            continue
        cbs_id = PLAYER.search(r["href"] or "")
        rows.append({"name": r["name"], "team": r["team"].upper() or None, "line": line,
                     "cbs_id": cbs_id.group(1) if cbs_id else None})  # fmt: skip
    if len(rows) < MIN_ROWS:
        return [], "parse_error", f"{len(rows)} rows", week, unread
    return rows, OK, None, week, unread


def derive(line: dict, yards: dict[str, float]) -> dict:
    """The published line plus the three keys the league scores and CBS leaves out (`DERIVED`)."""
    out = dict(with_fgmiss(line)[0])
    out["xpmiss"] = round(max(0.0, line["xpa"] - line["xpm"]), 4)
    out["fgm_yds"] = round(sum(line[f"fgm_{b}"] * yards[b] for b in BUCKETS), 2)
    return out


def fetch(ctx: FetchContext) -> AdapterResult:
    """Uses ctx.client, season, week, now, players (the kickers), matcher and cfg["kicker"]; never ctx.imputer."""
    result = AdapterResult(name=NAME, kind="projection", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    rows, status, error, week, unread = read_page(ctx)
    result.report = {"players": len(rows), "unread_rows": unread, "archive": ctx.archive}
    if status != OK:
        result.status, result.error = status, error
        if status == "not_yet_published":
            result.week = week
        return result
    teams = load_alias(ctx.cfg, "fp_teams")  # the JAC/WSH/LA spellings, shared with FantasyPros
    yards = yards_per_make(ctx.cfg)
    matcher = ctx.matcher or Matcher(ctx.players, slots=(K,))
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    known = {p.get("team") for p in ctx.players.values() if p.get("team")}
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, row)
    for row in rows:
        code = row["team"]
        team = teams.get(code, code) if code else None
        if team and team not in known:
            unresolved[code] += 1
        m = matcher.match(NAME, name=row["name"], team=team, slot=K)
        match.add(m, name=row["name"], team=team, slot=K)
        if m.pid is None:
            continue
        row = {**row, "team": team}
        prev = best.get(m.pid)
        if prev is not None:
            kept, other = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
            match.conflict(m.pid, *(f"{r[1]['name']} (CBS {r[1]['cbs_id']}, {r[0].how})" for r in (kept, other)))
            best[m.pid] = kept
        else:
            best[m.pid] = (m, row)
    for pid, (_m, row) in best.items():
        line = row["line"]
        if not line["gp"]:  # listed without a game: a placeholder, never 0
            result.no_projection_ids.add(pid)
            continue
        result.rows[pid] = {"stats": derive(line, yards), "imputed": list(DERIVED), "cbs_id": row["cbs_id"],
                            "team": row["team"], "opponent": None}  # fmt: skip
        result.covered_keys.update(k for k in line_keys([line]) if k in line)
    result.universe = {K: {"complete": False, "depth": None}}
    result.report |= {"match": match.summary(), "unresolved_teams": dict(unresolved), "imputed": list(DERIVED)}
    if not result.rows:  # a table none of whose rows is a known kicker is never saved as an empty week
        result.status, result.error = SUSPECT, f"none of the {len(rows)} rows matched a kicker with a game"
        return result
    result.status = OK
    return result
