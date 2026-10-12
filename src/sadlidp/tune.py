"""Tier tuning from the committed weeks (owner request 2026-10-12: "the tiers are poor, can we tune them?").

`sadlidp tune` re-tiers every week that has final stats, offline, under candidate settings, and measures
on the players the page would have shown (the top `display.depth` available per position, by μ, as of
the week's rosters) how well the page tracked the actual scores:

- **tier signal**: within-week correlation of −tier with points (higher = higher tiers scored more);
- **order signal**: within-week correlation of μ with points (what the source mix controls; `z` can't
  change it, since tiers only group the μ order);
- **inside r**: the μ order left within tiers (≈ 0 when tiers are wide enough to drop no real signal);
- **chance out of order**: if μ were exactly each player's expected score, how often a table of this
  size would still show some tier averaging less than the one below it (game-to-game swing alone).

It reads the same saved sources as the Stats page and never writes anything.
"""

from __future__ import annotations

import copy
import math
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from . import form, scoring, storage, weeklog
from .consensus import consensus
from .positions import IDP_SLOTS
from .stats import _positions, _weeks, source_points
from .tiers import build_tiers

Z_GRID = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
SPLIT_GRID = (0.4, 0.5, 0.6, 0.7, 0.8)


@dataclass
class Week:
    week: int
    values: dict[str, dict[str, float]]  # source -> pid -> SADL points
    kinds: dict[str, str]  # source -> projection | rank
    slots: dict[str, str]  # pid -> DL | LB | DB
    actual: dict[str, float]  # pid -> SADL points, players who played only
    rostered: set[str]
    history: dict[str, dict] = field(default_factory=dict)  # form.history as of the week (earlier weeks only)


def load_weeks(data_dir: Path | str, season: int, rostered: dict[int, set[str]]) -> list[Week]:
    """Every week with final stats, a saved source and known rosters."""
    season_dir = Path(data_dir) / str(season)
    sc = (storage.read_json(season_dir / "league.json") or {}).get("scoring_settings") or {}
    slots = _positions(season_dir)
    out = []
    for week, wd in _weeks(season_dir):
        if week not in rostered:
            continue
        values, _, kinds, _ = source_points(wd, sc, slots)
        if not values:
            continue
        lines = (storage.read_json(wd / "actuals.json") or {}).get("stats") or {}
        actual = {pid: scoring.points(line, sc) for pid, line in lines.items() if weeklog.played(line)}
        idp = {pid: slots[pid] for vals in values.values() for pid in vals if slots.get(pid) in IDP_SLOTS}
        out.append(Week(week, values, kinds, idp, actual, rostered[week], form.history(data_dir, season, week, sc)))
    return out


def shown(week: Week, cfg: dict) -> list[dict]:
    """The waiver players the page would have listed: per position, the top `display.depth` available by
    μ with their tier, μ and score (None = didn't play)."""
    depth = int((cfg.get("display") or {}).get("depth", 25))
    z = float((cfg.get("tiers") or {}).get("z", 1.0))
    cons = consensus(week.values, week.kinds, week.slots, cfg)
    coef = form.coefficients(cfg)
    if coef and week.history:  # walk-forward: only earlier weeks' results, as the sync would have had
        form.apply(cons, week.slots, week.history, coef)
    rows = []
    for slot in IDP_SLOTS:
        pool = {
            pid: (c["mu"], c["sigma"])
            for pid, c in cons.items()
            if week.slots.get(pid) == slot and pid not in week.rostered
        }
        listed = [(t["tier"], pid) for t in build_tiers(pool, z) for pid in t["players"]][:depth]
        rows += [
            {"week": week.week, "slot": slot, "pid": pid, "tier": tier, "mu": pool[pid][0], "act": week.actual.get(pid)}
            for tier, pid in listed
        ]
    return rows


