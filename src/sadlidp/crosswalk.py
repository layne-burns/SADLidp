"""Crosswalk ids for matching (SPEC.md §6.1 step 3): DynastyProcess `db_playerids.csv`.

Maps a source's own player id to a Sleeper id (FantasyPros so far). Fetched at most once per America/New_York
day through a file cache; anything that goes wrong leaves matching to the other steps, never fails the sync.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
COLUMNS = {"fantasypros": "fantasypros_id"}  # crosswalk name -> CSV column (sources name the crosswalk they use)
MISSING = ("", "NA")


def parse(text: str) -> dict[str, dict[str, str]]:
    """{crosswalk name: {source id: Sleeper id}} from the CSV; ids that repeat with different Sleeper ids drop out."""
    out: dict[str, dict[str, str]] = {name: {} for name in COLUMNS}
    clash: dict[str, set[str]] = {name: set() for name in COLUMNS}
    for row in csv.DictReader(io.StringIO(text)):
        sleeper = (row.get("sleeper_id") or "").strip()
        if sleeper in MISSING:
            continue
        for name, col in COLUMNS.items():
            sid = (row.get(col) or "").strip()
            if sid in MISSING:
                continue
            if out[name].get(sid, sleeper) != sleeper:
                clash[name].add(sid)
            out[name][sid] = sleeper
    return {name: {k: v for k, v in ids.items() if k not in clash[name]} for name, ids in out.items()}


def load(client, cache_dir: Path | str, day: str, log: Callable[[str], None] = print) -> dict[str, dict[str, str]]:
    """Today's crosswalk from the cache, else fetched (and cached); {} if it can't be read."""
    cache_dir = Path(cache_dir)
    path = cache_dir / f"crosswalk_{day}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    resp = client.get(URL)
    if not resp.ok:
        log(f"crosswalk: not read ({resp.error or f'HTTP {resp.status}'}); matching by name")
        return {}
    table = parse(resp.text())
    if not any(table.values()):
        log("crosswalk: no ids found in the file; matching by name")
        return {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("crosswalk_*.json"):
        old.unlink()
    path.write_text(json.dumps(table, sort_keys=True))
    return table
