"""ESPN kicker projections from the owner's public league (SPEC.md §5.2 "ESPN adapter"; kickers added 2026-11-17).

The sibling of `espn_proj`: the same league endpoint and paging, with its own request (lineup slot 17, the
kickers) and its own match report, so nothing here can change what `espn_proj` reads or whether it is complete.
ESPN projects the whole scored line (made-FG yards, missed FGs, extra points made and missed), so nothing is
derived and the imputer is never called. A week entry with an empty `stats` is a placeholder: "no projection",
never 0. The universe is never complete: ESPN zero-fills no kicker.
"""

from __future__ import annotations

from collections import Counter

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import K
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .espn_proj import URL, _pages, projections

NAME = "espn_k"
__all__ = ["NAME", "URL", "fetch"]  # URL: the backfill runner's per-host request budget (§5.3)
SLOT_IDS = [17]  # lineup slot K
POSITIONS = {5: K}  # defaultPositionId
MAP_VERSION = 1
# ESPN stat id -> Sleeper key; checked against Sleeper's week-10 actuals for 29 of 29 kickers (2026-11-17)
STAT_MAP = {
    "214": "fgm_yds",  # yards of the made field goals: 30 / 45 / 54 a make in the three bands below
    "85": "fgmiss",
    "86": "xpm",
    "88": "xpmiss",
    "83": "fgm",
    "84": "fga",
    "87": "xpa",
    "80": "fgm_0_39",
    "77": "fgm_40_49",
    "74": "fgm_50p",
}
RAW_ONLY = ("81", "82", "78", "79", "75", "76", "215")  # attempts and misses per band, missed-FG yards: kept raw


def fetch(ctx: FetchContext) -> AdapterResult:
    """Uses ctx.client, season, week, now, players (the kickers) and matcher; never ctx.imputer."""
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
    players, pages, _short, status, error = _pages(ctx, url, SLOT_IDS)
    result.report = {"pages": pages, "players": len(players), "archive": ctx.archive}
    if status:
        result.status, result.error = status, error
        return result
    kickers = [p for p in players if POSITIONS.get(p.get("defaultPositionId")) == K]
    result.report["kickers"] = len(kickers)
    if not kickers:  # not a kicker response at all: never saved as an empty week
        result.status, result.error = SUSPECT, "no kicker rows in the response"
        return result
    lines = [projections(p, ctx.season) for p in kickers]
    if not any(ctx.week in by_week for by_week in lines):
        result.status, result.week = "not_yet_published", max((w for by_week in lines for w in by_week), default=None)
        return result

    matcher = ctx.matcher or Matcher(ctx.players, slots=(K,))
    match = MatchReport()
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, ESPN id, name, team, line) of the winning ESPN row
    seen: set[str] = set()
    unresolved: Counter[str] = Counter()
    with_projection = 0
    for p, by_week in zip(kickers, lines, strict=True):
        sid = str(p.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        code = str(p.get("proTeamId"))
        if code not in teams:
            unresolved[code] += 1
        name, team = p.get("fullName"), teams.get(code)
        line = {k: v for k, v in by_week.get(ctx.week, {}).items() if isinstance(v, int | float)}
        with_projection += any(k in STAT_MAP for k in line)
        m = matcher.match(NAME, source_id=sid, id_field="espn_id", name=name, team=team, slot=K)
        match.add(m, name=name, team=team, slot=K)
        if m.pid is None:
            continue
        row = (m, sid, name, team, line)
        if (prev := best.get(m.pid)) is not None:
            kept, dropped = (row, prev) if m.rank < prev[0].rank else (prev, row)
            match.conflict(m.pid, *(f"{r[2]} (ESPN {r[1]}, {r[0].how})" for r in (kept, dropped)))
            row = kept
        best[m.pid] = row

    for pid, (_m, sid, _name, team, line) in best.items():
        stats = {STAT_MAP[k]: round(v, 3) for k, v in line.items() if k in STAT_MAP}
        if not stats:  # no week entry, or an empty one (a bye, a backup, a free agent): a placeholder
            result.no_projection_ids.add(pid)
            continue
        result.rows[pid] = {  # 3 decimals, as `espn_proj` stores its lines
            "stats": stats,
            "imputed": [],  # ESPN projects every key the league scores
            "raw": {k: round(v, 3) for k, v in line.items() if k in STAT_MAP or k in RAW_ONLY},
            "map_version": MAP_VERSION,
            "espn_id": sid,
            "team": team,
            "opponent": None,
        }

    result.universe = {K: {"complete": False, "depth": None}}
    result.covered_keys = set(STAT_MAP.values())
    result.report |= {
        "with_projection": with_projection,
        "match": match.summary(),
        "duplicates": len(kickers) - len(seen),
        "unresolved_teams": dict(unresolved),
    }
    if not result.rows:  # the week is posted but no matched kicker has a line: likely caught mid-update
        result.status, result.error = SUSPECT, f"no week-{ctx.week} line has kicking stats"
        return result
    result.status = OK
    return result
