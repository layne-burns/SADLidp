"""ESPN projections from the owner's public IDP league, read logged out (SPEC.md §5.2 "ESPN adapter", §13).

Pages of 400 IDPs sorted by percent owned until a short page (at most 6); any page error fails the whole fetch.
ESPN stat ids become Sleeper keys through STAT_MAP (§13) and the keys ESPN doesn't project are imputed (§6.2).
The result is complete only if paging ended on a short page, no ESPN id repeats and ≥ 98% of the players with
an NFL team matched a Sleeper player; only a complete result lists the Sleeper ids it can zero-fill (§5.2).
"""

from __future__ import annotations

import json
from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import IDP_SLOTS, slot_of
from .base import OK, SUSPECT, AdapterResult, FetchContext

NAME = "espn_proj"
URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
SLOT_IDS = [10, 11, 14, 15]  # lineup slots LB, DL, DB, DP
PAGE_SIZE = 400
MAX_PAGES = 6
MIN_MATCH_RATE = 0.98
MIN_COVERAGE = 0.6  # live 2026-10-12: 0.82 DL, 0.82 LB, 0.73 DB (the gap is stale Sleeper records)
MAP_VERSION = 1
STAT_MAP = {
    "108": "idp_tkl_solo",
    "107": "idp_tkl_ast",
    "109": "idp_tkl",
    "99": "idp_sack",
    "95": "idp_int",
    "106": "idp_ff",
    "96": "idp_fum_rec",
    "113": "idp_pass_def",
    "97": "idp_blk_kick",  # provisional (§13)
}
RAW_ONLY = ("94", "98", "103", "105")  # likely defensive TDs and safeties: kept raw, too rare to confirm (§13)
POSITIONS = {9: "DL", 10: "DL", 11: "LB", 12: "DB", 13: "DB"}  # defaultPositionId DT, DE, LB, CB, S


