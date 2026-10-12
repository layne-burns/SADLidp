"""Per-week decision logs for later evaluation (SPEC.md §6.5, D15, D20).

`badges.json`: every START/UPGRADE suggestion, keyed `W|BADGE|OVER`. Until the earlier kickoff of W and the
player he is compared with (`lock_at`) it takes the latest state (`active` false if withdrawn); after
that it is frozen, so "issued" = active at the last sync before lock. `tiers_log.json`: the waiver tiers
as shown at the pre-waiver sync and, per player, at his last sync before kickoff.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from . import scoring, storage


def _open(entry: dict, now: datetime) -> bool:
    lock_at = entry.get("lock_at")
    return lock_at is None or datetime.fromisoformat(lock_at) > now


def update_badge_log(existing: dict[str, dict], current: dict[str, dict], now: datetime) -> dict[str, dict]:
    """Keys are `W|BADGE|OVER` (the comparison is part of the suggestion). An open entry takes the latest
    state; one that disappears before its lock is marked withdrawn; after lock nothing changes."""
    stamp = now.isoformat()
    live = {key for key, e in current.items() if _open(e, now)}
    out = {key: ({**e, "active": key in live} if _open(e, now) else e) for key, e in existing.items()}
    for key, entry in current.items():
        old = out.get(key)
        if not _open(entry, now) or (old is not None and not _open(old, now)):
            continue  # past its lock: frozen as issued or withdrawn, or first seen after lock (never issued)
        out[key] = {
            **entry,
            "first_seen": (old or {}).get("first_seen", stamp),
            "first_p": (old or {}).get("first_p", entry["p"]),
            "last_seen": stamp,
            "active": True,
        }
    return out


def update_tiers_log(
    existing: dict,
    tier_rows: dict[str, dict],
    now: datetime,
    kickoff_of: Callable[[str], datetime | None],
    prewaiver: bool,
) -> dict:
    last, _ = storage.merge_rows(existing.get("last_pre_kickoff", {}), tier_rows, now, kickoff_of)
    out = {"schema_version": 1, "last_pre_kickoff": last, "prewaiver": existing.get("prewaiver")}
    if prewaiver:
        out["prewaiver"] = {"at": now.isoformat(), "players": tier_rows}
    return out


def played(line) -> bool:
    """A Sleeper stat line from a player who took part (gp, or any IDP stat)."""
    return isinstance(line, dict) and bool(line.get("gp") or any(v for k, v in line.items() if k.startswith("idp_")))


def stat_result(line, sc: dict, final: bool) -> dict:
    """SADL points from a stat line: `live`/`final`, or `dnp` once final with no line (D22)."""
    if played(line):
        return {"pts": round(scoring.points(line, sc), 2), "state": "final" if final else "live"}
    return {"pts": None, "state": "dnp"} if final else {"pts": 0.0, "state": "live"}


def settled(r: dict | None) -> bool:
    return bool(r) and r["state"] in ("final", "dnp")


def outcome(w_res: dict | None, over: str | None, over_res: dict | None) -> str | None:
    """hit / miss / push once both results are final (an empty slot counts as 0); void if either didn't play."""
    if not settled(w_res) or (over and not settled(over_res)):
        return None
    if w_res["state"] == "dnp" or (over and over_res["state"] == "dnp"):
        return "void"
    w_pts, o_pts = w_res["pts"] or 0.0, (over_res or {}).get("pts") or 0.0
    return "hit" if w_pts > o_pts else "miss" if w_pts < o_pts else "push"