def within_r(rows: list[dict], key, group=lambda r: r["week"]) -> dict[str, tuple[float | None, int]]:
    """slot -> (Pearson r of key vs points after removing each group's average from both, player-games).
    Grouped by week (the default) it measures order; grouped by (week, tier), the order left inside tiers."""
    out = {}
    for slot in IDP_SLOTS:
        by: dict = {}
        for r in rows:
            if r["slot"] == slot and r["act"] is not None:
                by.setdefault(group(r), []).append(r)
        x, y = [], []
        for q in by.values():
            if len(q) < 2:
                continue
            mx, my = statistics.fmean(key(r) for r in q), statistics.fmean(r["act"] for r in q)
            x += [key(r) - mx for r in q]
            y += [r["act"] - my for r in q]
        try:
            r = statistics.correlation(x, y) if len(x) >= 3 else None
        except statistics.StatisticsError:  # nothing varies (one tier only): no signal to measure
            r = None
        out[slot] = (r, len(x))
    return out


def tier_table(rows: list[dict]) -> dict[str, list[dict]]:
    """slot -> [{tier, n, avg, margin}], margin = 2 standard errors (None below 2 player-games)."""
    out = {}
    for slot in IDP_SLOTS:
        by: dict[int, list[float]] = {}
        for r in rows:
            if r["slot"] == slot and r["act"] is not None:
                by.setdefault(r["tier"], []).append(r["act"])
        out[slot] = [
            {
                "tier": t,
                "n": len(v),
                "avg": statistics.fmean(v),
                "margin": 2 * statistics.stdev(v) / math.sqrt(len(v)) if len(v) >= 2 else None,
            }
            for t, v in sorted(by.items())
        ]
    return out


def chance_out_of_order(rows: list[dict], sims: int = 2000, seed: int = 0) -> dict[str, float | None]:
    """slot -> share of simulated tables with some tier averaging less than the next one, if every score were
    μ plus game-to-game swing (the SD of points around each week's average, as observed)."""
    rng = random.Random(seed)
    out = {}
    for slot in IDP_SLOTS:
        q = [r for r in rows if r["slot"] == slot and r["act"] is not None]
        if len({r["tier"] for r in q}) < 2:
            out[slot] = None
            continue
        weeks = {r["week"] for r in q}
        wmean = {w: statistics.fmean(r["act"] for r in q if r["week"] == w) for w in weeks}
        sd = statistics.pstdev([r["act"] - wmean[r["week"]] for r in q])
        bad = 0
        for _ in range(sims):
            by: dict[int, list[float]] = {}
            for r in q:
                by.setdefault(r["tier"], []).append(r["mu"] + rng.gauss(0, sd))
            means = [statistics.fmean(v) for _, v in sorted(by.items())]
            bad += any(a < b for a, b in zip(means, means[1:], strict=False))
        out[slot] = bad / sims
    return out


def _with(cfg: dict, z: float | None = None, split: float | None = None, form_on: bool | None = None) -> dict:
    c = copy.deepcopy(cfg)
    if z is not None:
        c.setdefault("tiers", {})["z"] = z
    if split is not None:
        c.setdefault("weights", {})["prior_split"] = split
    if form_on is not None:
        c.setdefault("form", {})["enabled"] = form_on
    return c


