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


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")


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
    counts = {"frozen": len(merged), "updated": 0, "added": 0, "deleted": 0, "skipped_after_kickoff": 0, "late": 0}
    stamp = fetched_at.isoformat()
    for pid, row in fetched.items():
        if pid in merged:
            continue  # frozen: never modified
        if locked(pid):
            if accept_late:
                merged[pid] = {**row, "fetched_at": stamp, "late": True}
                counts["late"] += 1
            else:
                counts["skipped_after_kickoff"] += 1  # first seen after kickoff: can't be a pre-game value
            continue
        counts["updated" if pid in existing else "added"] += 1
        k = kickoff_of(pid)
        merged[pid] = {**row, "fetched_at": stamp, **({"kickoff": k.isoformat()} if k else {})}
    counts["deleted"] = sum(1 for pid in existing if pid not in merged)
    return merged, counts


def load_source(path: Path) -> dict:
    return read_json(path, {"rows": {}})


def save_source(path: Path, result, rows: dict[str, dict]) -> None:
    write_json(
        path,
        {
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
