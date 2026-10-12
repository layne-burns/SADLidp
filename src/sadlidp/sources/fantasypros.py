"""FantasyPros weekly IDP ECR, read logged out (SPEC.md §5.2 "FantasyPros adapter", §6.4, §6.5).

Three public ranking pages (DL, LB, DB), 5 s apart, each embedding `var ecrData = {...}`. Each page is one list
in its own position taxonomy, which here equals the D14 slots, so `universe` and the per-list report are keyed
by list name (DL/LB/DB). A page that fails, can't be parsed or shows another year/week fails its list only
(list-scoped merge, §6.5); the result is ok if any list is ok. Ranks stay ranks here: the effective rank and the
rank -> value mapping happen in the consensus (§6.4).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import load_alias
from ..matching import Matcher, MatchReport
from ..positions import IDP_SLOTS, slot_of
from .base import OK, AdapterResult, FetchContext

NAME = "fantasypros"
URL = "https://www.fantasypros.com/nfl/rankings/{page}.php"
LISTS = IDP_SLOTS  # page dl.php is list DL, ...; also the order a player in several lists falls back on (§6.4)
ECR_START = re.compile(r"\bvar\s+ecrData\s*=\s*")
FAILURE_ORDER = ("blocked", "parse_error", "not_yet_published", "failed")  # the most informative first
RANK_FIELDS = ("rank_ave", "rank_std", "rank_min", "rank_max")


@dataclass
class Page:
    """One ranking page as read: its status and, when the page parsed, its ecrData."""

    status: str
    ecr: dict | None = None
    error: str | None = None
    week: int | None = None
    published_at: str | None = None

    def report(self, players: int | None = None) -> dict:
        ecr = self.ecr or {}
        return {
            "status": self.status,
            "players": players if players is not None else len(ecr.get("players") or []),
            "experts": _int(ecr.get("total_experts")),
            "week": self.week,
            "last_updated": self.published_at,
            "error": self.error,
        }


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iso_ts(ts) -> str | None:
    """A Unix timestamp (`last_updated_ts`) as ISO 8601 UTC with its offset, None if unusable."""
    ts = _float(ts)
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts and ts > 0 else None


def parse_ecr(text: str) -> dict | None:
    """The `var ecrData = {...};` object of a ranking page, None if absent or not valid JSON."""
    m = ECR_START.search(text)
    if not m:
        return None
    try:
        ecr, _ = json.JSONDecoder().raw_decode(text, m.end())
    except ValueError:
        return None
    return ecr if isinstance(ecr, dict) and isinstance(ecr.get("players"), list) else None


def page_url(page: str, ctx: FetchContext) -> str:
    return URL.format(page=page) + (f"?week={ctx.week}" if ctx.archive else "")


def read_page(ctx: FetchContext, page: str, position: str) -> Page:
    """GET one ranking page and check its ecrData: position, then year and week (§5.2 week check)."""
    resp = ctx.client.get(page_url(page, ctx))
    if getattr(resp, "challenged", False) or str(resp.error or "").startswith("blocked"):
        return Page("blocked", error="bot challenge")  # never retried; the client blocks the host (§7)
    if not resp.ok:
        return Page("failed", error=resp.error or f"HTTP {resp.status}")
    ecr = parse_ecr(resp.body.decode("utf-8", errors="replace"))
    if ecr is None:
        return Page("parse_error", error="no ecrData")
    out = Page(OK, ecr, week=_int(ecr.get("week")), published_at=iso_ts(ecr.get("last_updated_ts")))
    if str(ecr.get("position_id") or "").upper() != position:
        out.status, out.error = "parse_error", f"position_id {ecr.get('position_id')!r}, expected {position}"
    elif (_int(ecr.get("year")), out.week) != (ctx.season, ctx.week):
        out.status, out.error = "not_yet_published", f"page shows {ecr.get('year')} week {ecr.get('week')}"
    return out


def overall(pages: dict[str, Page]) -> str:
    """ok if any list is ok, otherwise the most informative list failure."""
    statuses = {p.status for p in pages.values()}
    if OK in statuses:
        return OK
    return next((s for s in FAILURE_ORDER if s in statuses), "failed")


def _label(m, row: dict) -> str:
    return f"{row['name']} ({row['list']} #{row['rank']:g}, FP {row['fp_id']}, {m.how})"


def failures(pages: dict[str, Page]) -> str | None:
    return "; ".join(f"{name}: {p.status} ({p.error})" for name, p in pages.items() if p.status != OK) or None


def fetch(ctx: FetchContext) -> AdapterResult:
    result = AdapterResult(name=NAME, kind="rank", status="failed", week=ctx.week, fetched_at=ctx.now.isoformat())
    teams = load_alias(ctx.cfg, "fantasypros_teams")
    if not teams:
        result.error = "config/aliases/fantasypros_teams.json is missing"
        return result
    pages = {name: read_page(ctx, name.lower(), name) for name in LISTS}

    matcher = ctx.matcher or Matcher(ctx.players)
    match = MatchReport()
    unresolved: Counter[str] = Counter()
    by_list: dict[str, dict[str, tuple]] = {}  # list -> Sleeper id -> (Match, row) of the winning FP row
    for name, page in pages.items():
        if page.status != OK:
            continue
        best = by_list.setdefault(name, {})
        experts = _int(page.ecr.get("total_experts"))
        for p in page.ecr["players"]:
            rank = _float(p.get("rank_ecr")) if isinstance(p, dict) else None
            if rank is None:
                continue
            code = str(p.get("player_team_id") or "").upper()
            if code not in teams:
                unresolved[code] += 1
            fp_id, pname, team = str(p.get("player_id")), p.get("player_name"), teams.get(code)
            m = matcher.match(NAME, source_id=fp_id, id_field="fantasypros_id", name=pname, team=team, slot=name)
            match.add(m, name=pname, team=team, slot=name)
            if m.pid is None:
                continue
            row = {"rank": rank, "list": name, "fp_id": fp_id, "name": pname, "team": team}
            row |= {"pos": p.get("player_position_id"), **{k: _float(p.get(k)) for k in RANK_FIELDS}}
            row["experts"] = experts
            if ctx.archive:  # the caller drops rows whose kickoff is at or before it (§5.2 archive rule)
                row["list_published_at"] = page.published_at
            kept = (m, row)
            if (prev := best.get(m.pid)) is not None:  # the higher-precedence match wins, then the better rank
                kept, dropped = (prev, kept) if prev[0].rank <= m.rank else (kept, prev)
                match.conflict(m.pid, _label(*kept), _label(*dropped))
            best[m.pid] = kept

    multi = 0
    for pid in dict.fromkeys(pid for best in by_list.values() for pid in best):
        lists = [name for name in LISTS if pid in by_list.get(name, {})]
        player = ctx.players.get(pid) or getattr(matcher, "players", {}).get(pid) or {}
        home = slot_of(player) if slot_of(player) in lists else lists[0]  # §6.4: his D14 slot's list, else DL/LB/DB
        row = dict(by_list[home][pid][1])
        if len(lists) > 1:
            multi += 1
            row["other_lists"] = {name: by_list[name][pid][1]["rank"] for name in lists if name != home}
        result.rows[pid] = row

    ok_pages = {name: p for name, p in pages.items() if p.status == OK}
    published = [p.published_at for p in ok_pages.values() if p.published_at]
    result.published_at = min(published, key=datetime.fromisoformat) if published else None
    # depth: the list's matched members (deduplicated); the effective depth |V| needs P, so the consensus owns it
    result.universe = {
        name: {"complete": False, "depth": len(by_list[name]) if name in ok_pages else None, "ok": name in ok_pages}
        for name in LISTS
    }
    result.report = {
        "lists": {name: p.report() for name, p in pages.items()},
        "match": match.summary(),
        "multi_list": multi,
        "unresolved_teams": dict(unresolved),
        "archive": ctx.archive,
    }
    result.status, result.error = overall(pages), failures(pages)
    if result.status == "not_yet_published":
        result.week = max((p.week for p in pages.values() if p.week is not None), default=None)
    return result