def report(weeks: list[Week], cfg: dict) -> dict:
    def rows_for(c: dict, only: list[Week] | None = None) -> list[dict]:
        return [r for w in (only or weeks) for r in shown(w, c)]

    current = rows_for(cfg)
    z_rows = {z: rows_for(_with(cfg, z=z)) for z in Z_GRID}
    z_sweep = []
    for z, rows in z_rows.items():
        tiers_per_week = {
            slot: statistics.fmean(
                max((r["tier"] for r in rows if r["slot"] == slot and r["week"] == w.week), default=0) for w in weeks
            )
            for slot in IDP_SLOTS
        }
        z_sweep.append(
            {
                "z": z,
                "tier_r": within_r(rows, lambda r: -r["tier"]),
                "inside_r": within_r(rows, lambda r: r["mu"], group=lambda r: (r["week"], r["tier"])),
                "tiers_per_week": tiers_per_week,
            }
        )

    split_sweep = [
        {"split": s, "order_r": within_r(rows_for(_with(cfg, split=s)), lambda r: r["mu"])} for s in SPLIT_GRID
    ]
    form_sweep = {
        on: {
            "order_r": within_r(rows_for(_with(cfg, form_on=on)), lambda r: r["mu"]),
            "tier_r": within_r(rows_for(_with(cfg, form_on=on)), lambda r: -r["tier"]),
        }
        for on in (False, True)
    }
    return {
        "weeks": [w.week for w in weeks],
        "z": float((cfg.get("tiers") or {}).get("z", 1.0)),
        "split": float((cfg.get("weights") or {}).get("prior_split", 0.6)),
        "table": tier_table(current),
        "order_r": within_r(current, lambda r: r["mu"]),
        "tier_r": within_r(current, lambda r: -r["tier"]),
        "chance_out_of_order": chance_out_of_order(current),
        "z_sweep": z_sweep,
        "split_sweep": split_sweep,
        "form": form_sweep,
        "form_on": bool((cfg.get("form") or {}).get("enabled")),
    }


def _r(res: tuple[float | None, int]) -> str:
    r, _ = res
    return "   —  " if r is None else f"{r:+.2f}±{2 / math.sqrt(max(res[1], 4) - 3):.2f}"  # ~95% margin near r = 0


def format_report(rep: dict) -> str:
    out = [
        f"Weeks {', '.join(map(str, rep['weeks']))}; current z = {rep['z']}, projection:rank split = {rep['split']}",
        "",
        "Tier averages as the page would show them now (± = 2 standard errors; player-games in brackets):",
    ]
    for slot in IDP_SLOTS:
        cells = [
            f"T{t['tier']} {t['avg']:.1f}" + (f"±{t['margin']:.1f}" if t["margin"] is not None else "") + f" ({t['n']})"
            for t in rep["table"][slot]
        ]
        p = rep["chance_out_of_order"][slot]
        chance = "" if p is None else f"   [out of order by chance alone: {p:.0%}]"
        out.append(f"  {slot}: " + "  ".join(cells) + chance)
    out += [
        "",
        "Signal (within-week r with points; ± ≈ 95% margin):",
        "  "
        + "  ".join(
            f"{slot}: μ order {_r(rep['order_r'][slot])}, tier {_r(rep['tier_r'][slot])}" for slot in IDP_SLOTS
        ),
    ]
    out += [
        "",
        "z sweep: tiers only group the μ order, so finer tiers always carry more of it. The useful z is the",
        "largest whose 'inside' r (μ order left within tiers) is ≈ 0: wide enough to drop no real signal.",
        "  z      " + "  ".join(f"{slot + ' tiers/wk, tier r, inside r':<34}" for slot in IDP_SLOTS),
    ]
    for row in rep["z_sweep"]:
        out.append(
            f"  {row['z']:<5}  "
            + "  ".join(
                f"{row['tiers_per_week'][slot]:>3.1f}, {_r(row['tier_r'][slot])}, {_r(row['inside_r'][slot])}".ljust(34)
                for slot in IDP_SLOTS
            )
        )
    out += ["", "Projection:rank split (changes the μ order):"]
    for row in rep["split_sweep"]:
        out.append(f"  {row['split']:<4}" + "  ".join(f"{slot} {_r(row['order_r'][slot])}" for slot in IDP_SLOTS))
    f = rep["form"]
    out += [
        "",
        f"Form term (walk-forward from earlier weeks' results; config: {'on' if rep['form_on'] else 'off'}):",
        "  μ order off → on  "
        + "  ".join(f"{slot} {_r(f[False]['order_r'][slot])} → {_r(f[True]['order_r'][slot])}" for slot in IDP_SLOTS),
        "  tier    off → on  "
        + "  ".join(f"{slot} {_r(f[False]['tier_r'][slot])} → {_r(f[True]['tier_r'][slot])}" for slot in IDP_SLOTS),
    ]
    return "\n".join(out)
