"""Per-week decision logs for later evaluation (SPEC.md §6.5, D15, D20).

`badges.json`: every START/UPGRADE suggestion, keyed `W|BADGE`. Until the earlier kickoff of W and the
player he is compared with (`lock_at`) it takes the latest state (`active` false if withdrawn); after
that it is frozen, so "issued" = active at the last sync before lock. `tiers_log.json`: the waiver tiers
as shown at the pre-waiver sync and, per player, at his last sync before kickoff.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from . import storage


def _open(entry: dict, now: datetime) -> bool:
    lock_at = entry.get("lock_at")
    return lock_at is None or datetime.fromisoformat(lock_at) > now


def update_badge_log(existing: dict[str, dict], current: dict[str, dict], now: datetime) -> dict[str, dict]:
    stamp = now.isoformat()
    out = {key: ({**e, "active": key in current} if _open(e, now) else e) for key, e in existing.items()}
    for key, entry in current.items():
        old = out.get(key)
        if old is not None and not _open(old, now):
            continue  # locked: frozen as issued (or withdrawn)
        if old is None and not _open(entry, now):
            continue  # first seen after lock: never issued
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
