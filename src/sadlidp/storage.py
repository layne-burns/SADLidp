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
            "rows": rows,
            "no_projection_ids": sorted(result.no_projection_ids),
            "universe": result.universe,
            "covered_keys": sorted(result.covered_keys),
        },
    )
