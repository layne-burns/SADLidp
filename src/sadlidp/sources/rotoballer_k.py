"""RotoBaller's weekly kicker rankings article (SPEC.md §5.2 "RotoBaller adapter"; kickers added 2026-11-17).

The sibling of `rotoballer`: kickers get their own weekly article ("Week N Kicker Streamers, Starters, and
Rankings"), found through its own search feed and its own link pattern. The pattern is tight on purpose: the
site also posts a Sunday "best kicker pickups" piece and a "weekend updates" piece whose slugs start the same
way, and neither is this ranking. The article has one table (tier, rank, name, team abbreviation, ...); a
trailing "*" on a name points at a note under the table and is dropped. `published_at` is the article's
JSON-LD `datePublished`, and a kicker whose game kicked off before it is dropped from the rows (the article
came out after his game) while staying in the whole list, exactly as in the IDP article. Past weeks come from
the same feed, so it backfills; RotoBaller re-dates an article it updates, so a re-dated week keeps only the
kickers who had not played by then.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import K
from .base import OK, SUSPECT, AdapterResult, FetchContext
from .htmltables import tables
from .rotoballer import FLAG, HOST, find_article, published

NAME = "rotoballer_k"
RSS = "https://www.rotoballer.com/?s=kicker+streamers+tiers&feed=rss2&paged={page}"
URL = RSS.format(page=1)  # for the backfill runner's host budget
__all__ = ["HOST", "NAME", "URL", "fetch"]
LINK = re.compile(r"/week-(\d+)-kicker-streamers-starters-and-rankings-(\d{4})-[a-z0-9-]*/\d+/?$")
HEADER = ("kicker tier", "kicker rank", "kicker name", "team")  # the table's first four header cells
MIN_ROWS = 10  # a table shorter than this is not the ranking (the article lists every team playing: 24 to 32)
NOTE = re.compile(r"\s*\*+\s*$")  # "Charlie Smyth*": a pointer to a note under the table


def _key(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()  # "Kicker<br>Tier" reads "Kicker Tier" or "KickerTier"


def ranking_table(html: str) -> tuple[str, list[list[str]], str | None]:
    """(status, rows of [rank, name, team], error): the one table whose header is the kicker ranking's."""
    want = [_key(h) for h in HEADER]
    for t in tables(html):
        if not t.rows or [_key(c.text) for c in t.rows[0][:4]] != want:
            continue
        rows = [[c.text for c in r[1:4]] for r in t.rows[1:] if len(r) >= 4]
        if len(rows) < MIN_ROWS:
            return "parse_error", [], f"K: {len(rows)} rows"
        return OK, rows, None
    return "parse_error", [], "K: no kicker ranking table"


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    url, status, error = find_article(ctx, RSS, LINK)
    result.report = {"archive": ctx.archive, "article": url}
    if status != OK:
        result.status, result.error = status, error
        return result
    resp = ctx.client.get(url)
    if not resp.ok:
        result.status = "blocked" if resp.status in (403, 429, 503) else "failed"
        result.error = f"article: {resp.error or f'HTTP {resp.status}'}"
        return result
    html = resp.text()
    result.published_at = published(html)
    cutoff = datetime.fromisoformat(result.published_at) if result.published_at else None
    st, rows, err = ranking_table(html)
    info = {"status": st, "error": err, "players": len(rows)}
    result.report["lists"] = {K: info}
    if st != OK:
        result.status, result.error = st, err
        return result
    teams = load_alias(ctx.cfg, "rotoballer_teams")
    matcher = ctx.matcher or Matcher(ctx.players, slots=(K,))
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    best: dict[str, tuple] = {}  # Sleeper id -> (Match, row)
    whole: dict[str, int] = {}
    ranked = dropped = 0
    for rank_text, player, team_text in rows:
        try:
            rank = int(rank_text)
        except ValueError:
            continue
        ranked += 1
        player = NOTE.sub("", player)
        flag = FLAG.search(player)
        name = NOTE.sub("", FLAG.sub("", player)).strip()
        team = teams.get(team_text.strip().lower())
        if team is None:
            unresolved[team_text] += 1
        m = matcher.match(NAME, name=name, team=team, slot=K)
        match.add(m, name=name, team=team, slot=K)
        if m.pid is None:
            continue
        whole.setdefault(m.pid, rank)  # the whole list builds the V curve (§6.4)
        kick = ctx.kickoff_of(m.pid) if ctx.kickoff_of else None
        if cutoff is not None and kick is not None and kick <= cutoff:
            dropped += 1  # the article came out after his game: never a pre-game rank (§5.3)
            continue
        row = {"rank": rank, "name": name, "team": team, "flag": flag.group(1).upper() if flag else None}
        prev = best.get(m.pid)
        if prev is not None:
            kept, other = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
            match.conflict(m.pid, *(f"{r[1]['name']} ({r[0].how})" for r in (kept, other)))
            best[m.pid] = kept
        else:
            best[m.pid] = (m, row)
    info["depth"] = ranked
    for pid, (_m, r) in best.items():
        result.rows[pid] = {"ranks": {K: r["rank"]}, "flags": {K: r["flag"]} if r["flag"] else {}, "team": r["team"]}
    result.universe = {K: {"complete": False, "depth": ranked}}
    result.lists = {K: whole}  # kickers who already played included (§6.4)
    result.report |= {"match": match.summary(), "unresolved_teams": dict(unresolved), "dropped_after_kickoff": dropped}
    if not whole:  # a table none of whose rows is a known kicker is never saved as an empty week
        result.status, result.error = SUSPECT, f"none of the {ranked} ranked rows matched a kicker"
        return result
    result.status = OK
    return result
