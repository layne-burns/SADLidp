"""League transactions and the FAAB context line (SPEC §6.7 "FAAB context", owner decision 2026-11-17).

Every sync saves the current and previous leg's waiver claims and free-agent moves from Sleeper's public
league endpoint (`/v1/league/<id>/transactions/<leg>`) to `data/<season>/week<NN>/transactions.json`,
compactly. It also fills, a few at a time, earlier legs that have none.

The page shows one factual line built from them: how IDP waiver claims have gone this season (how many a
token bid won, how many were contested and what those went for) and when IDP free agents get picked up.
Leagues start with different FAAB budgets, so every bid is read as a share of the league's starting budget
(Sleeper's `waiver_budget`). A token bid is at most 1% of it, or $1 if that is more. A league without FAAB
(`waiver_type` other than 2) gets no line. These are counts only: never a bid for a particular player, and
never "safe to wait" advice.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from . import storage
from .positions import IDP_SLOTS
from .schedule import ET

KEEP = ("transaction_id", "type", "status", "created", "status_updated", "adds", "drops", "roster_ids", "leg")
BACKFILL_PER_SYNC = 3  # earlier legs fetched per sync until every leg has a file
MIN_CLAIMS = 5  # IDP claims needed before the line is shown
TOKEN_SHARE = 0.01  # a token bid: at most this share of the starting budget ($1 if that is more)
FAAB = 2  # Sleeper's waiver_type for free-agent budget bidding


def compact(t: dict) -> dict:
    out = {k: t.get(k) for k in KEEP}
    out["bid"] = (t.get("settings") or {}).get("waiver_bid")
    return out


def _path(data_dir: Path | str, season: int, leg: int, sub: str | None) -> Path:
    """A leg's file: `week<NN>/transactions.json` for SADL, `week<NN>/<sub>/transactions.json` for another league
    (other_leagues.py)."""
    wdir = storage.week_dir(data_dir, season, leg)
    return (wdir / sub if sub else wdir) / "transactions.json"


def save_leg(sleeper, league_id: str, data_dir: Path | str, season: int, leg: int, now: datetime,
             sub: str | None = None) -> int | None:  # fmt: skip
    """Fetch and save one leg's transactions; the number saved, or None if the fetch failed."""
    payload, _err = sleeper.transactions(league_id, leg)
    if not isinstance(payload, list):
        return None
    rows = [compact(t) for t in payload if isinstance(t, dict)]
    rows.sort(key=lambda t: (t.get("created") or 0, str(t.get("transaction_id"))))
    doc = {"schema_version": 1, "leg": leg, "fetched_at": now.isoformat(), "transactions": rows}
    storage.write_json(_path(data_dir, season, leg, sub), doc)
    return len(rows)


def sync_legs(sleeper, league_id: str, data_dir: Path | str, season: int, week: int, now: datetime,
              sub: str | None = None) -> dict:  # fmt: skip
    """This sync's legs: the previous and current one every time (claims process mid-leg), plus up to
    BACKFILL_PER_SYNC earlier legs that have no file yet. `sub`: another league's folder (None: SADL)."""
    legs = [w for w in (week - 1, week) if w >= 1]
    missing = [w for w in range(1, week - 1) if not _path(data_dir, season, w, sub).exists()]
    out = {}
    for leg in legs + missing[:BACKFILL_PER_SYNC]:
        out[leg] = save_leg(sleeper, league_id, data_dir, season, leg, now, sub)
    return out


def _all(data_dir: Path | str, season: int, sub: str | None = None) -> list[dict]:
    out = []
    pattern = f"week*/{sub}/transactions.json" if sub else "week*/transactions.json"
    for path in sorted((Path(data_dir) / str(season)).glob(pattern)):
        out += (storage.read_json(path) or {}).get("transactions") or []
    return out


