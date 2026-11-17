"""Read-only data audit (SPEC §7 "Data audit", added 2026-11-17 after the full review).

`sadlidp audit` re-runs the integrity checks the backtests and the Stats page depend on, over every saved
week of a season, and prints pass/warn per check. It writes nothing and fixes nothing. A warning is a
prompt to look, never a rule to drop data: a week is left out of a test only if that test's registration
says so in advance (`docs/eval-protocol.md`).

Checks (thresholds fixed in advance as relative rules, so they don't drift to silence a warning):
- **rows:** each source's saved rows for a week with results, against the median of its earlier weeks
  (3 or more): warn below half. 2026 week 5's ESPN (37) and FantasyPros (34) rows are the case it was
  written for.
- **late source:** the week's last sync merged a source mostly after kickoff: more rows were skipped for
  games already started than were saved before them, and over half of what it fetched.
- **kickoff:** every frozen record's kickoff is one of the week's scheduled kickoffs.
- **page:** each week's page has the per-player swing, and a live week has its pre-waiver tiers log.
- **weights:** each recorded `weights.json` (method `accuracy`) reproduces from the saved data.
- **unmatched:** a source name left unmatched in 3 or more weeks.

A week listed in `[audit] acknowledged` ("2026-w05") has been looked at and written up; its warnings show as
`known` and aren't counted, so a new problem stands out.
"""

from __future__ import annotations

import os
import re
import statistics
from datetime import datetime
from pathlib import Path

from . import accuracy, storage
from .schedule import Schedule

ROW_SHARE = 0.5  # a week's rows below this share of the source's earlier median: warn
MIN_HISTORY = 3  # earlier weeks needed before the row check applies
UNMATCHED_WEEKS = 3  # a name unmatched in this many weeks: warn


def _weeks(season_dir: Path) -> list[tuple[int, Path]]:
    out = []
    for d in season_dir.glob("week*"):
        m = re.fullmatch(r"week(\d+)", d.name)
        if m:
            out.append((int(m.group(1)), d))
    return sorted(out)


def _row(check: str, week: int | None, ok: bool, detail: str) -> dict:
    return {"check": check, "week": week, "status": "pass" if ok else "warn", "detail": detail}


def run(data_dir: Path | str, season: int, cfg: dict) -> list[dict]:
    season_dir = Path(data_dir) / str(season)
    sched = Schedule.from_json(storage.read_json(season_dir / "schedule.json") or {"season": season, "games": []})
    out: list[dict] = []
    counts: dict[str, list[tuple[int, int]]] = {}  # source -> [(week, rows)]
    unmatched: dict[tuple[str, str], set[int]] = {}
    for week, wd in _weeks(season_dir):
        has_results = (wd / "actuals.json").exists()
        for path in sorted((wd / "sources").glob("*.json")):
            doc = storage.read_json(path) or {}
            if doc.get("kind") == "signal":
                continue
            n = len(doc.get("rows") or {})
            earlier = [c for _, c in counts.get(path.stem, [])]
            if has_results and len(earlier) >= MIN_HISTORY:
                med = statistics.median(earlier)
                ok = n >= ROW_SHARE * med
                out.append(_row("rows", week, ok, f"{path.stem}: {n} rows (earlier median {med:.0f})"))
            if has_results:
                counts.setdefault(path.stem, []).append((week, n))
        run_doc = storage.read_json(wd / "run.json") or {}
        for name, info in (run_doc.get("sources") or {}).items():
            if ((storage.read_json(wd / "sources" / f"{name}.json") or {}).get("kind")) == "signal":
                continue  # the D/ST signal file is never blended
            merge = info.get("merge") or {}
            lost, kept = merge.get("skipped_after_kickoff", 0), merge.get("frozen", 0)
            fetched = lost + sum(merge.get(k, 0) for k in ("unchanged", "updated", "added", "late"))
            if lost and lost > ROW_SHARE * fetched and lost > kept:
                out.append(_row("late source", week, False,
                                f"{name}: {lost} of {fetched} rows were for games already started"))  # fmt: skip
            for u in ((info.get("report") or {}).get("match") or {}).get("unmatched") or []:
                key = (name, " ".join(str(u.get(k)) for k in ("name", "team", "slot") if u.get(k)))
                unmatched.setdefault(key, set()).add(week)
        kicks = {g.kickoff for g in sched.week_games(week) if g.kickoff}
        frozen = (storage.read_json(wd / "frozen.json") or {}).get("players") or {}
        if frozen and kicks:
            bad = [pid for pid, r in frozen.items() if r.get("kickoff") and datetime.fromisoformat(r["kickoff"])
                   not in kicks]  # fmt: skip
            out.append(_row("kickoff", week, not bad, f"{len(bad)} of {len(frozen)} frozen records off schedule"))
        page = storage.read_json(wd / "consensus.json")
        if page is not None and has_results:
            missing = []
            if not page.get("swing"):
                missing.append("per-player swing")
            log = (storage.read_json(wd / "tiers_log.json") or {}).get("prewaiver")
            if not page.get("replayed") and not log:
                missing.append("pre-waiver tiers log")
            out.append(_row("page", week, not missing, "missing " + ", ".join(missing) if missing else "complete"))
        rec = storage.read_json(wd / "weights.json")
        if rec and rec.get("method") == "accuracy":
            as_of = datetime.fromisoformat(rec["computed_at"]) if rec.get("computed_at") else None
            rule = (rec.get("projection_rule") or {}).get("rule", "ratio")  # the rule in force when it was written
            cfg_then = {**cfg, "weights": {**(cfg.get("weights") or {}), "projection_rule": rule}}
            again = accuracy.weights(accuracy.measure(data_dir, season, week - accuracy.LAG, sched, cfg, as_of),
                                     cfg_then)  # fmt: skip
            diffs = [
                n
                for n, s in (rec.get("sources") or {}).items()
                if abs(float(s["w"]) - float(((again.get("sources") or {}).get(n) or {}).get("w", -1))) > 1e-3
            ]
            if abs(float(rec.get("alpha", 0)) - float(again.get("alpha", 0))) > 1e-3:
                diffs.append("alpha")
            out.append(_row("weights", week, not diffs, "reproduces" if not diffs else "differs: " + ", ".join(diffs)))
    for (name, who), weeks in sorted(unmatched.items()):
        if len(weeks) >= UNMATCHED_WEEKS:
            out.append(_row("unmatched", None, False, f"{name}: {who} unmatched in weeks {sorted(weeks)}"))
    known = set((cfg.get("audit") or {}).get("acknowledged") or [])
    for r in out:
        if r["status"] == "warn" and r["week"] is not None and f"{season}-w{r['week']:02d}" in known:
            r["status"] = "known"
    return out


def report(rows: list[dict]) -> str:
    warn = [r for r in rows if r["status"] == "warn"]
    known = sum(r["status"] == "known" for r in rows)
    lines = [f"audit: {len(rows)} checks, {len(warn)} to look at" + (f", {known} known" if known else "")]
    for r in rows:
        if r["status"] == "warn" or os.environ.get("AUDIT_VERBOSE"):
            wk = f"week {r['week']}" if r["week"] is not None else "season"
            lines.append(f"  {r['status']:4}  {r['check']:11} {wk:8} {r['detail']}")
    if os.environ.get("GITHUB_ACTIONS"):
        lines += [f"::warning title=data audit::{r['check']} (week {r['week']}): {r['detail']}" for r in warn]
    return "\n".join(lines)