def filter_header(offset: int) -> str:
    """The X-Fantasy-Filter for one page: the IDP lineup slots, most owned first."""
    players = {
        "filterSlotIds": {"value": SLOT_IDS},
        "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    return json.dumps({"players": players})


def projections(player: dict, season: int) -> dict[int, dict]:
    """The player's one-week projected lines this season by week, {} for an empty line (statSourceId 1 is
    projected, 0 actual; statSplitTypeId 1 is a single week)."""
    out = {}
    for s in player.get("stats") or []:
        if not isinstance(s, dict) or s.get("seasonId", season) != season:
            continue
        if s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1 and isinstance(s.get("scoringPeriodId"), int):
            out[s["scoringPeriodId"]] = s["stats"] if isinstance(s.get("stats"), dict) else {}
    return out


def _pages(ctx: FetchContext, url: str) -> tuple[list[dict], int, bool, str | None, str | None]:
    """(players, pages read, ended on a short page, status, error): any bad page fails them all (§5.2)."""
    players: list[dict] = []
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        where = f"page {page + 1} (offset {offset})"
        resp = ctx.client.get(url, headers={"X-Fantasy-Filter": filter_header(offset)})
        if getattr(resp, "challenged", False):
            return players, page + 1, False, "blocked", f"{where}: bot challenge"
        if not resp.ok:
            return players, page + 1, False, "failed", f"{where}: {resp.error or f'HTTP {resp.status}'}"
        try:
            batch = resp.json().get("players")
        except (ValueError, AttributeError):
            batch = None
        if not isinstance(batch, list):
            return players, page + 1, False, "parse_error", f"{where}: no players list"
        players += [p for e in batch if isinstance(e, dict) and isinstance(p := e.get("player"), dict) and "id" in p]
        if len(batch) < PAGE_SIZE:
            return players, page + 1, True, None, None
    return players, MAX_PAGES, False, None, None


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="projection", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    league = ctx.cfg.get("espn_league_id")
    if not league:
        result.status, result.error = "skipped", "no espn.league_id configured"
        return result
    teams = load_alias(ctx.cfg, "espn_teams")
    if not teams:
        result.error = "config/aliases/espn_teams.json is missing"
        return result
    url = URL.format(season=ctx.season, league=league) + f"?view=kona_player_info&scoringPeriodId={ctx.week}"
    players, pages, short, status, error = _pages(ctx, url)
    result.report = {"pages": pages, "players": len(players), "archive": ctx.archive}
    if status:
        result.status, result.error = status, error
        return result
    lines = [projections(p, ctx.season) for p in players]
    if not any(ctx.week in by_week for by_week in lines):
        result.status, result.week = "not_yet_published", max((w for by_week in lines for w in by_week), default=None)
        return result

    matcher = ctx.matcher or Matcher(ctx.players)
    match = MatchReport()
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, ESPN id, name, team, line) of the winning ESPN row
    seen: set[str] = set()
    unresolved: Counter[str] = Counter()
    with_projection = 0
    for p, by_week in zip(players, lines, strict=True):
        sid = str(p.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        code = str(p.get("proTeamId"))
        if code not in teams:
            unresolved[code] += 1
        name, team, slot = p.get("fullName"), teams.get(code), POSITIONS.get(p.get("defaultPositionId"))
        line = {k: v for k, v in by_week.get(ctx.week, {}).items() if isinstance(v, int | float)}
        with_projection += any(k in STAT_MAP for k in line)
        m = matcher.match(NAME, source_id=sid, id_field="espn_id", name=name, team=team, slot=slot)
        match.add(m, name=name, team=team, slot=slot)
        if m.pid is None:
            continue
        row = (m, sid, name, team, line)
        if (prev := best.get(m.pid)) is not None:
            kept, dropped = (row, prev) if m.rank < prev[0].rank else (prev, row)
            match.conflict(m.pid, *(f"{r[2]} (ESPN {r[1]}, {r[0].how})" for r in (kept, dropped)))
            row = kept
        best[m.pid] = row

    covered = set(STAT_MAP.values())
    imputed_counts: Counter[str] = Counter()
    for pid, (_m, sid, _name, team, line) in best.items():
        stats = {STAT_MAP[k]: v for k, v in line.items() if k in STAT_MAP}
        if not stats:  # no line, or a line without IDP stat ids
            result.no_projection_ids.add(pid)
            continue
        imputed: list[str] = []
        if ctx.imputer is not None:
            stats, imputed = ctx.imputer.fill(pid, stats, covered)
        imputed_counts.update(imputed)
        result.rows[pid] = {  # 3 decimals: well below any scoring step, and a third of the file size
            "stats": {k: round(v, 3) for k, v in stats.items()},
            "imputed": list(imputed),
            "raw": {k: round(v, 3) for k, v in line.items() if k in STAT_MAP or k in RAW_ONLY},
            "map_version": MAP_VERSION,
            "espn_id": sid,
            "team": team,
            "opponent": None,
        }

    summary = match.summary()
    rate, repeats = summary["teamed_match_rate"], len(players) - len(seen)
    why = []
    if not short:
        why.append(f"paging stopped at the {MAX_PAGES}-page cap")
    if repeats:
        why.append(f"{repeats} repeated ESPN ids")
    if rate is None or rate < MIN_MATCH_RATE:
        why.append(
            f"matched {'no' if rate is None else f'{rate:.1%} of'} players with an NFL team (< {MIN_MATCH_RATE:.0%})"
        )
    # coverage per slot: a short or skewed response must not zero-fill the players it left out (§5.2)
    got = {str(p.get("id")) for p in players}
    coverage = {}
    for slot in IDP_SLOTS:
        expect = [
            str(p["espn_id"])
            for p in ctx.players.values()
            if slot_of(p) == slot and p.get("espn_id") not in (None, "") and p.get("active") and p.get("team")
        ]
        coverage[slot] = sum(e in got for e in expect) / len(expect) if expect else None
    slot_ok = {s: not why and (c is None or c >= MIN_COVERAGE) for s, c in coverage.items()}
    for s, c in coverage.items():
        if not why and c is not None and c < MIN_COVERAGE:
            why.append(f"{s}: only {c:.0%} of Sleeper's {s}s with an ESPN id came back (< {MIN_COVERAGE:.0%})")
    complete = all(slot_ok.values())
    zero_fill: dict[str, list[str]] = {slot: [] for slot in IDP_SLOTS}
    for pid, p in ctx.players.items():  # an incomplete slot zero-fills nobody
        if slot_ok.get(slot_of(p)) and p.get("espn_id") not in (None, ""):
            zero_fill[slot_of(p)].append(pid)
    result.universe = {
        s: {"complete": slot_ok[s], "depth": None, "zero_fill_ids": sorted(ids)} for s, ids in zero_fill.items()
    }
    result.covered_keys = covered
    result.report |= {
        "with_projection": with_projection,
        "match": summary,
        "complete": complete,
        "coverage": {k: round(v, 3) if v is not None else None for k, v in coverage.items()},
        "why_incomplete": "; ".join(why) or None,
        "duplicates": repeats,
        "unresolved_teams": dict(unresolved),
        "imputed": dict(imputed_counts),
    }
    if not result.rows:  # the week is posted but no line carries IDP stats: likely caught mid-update
        result.status, result.error = SUSPECT, f"no week-{ctx.week} line has IDP stats"
        return result
    result.status = OK
    return result
