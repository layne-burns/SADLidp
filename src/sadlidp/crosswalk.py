"""The DynastyProcess player-id crosswalk (SPEC.md §6.1 step 3): source ids -> Sleeper ids.

`db_playerids.csv` is fetched at most once per ISO week into the cache directory; a failed fetch reuses the
newest cached copy. Only the id columns the matcher can use are kept, as {id_field: {source id: sleeper id}}.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
TARGET = "sleeper_id"
ID_FIELDS = ("fantasypros_id", "espn_id", "fantasy_data_id")
BLANK = frozenset({"", "NA", "NAN", "NULL", "NONE"})
PREFIX = "db_playerids_"


def _id(value: str | None) -> str | None:
    text = (value or "").strip()
    if text.upper() in BLANK:
        return None
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text  # '12345.0' from a float column


def parse(text: str) -> dict[str, dict[str, str]]:
    """CSV text -> {"fantasypros_id": {fp id: sleeper id}, ...} for the id columns present.

    Blank/NA cells are skipped; a source id mapped to two different Sleeper ids is dropped as ambiguous.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [f for f in ID_FIELDS if f in (reader.fieldnames or [])]
    if TARGET not in (reader.fieldnames or []) or not fields:
        return {}
    out: dict[str, dict[str, str]] = {f: {} for f in fields}
    clashes: dict[str, set[str]] = {f: set() for f in fields}
    for row in reader:
        sleeper = _id(row.get(TARGET))
        if sleeper is None:
            continue
        for f in fields:
            sid = _id(row.get(f))
            if sid is None:
                continue
            if out[f].setdefault(sid, sleeper) != sleeper:
                clashes[f].add(sid)
    for f in fields:
        for sid in clashes[f]:
            del out[f][sid]
    return out


def cache_name(today: date) -> str:
    year, week, _ = today.isocalendar()
    return f"{PREFIX}{year}-W{week:02d}.csv"


def load(client, cache_dir: Path | str, today: date) -> dict[str, dict[str, str]]:
    """The crosswalk for `today`'s ISO week: cached file, else one fetch, else the newest cached file, else {}."""
    cache_dir = Path(cache_dir)
    path = cache_dir / cache_name(today)
    if path.exists():
        return parse(path.read_text(encoding="utf-8"))
    r = client.get(URL)
    table = parse(r.text()) if r.ok else {}
    if table.get("fantasypros_id"):
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text(), encoding="utf-8")
        for old in cache_dir.glob(f"{PREFIX}*.csv"):
            if old != path:
                old.unlink()
        return table
    cached = sorted(cache_dir.glob(f"{PREFIX}*.csv"))  # ISO-week names sort chronologically
    return parse(cached[-1].read_text(encoding="utf-8")) if cached else {}
