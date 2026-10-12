"""Backfill runner (SPEC.md §5.3): past weeks' projections from every source that serves an archive.

For each enabled source with `archive = true`, each past week of the season with no snapshot is fetched once
with `ctx.archive`, as of the backfill time, and every row is marked `backfill: true`. Its imputation uses only
actuals from earlier weeks (§6.2). The runner stops at `backfill.budget_s` seconds, and a source stops at
`backfill.max_requests` requests to its host; what's left waits for the next sync.
"""

from __future__ import annotations

import time
import traceback
import urllib.parse
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from . import storage, weeklog
from .impute import Imputer
from .sources.base import FetchContext

BUDGET_S = 300.0
MAX_REQUESTS = 40


def history(data_dir: Path | str, season: int, before_week: int) -> dict[str, list[dict]]:
    """This season's played Sleeper stat lines per player, from weeks before `before_week` only (no leakage)."""
    out: dict[str, list[dict]] = {}
    for week in range(1, before_week):
        doc = storage.read_json(storage.week_dir(data_dir, season, week) / "actuals.json") or {}
        for pid, line in (doc.get("stats") or {}).items():
            if weeklog.played(line):
                out.setdefault(str(pid), []).append(line)
    return out


def imputer_for(cfg: dict, priors: dict | None, data_dir: Path | str, season: int, week: int, players: dict) -> Imputer:
    """The §6.2 imputer for `week`: last season's priors and this season's actuals before the week."""
    m_k = (cfg.get("impute") or {}).get("m_k") or {}
    return Imputer(priors or {}, history(data_dir, season, week), players, m_override=m_k)


def source_cfg(cfg: dict, name: str) -> dict:
    """`ctx.cfg`: the source's own config table plus the ESPN league id and the config root (alias tables)."""
    out = {**((cfg.get("sources") or {}).get(name) or {}), "espn_league_id": (cfg.get("espn") or {}).get("league_id")}
    if "_root" in cfg:
        out["_root"] = cfg["_root"]
    return out


def host_of(module) -> str:
    """The host an adapter talks to (`HOST`, or its `URL`'s), for the per-host request budget."""
    return getattr(module, "HOST", None) or urllib.parse.urlsplit(getattr(module, "URL", "")).hostname or module.NAME


def _all_backfilled(path: Path) -> bool:
    rows = storage.load_source(path).get("rows") or {}
    return all(r.get("backfill") for r in rows.values())


def run(
    sources: dict[str, tuple],
    cfg: dict,
    data_dir: Path | str,
    season: int,
    weeks: Iterable[int],
    now: datetime,
    *,
    client,
    sleeper,
    players: dict[str, dict],
    matcher=None,
    priors: dict | None = None,
    only: str | None = None,
    redo: bool = False,
    log: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, list[int]]:
    """Fetch and save the missing past `weeks` of every archive source (`only` one, if named): {name: [weeks]}.

    `sources` is the sync's registry (name -> (module, kind)); `players` the IDP players. With `redo`, a week
    that was backfilled before is fetched again; a week with live rows is never overwritten.
    """
    bcfg = cfg.get("backfill") or {}
    budget_s = float(bcfg.get("budget_s", BUDGET_S))
    max_requests = int(bcfg.get("max_requests", MAX_REQUESTS))
    weeks = sorted({int(w) for w in weeks if int(w) >= 1})
    start = clock()
    used: Counter[str] = Counter()
    done: dict[str, list[int]] = {}
    for name, (module, _kind) in sources.items():
        scfg = (cfg.get("sources") or {}).get(name) or {}
        if only and name != only:
            continue
        if not (scfg.get("enabled", True) and scfg.get("archive")):
            if only:
                log(f"backfill: {name} is disabled or has no archive (sources.{name}.archive); nothing to do")
            continue
        host = host_of(module)
        todo = []
        for w in weeks:
            path = storage.week_dir(data_dir, season, w) / "sources" / f"{name}.json"
            if not path.exists():
                todo.append(w)
            elif redo and _all_backfilled(path):
                todo.append(w)
            elif redo:
                log(f"backfill: {name} week {w} has live rows; left alone")
        for i, w in enumerate(todo):
            left = ", ".join(str(x) for x in todo[i:])
            if clock() - start >= budget_s:
                log(f"backfill: time budget ({budget_s:.0f} s) reached; {name} weeks {left} wait for the next sync")
                return done
            if used[host] >= max_requests:
                log(f"backfill: {max_requests} requests to {host} reached; {name} weeks {left} wait for the next sync")
                break
            before = getattr(client, "request_count", None)
            ctx = FetchContext(client, sleeper, season, w, now, players, source_cfg(cfg, name), matcher,
                               imputer_for(cfg, priors, data_dir, season, w, players), archive=True)  # fmt: skip
            try:
                res = module.fetch(ctx)
                why = None if res.ok else res.status + (f" ({res.error})" if res.error else "")
            except Exception as e:  # one broken adapter never stops the others (SPEC §7)
                log(traceback.format_exc())
                res, why = None, f"{type(e).__name__} in fetch"
            after = getattr(client, "request_count", None)
            used[host] += after - before if isinstance(before, int) and isinstance(after, int) else 1
            if why:
                log(f"backfill: {name} week {w}: {why}; retried next sync")
                continue
            path = storage.week_dir(data_dir, season, w) / "sources" / f"{name}.json"
            storage.save_source(path, res, {pid: {**row, "backfill": True} for pid, row in res.rows.items()})
            done.setdefault(name, []).append(w)
    return done
