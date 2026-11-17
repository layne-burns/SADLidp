"""League transactions and the FAAB context line (SPEC §6.7 "FAAB context", owner decision 2026-11-17).

Every sync saves the current and previous leg's waiver claims and free-agent moves from Sleeper's public
league endpoint (`/v1/league/<id>/transactions/<leg>`) to `data/<season>/week<NN>/transactions.json`,
compactly. It also fills, a few at a time, earlier legs that have none.

The page shows one factual line built from them: how IDP waiver claims have gone this season (what share a
$1-or-less bid won, how many were contested and what those went for) and when IDP free agents get picked
up. These are counts only: never a bid for a particular player, and never "safe to wait" advice.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from . import storage
from .schedule import ET

KEEP = ("transaction_id", "type", "status", "created", "status_updated", "adds", "drops", "roster_ids", "leg")
BACKFILL_PER_SYNC = 3  # earlier legs fetched per sync until every leg has a file
MIN_CLAIMS = 5  # IDP claims needed before the line is shown


def compact(t: dict) -> dict:
    out = {k: t.get(k) for k in KEEP}
    out["bid"] = (t.get("settings") or {}).get("waiver_bid")
    return out


def save_leg(sleeper, league_id: str, data_dir: Path | str, season: int, leg: int, now: datetime) -> int | None:
    """Fetch and save one leg's transactions; the number saved, or None if the fetch failed."""
    payload, _err = sleeper.transactions(league_id, leg)
    if not isinstance(payload, list):
        return None
    rows = [compact(t) for t in payload if isinstance(t, dict)]
    rows.sort(key=lambda t: (t.get("created") or 0, str(t.get("transaction_id"))))
    doc = {"schema_version": 1, "leg": leg, "fetched_at": now.isoformat(), "transactions": rows}
    storage.write_json(storage.week_dir(data_dir, season, leg) / "transactions.json", doc)
    return len(rows)


def sync_legs(sleeper, league_id: str, data_dir: Path | str, season: int, week: int, now: datetime) -> dict:
    """This sync's legs: the previous and current one every time (claims process mid-leg), plus up to
    BACKFILL_PER_SYNC earlier legs that have no file yet."""
    legs = [w for w in (week - 1, week) if w >= 1]
    missing = [w for w in range(1, week - 1)
               if not (storage.week_dir(data_dir, season, w) / "transactions.json").exists()]  # fmt: skip
    out = {}
    for leg in legs + missing[:BACKFILL_PER_SYNC]:
        out[leg] = save_leg(sleeper, league_id, data_dir, season, leg, now)
    return out


def _all(data_dir: Path | str, season: int) -> list[dict]:
    out = []
    for path in sorted((Path(data_dir) / str(season)).glob("week*/transactions.json")):
        out += (storage.read_json(path) or {}).get("transactions") or []
    return out


def faab_summary(data_dir: Path | str, season: int, slots: dict[str, str]) -> dict | None:
    """Counts behind the FAAB line: IDP waiver claims won, the share won at $1 or less, contested claims (two
    or more teams claimed the same player in a leg) and their winning bids, and IDP free-agent adds by day."""
    txs = _all(data_dir, season)
    if not txs:
        return None
    idp = lambda t: [pid for pid in (t.get("adds") or {}) if pid in slots]  # noqa: E731
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
    bids = [int(t.get("bid") or 0) for t in contested]
    adds = [t for t in txs if t.get("type") == "free_agent" and t.get("status") == "complete" and idp(t)]
    days = Counter(datetime.fromtimestamp(t["created"] / 1000, ET).strftime("%A") for t in adds if t.get("created"))
    top = days.most_common(1)
    return {
        "claims": len(won),
        "won_at_1": sum(int(t.get("bid") or 0) <= 1 for t in won),
        "contested": len(contested),
        "contested_bids": [min(bids), max(bids)] if bids else None,
        "fa_adds": sum(days.values()),
        "fa_top_day": top[0] if top else None,
    }
