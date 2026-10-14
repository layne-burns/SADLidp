"""RotoBaller's weekly IDP rankings article (SPEC.md §5.2 "RotoBaller adapter", PR 5).

The article is found through the site's search RSS (the newest few pages), then its three tables are read
in page order DL, LB, DB, each checked against its heading. Teams are nicknames (config/aliases/
rotoballer_teams.json); an injury tag after a name ("(Q)") is kept as a flag; the Opponent column is ignored.
`published_at` is the article's JSON-LD `datePublished`: a player whose game kicked off before it is dropped,
since the article came out after his game. Past weeks come from the same feed, so it backfills.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from ..config import load_alias
from ..matching import Matcher, MatchReport
from .base import OK, AdapterResult, FetchContext
from .htmltables import tables

NAME = "rotoballer"
HOST = "www.rotoballer.com"
RSS = "https://www.rotoballer.com/?s=idp+rankings&feed=rss2&paged={page}"
URL = RSS.format(page=1)  # for the backfill runner's host budget
RSS_PAGES = 3
LINK = re.compile(r"/week-(\d+)-idp-rankings[a-z0-9-]*?-(\d{4})/\d+/?$")
LISTS = ("DL", "LB", "DB")
HEADING = {"DL": "defensive line", "LB": "linebacker", "DB": "defensive back"}  # words each heading must contain
MIN_ROWS = 20  # a table shorter than this is not the ranking (the article has 50 per list)
FLAG = re.compile(r"\s*\((Q|D|O|IR|PUP|NA|SUS)\)\s*$", re.IGNORECASE)


def find_article(ctx: FetchContext) -> tuple[str | None, str, str | None]:
    """(article URL, status, error): the feed item for the target week and season."""
    for page in range(1, RSS_PAGES + 1):
        resp = ctx.client.get(RSS.format(page=page))
        if getattr(resp, "challenged", False) or resp.status in (403, 429, 503):
            return None, "blocked", "bot challenge" if getattr(resp, "challenged", False) else f"HTTP {resp.status}"
        if resp.status == 404 and page > 1:
            break  # WordPress answers 404 past the last page of results
        if not resp.ok:
            return None, "failed", f"feed page {page}: {resp.error or f'HTTP {resp.status}'}"
        links = re.findall(r"<link>\s*([^<\s]+)\s*</link>", resp.text())
        for link in links:
            m = LINK.search(link)
            if m and (int(m.group(1)), int(m.group(2))) == (ctx.week, ctx.season):
                return link, OK, None
        if "<item>" not in resp.text():
            break  # past the last page of results
    return None, "not_yet_published", f"no week-{ctx.week} article in the feed's first {RSS_PAGES} pages"


def published(html: str) -> str | None:
    """The JSON-LD `datePublished` (the first one on the page)."""
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def ranking_tables(html: str) -> dict[str, tuple[str, list[list[str]], str | None]]:
    """slot -> (status, rows of [rank, player, team], error): the three ranking tables in page order, each
    checked against its heading. A table that doesn't check out fails its list only."""
    found = [
        t for t in tables(html) if t.rows and [c.text.lower() for c in t.rows[0][:3]] == ["rank", "player", "team"]
    ]
    out = {}
    for i, slot in enumerate(LISTS):
        if i >= len(found):
            out[slot] = ("parse_error", [], f"{slot}: table {i + 1} missing")
            continue
        t = found[i]
        if HEADING[slot] not in f"{t.heading} {t.before}".lower():
            out[slot] = ("parse_error", [], f"{slot}: table {i + 1} follows {t.before[-60:]!r}")
            continue
        rows = [[c.text for c in r[:3]] for r in t.rows[1:] if len(r) >= 3]
        if len(rows) < MIN_ROWS:
            out[slot] = ("parse_error", [], f"{slot}: {len(rows)} rows")
            continue
        out[slot] = (OK, rows, None)
    return out


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    url, status, error = find_article(ctx)
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
    teams = load_alias(ctx.cfg, "rotoballer_teams")
    matcher = ctx.matcher or Matcher(ctx.players)
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    lists: dict[str, dict] = {}
    best: dict[str, dict] = {}  # Sleeper id -> {list: (Match, row)}
    dropped = 0
    whole: dict[str, dict[str, int]] = {}
    for slot, (st, rows, err) in ranking_tables(html).items():
        lists[slot] = {"status": st, "error": err, "players": len(rows)}
        if st != OK:
            continue
        ranked = 0
        for rank_text, player, team_text in rows:
            try:
                rank = int(rank_text)
            except ValueError:
                continue
            ranked += 1
            flag = FLAG.search(player)
            name = FLAG.sub("", player).strip()
            team = teams.get(team_text.strip().lower())
            if team is None:
                unresolved[team_text] += 1
            m = matcher.match(NAME, name=name, team=team, slot=slot)
            match.add(m, name=name, team=team, slot=slot)
            if m.pid is None:
                continue
            whole.setdefault(slot, {}).setdefault(m.pid, rank)  # the whole list builds the V curve (§6.4)
            kick = ctx.kickoff_of(m.pid) if ctx.kickoff_of else None
            if cutoff is not None and kick is not None and kick <= cutoff:
                dropped += 1  # the article came out after his game: never a pre-game rank (§5.3)
                continue
            row = {"rank": rank, "name": name, "team": team, "flag": flag.group(1).upper() if flag else None}
            prev = best.setdefault(m.pid, {}).get(slot)
            if prev is not None:
                kept, other = ((m, row), prev) if m.rank < prev[0].rank else (prev, (m, row))
                match.conflict(m.pid, *(f"{r[1]['name']} ({r[0].how})" for r in (kept, other)))
                best[m.pid][slot] = kept
            else:
                best[m.pid][slot] = (m, row)
        lists[slot]["depth"] = ranked

    read = [s for s in LISTS if lists.get(s, {}).get("status") == OK]
    for pid, by_list in best.items():
        first = next(iter(by_list.values()))[1]
        result.rows[pid] = {
            "ranks": {s: r["rank"] for s, (_m, r) in by_list.items()},
            "flags": {s: r["flag"] for s, (_m, r) in by_list.items() if r["flag"]},
            "team": first["team"],
        }
    result.universe = {s: {"complete": False, "depth": lists[s].get("depth")} for s in read}
    result.lists = {s: dict(whole.get(s) or {}) for s in read}  # players already played included (§6.4)
    result.report |= {"lists": lists, "match": match.summary(), "unresolved_teams": dict(unresolved),
                      "dropped_after_kickoff": dropped}  # fmt: skip
    if read:
        result.status = OK
        return result
    result.status = "parse_error"
    result.error = "; ".join(v["error"] for v in lists.values() if v.get("error")) or "no ranking tables"
    return result