def pct_text(x: float, nd: int | None = None) -> str:
    """A share of the budget for the page, without the % sign: one decimal below 10%, whole above (half up),
    "<0.1" for a sliver, "0" for nothing."""
    if x == 0:
        return "0"
    if 0 < x < 0.1:
        return "<0.1"
    nd = (1 if x < 10 else 0) if nd is None else nd
    out = f"{Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP):.{nd}f}"
    return out[:-2] if out.endswith(".0") else out


def range_text(lo: float, hi: float) -> str:
    """'a–b', or one value when the two ends read the same (one decimal first if rounding merged them)."""
    a, b = pct_text(lo), pct_text(hi)
    if lo != hi and a == b:
        a, b = pct_text(lo, 1), pct_text(hi, 1)
    return a if a == b else f"{a}–{b}"


def cut_text(x: float) -> str:
    """The token-bid cut, rounded up to one decimal so the page never states a lower cut than the one counted."""
    out = f"{math.ceil(round(x * 10, 6)) / 10:.1f}"
    return out[:-2] if out.endswith(".0") else out


def faab_summary(data_dir: Path | str, season: int, slots: dict[str, str], settings: dict,
                 count: tuple[str, ...] = IDP_SLOTS, sub: str | None = None) -> dict | None:  # fmt: skip
    """Counts behind the FAAB line, bids as a share of the league's starting budget: IDP waiver claims won, how
    many with a token bid, contested claims (two or more teams claimed the same player in a leg) and their
    winning bids, and IDP free-agent adds by day. None for a league without FAAB. `slots` is pid -> slot for
    every tracked player; only DL, LB and DB count, so the line's numbers are what they were before kickers.
    Another league's kicker line (2026-11-21): `count=("K",)` and `sub` = its folder (other_leagues.py)."""
    budget = float(settings.get("waiver_budget") or 0)
    txs = _all(data_dir, season, sub)
    if not txs or settings.get("waiver_type") != FAAB or budget <= 0:
        return None
    token = max(1.0, TOKEN_SHARE * budget)  # dollars

    def pct(t):
        return 100 * float(t.get("bid") or 0) / budget

    # IDPs only (`count`): `slots` also maps kickers ("K") since 2026-11-17, and a kicker claim is not an IDP claim
    idp = lambda t: [pid for pid in (t.get("adds") or {}) if slots.get(pid) in count]  # noqa: E731
    claims: dict[tuple, list[dict]] = {}
    for t in txs:
        if t.get("type") == "waiver":
            for pid in idp(t):
                claims.setdefault((t.get("leg"), pid), []).append(t)
    won = [next(t for t in ts if t.get("status") == "complete") for ts in claims.values()
           if any(t.get("status") == "complete" for t in ts)]  # fmt: skip
    if len(won) < MIN_CLAIMS:
        return None
    contested = [next(t for t in ts if t.get("status") == "complete") for ts in claims.values()
                 if len(ts) > 1 and any(t.get("status") == "complete" for t in ts)]  # fmt: skip
    bids = [pct(t) for t in contested]
    adds = [t for t in txs if t.get("type") == "free_agent" and t.get("status") == "complete" and idp(t)]
    days = Counter(datetime.fromtimestamp(t["created"] / 1000, ET).strftime("%A") for t in adds if t.get("created"))
    top = days.most_common(2)
    lead = top[0] if top and top[0][1] > 1 and (len(top) == 1 or top[0][1] > top[1][1]) else None  # no tie, 2+
    return {
        "claims": len(won),
        "budget": budget,
        "token_pct": 100 * token / budget,  # the token-bid cut as a share of the budget (1%, or $1 if more)
        "token_label": cut_text(100 * token / budget),
        "won_token": sum(float(t.get("bid") or 0) <= token for t in won),
        "contested": len(contested),
        "contested_pct": [min(bids), max(bids)] if bids else None,
        "contested_label": range_text(min(bids), max(bids)) if bids else None,
        "fa_adds": sum(days.values()),
        "fa_top_day": lead,
    }
