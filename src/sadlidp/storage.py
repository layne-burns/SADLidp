"""Snapshots and freezing (SPEC.md §3.3, §6.5).

`data/<season>/week<NN>/sources/<src>.json` holds normalized rows. On a successful fetch, every
player whose game hasn't kicked off takes the new fetch's state (updated if listed, deleted if not);
rows of players whose game kicked off before the fetch are never touched, so the file *is* the frozen
pre-kickoff snapshot. A failed fetch leaves the file alone.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


def week_dir(data_dir: Path | str, season: int, week: int) -> Path:
    return Path(data_dir) / str(season) / f"week{week:02d}"


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


COMPACT_KEYS = {"rows", "players", "stats", "badges", "last_pre_kickoff"}  # per-player maps: one entry per line


def dumps(obj) -> str:
    """JSON with sorted keys. Per-player maps (COMPACT_KEYS) are written one player per line, so a
    sync's diff touches only the players that changed and files stay small (SPEC §6.5)."""
    if not isinstance(obj, dict) or not COMPACT_KEYS & obj.keys():
        return json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n"
    parts = []
    for key in sorted(obj):
        value = obj[key]
        if key in COMPACT_KEYS and isinstance(value, dict) and value:
            lines = ",\n".join(
                f"  {json.dumps(str(k))}: {json.dumps(v, sort_keys=True, separators=(',', ':'), default=str)}"
                for k, v in sorted(value.items())
            )
            parts.append(f" {json.dumps(key)}: {{\n{lines}\n }}")
        else:
            text = json.dumps(value, indent=1, sort_keys=True, default=str).replace("\n", "\n ")
            parts.append(f" {json.dumps(key)}: {text}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj))


def _content(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("fetched_at", "kickoff", "late")}


def merge_rows(
    existing: dict[str, dict],
    fetched: dict[str, dict],
    fetched_at: datetime,
    kickoff_of: Callable[[str], datetime | None],
    accept_late: bool = False,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Apply one successful fetch to a source's rows under the kickoff freeze rule.

    A player first seen after his kickoff can't be proven to carry a pre-game value: he is skipped, or,
    for a source whose rows don't change after games (`accept_late`, SPEC §6.5), kept once with
    `late: true` and then frozen like any other row.
    """

    def locked(pid: str, row: dict | None = None) -> bool:
        k = kickoff_of(pid)
        if k is not None and k <= fetched_at:
            return True
        stored = (row or {}).get("kickoff")  # the kickoff when the row was written: a player cut or traded
        return bool(stored) and datetime.fromisoformat(stored) <= fetched_at  # after playing stays frozen

    merged = {pid: row for pid, row in existing.items() if locked(pid, row)}
    counts = {"frozen": len(merged), "updated": 0, "unchanged": 0, "added": 0, "deleted": 0,
              "skipped_after_kickoff": 0, "late": 0}  # fmt: skip
    stamp = fetched_at.isoformat()
    for pid, row in fetched.items():
        if pid in merged:
            continue  # frozen: never modified
        if locked(pid):
            if accept_late:
                merged[pid] = {**row, "fetched_at": stamp, "late": True, "kickoff": kickoff_of(pid).isoformat()}
                counts["late"] += 1
            else:
                counts["skipped_after_kickoff"] += 1  # first seen after kickoff: can't be a pre-game value
            continue
        k = kickoff_of(pid)
        kick = {"kickoff": k.isoformat()} if k else {}
        old = existing.get(pid)
        if old is not None and _content(old) == _content(row):
            merged[pid] = {**old, **kick}  # unchanged: keep when it was first seen (the file lists every fetch)
            counts["unchanged"] += 1
            continue
        counts["updated" if old is not None else "added"] += 1
        merged[pid] = {**row, "fetched_at": stamp, **kick}
    counts["deleted"] = sum(1 for pid in existing if pid not in merged)
    return merged, counts


def list_ranks(row: dict) -> dict[str, float]:
    """A rank row's lists: {list: rank} from its own `list`/`rank` and `other_lists` (§6.4 multi-list)."""
    if not row.get("list") or row.get("rank") is None:
        return {}
    return {row["list"]: row["rank"], **(row.get("other_lists") or {})}


def without_list(rows: dict[str, dict], name: str) -> dict[str, dict]:
    """Rank rows with list `name` taken out: a row whose own list it was moves to its next list, or goes."""
    out = {}
    for pid, row in rows.items():
        ranks = list_ranks(row)
        if name not in ranks:
            out[pid] = row
            continue
        ranks.pop(name)
        if ranks:
            out[pid] = _with_lists(row, ranks)
    return out


def _with_lists(row: dict, ranks: dict[str, float], order: tuple[str, ...] = ("DL", "LB", "DB")) -> dict:
    """`row` holding exactly `ranks`; its own list stays if still there, else the first in `order`. Per-list
    fields (rank_ave ...) belong to the old list and are dropped when the own list changes."""
    home = row.get("list") if row.get("list") in ranks else next(n for n in (*order, *ranks) if n in ranks)
    out = {k: v for k, v in row.items() if k != "other_lists"}
    if home != row.get("list"):
        out = {k: v for k, v in out.items() if not k.startswith("rank_")}
    out.update(list=home, rank=ranks[home])
    others = {n: r for n, r in ranks.items() if n != home}
    return {**out, "other_lists": others} if others else out


def list_scoped(existing: dict[str, dict], fetched: dict[str, dict], ok_lists: set[str]) -> dict[str, dict]:
    """The rows a multi-list fetch stands for (SPEC §6.5 list-scoped merge): the fetched rows of its ok lists, plus
    every earlier rank of the lists that failed, so merge_rows keeps those (stale) instead of deleting them."""
    out = dict(fetched)
    for pid, old in existing.items():
        stale = {n: r for n, r in list_ranks(old).items() if n not in ok_lists}
        if not stale:
            continue
        fresh = {n: r for n, r in list_ranks(out[pid]).items() if n in ok_lists} if pid in out else {}
        base = out.get(pid)
        if base is None or old.get("list") in stale:  # his own list failed: its row stays as it was
            base = {k: v for k, v in old.items() if k not in ("fetched_at", "kickoff", "late")}
        out[pid] = _with_lists(base, {**stale, **fresh})
    return out


def list_counts(rows: dict[str, dict]) -> dict[str, int]:
    """Members per list of a rank source's rows."""
    counts: dict[str, int] = {}
    for row in rows.values():
        for name in list_ranks(row):
            counts[name] = counts.get(name, 0) + 1
    return counts


def load_source(path: Path) -> dict:
    return read_json(path, {"rows": {}})


def save_source(path: Path, result, rows: dict[str, dict]) -> None:
    old = read_json(path, {}) or {}
    earlier = old.get("fetches") or ([old["last_success"]] if old.get("last_success") else [])  # schema 1: one
    fetches = [*earlier, result.fetched_at][-50:]
    write_json(
        path,
        {
            "schema_version": 2,
            "fetches": fetches,  # every successful fetch (a row's fetched_at is when its content was first seen)
            "source": result.name,
            "kind": result.kind,
            "week": result.week,
            "last_success": result.fetched_at,
            # when the source says it first posted this week's data; the earliest seen counts (§5.3)
            "published_at": old.get("published_at") or getattr(result, "published_at", None),
            "rows": rows,
            "no_projection_ids": sorted(result.no_projection_ids),
            "universe": result.universe,
            "covered_keys": sorted(result.covered_keys),
        },
    )
