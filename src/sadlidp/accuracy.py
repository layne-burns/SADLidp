"""Accuracy weights (SPEC §6.5 "Within-group weights", "Group split", "Implementation"; PR 6a).

For week N, from the settled weeks ≤ N − 2:
- each source's error ratio r_s = Σ(e_s − ē_s)² / Σ(e_c − ē_c)² per position-week over the pool players it
  covers (≥ 15), where e = prediction − actual on core points, c is the prior-weight blend of every source
  covering the player, and the bars are means over those players (a level offset earns no weight);
- w_data,s = (1/mean r_s) over the mean of 1/mean r among its group's evaluated sources, so equal skill is 1;
- w_s = n_s/(n_s+k)·w_data,s + k/(n_s+k)·1, n_s = the weeks in which s was evaluated at any position;
- α̂ = clamp(Σ(y−R)(P−R)/Σ(P−R)², 0.2, 0.8) on values demeaned within each position-week with ≥ 15 pool
  players that both groups cover, and α = n/(n+k)·α̂ + k/(n+k)·prior, n = weeks with such a position.
Prior weights apply until 3 weeks are evaluated. The weights in force are written once per week to
`weights.json` at its first sync and never recomputed, so a page's numbers don't move as results come in.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from pathlib import Path

from . import scoring, storage, weeklog
from .positions import IDP_SLOTS
from .schedule import Schedule

# rare, unpredictable scores left out of the error that drives the weights (§6.5 "Scoring for weights")
CORE_DROP = frozenset({
    "idp_def_td", "idp_safe", "idp_blk_kick", "blk_kick", "def_td", "def_st_td", "st_td", "fum_rec_td",
    "st_ff", "st_fum_rec", "def_st_ff", "def_st_fum_rec",
})  # fmt: skip
MIN_COVER = 15  # pool players a source must cover in a position-week for it to count
ACTIVATE = 3  # evaluated weeks before the data weights apply
LAG = 2  # week N uses weeks ≤ N − 2


def core_scoring(sc: dict) -> dict:
    return {k: v for k, v in sc.items() if k not in CORE_DROP}


def _demeaned_ss(errors: list[float]) -> float:
    m = statistics.fmean(errors)
    return sum((e - m) ** 2 for e in errors)


def measure(data_dir: Path | str, season: int, upto: int, sched: Schedule, cfg: dict) -> dict:
    """Error ratios per source and the group-split estimate over the settled weeks ≤ `upto`."""
    from . import stats  # stats reads the sync's source labels; imported here to keep the import graph acyclic

    season_dir = Path(data_dir) / str(season)
    league = storage.read_json(season_dir / "league.json") or {}
    core = core_scoring(league.get("scoring_settings") or {})
    prior = float((cfg.get("weights") or {}).get("prior_split", 0.6))
    untrusted = set((cfg.get("accuracy") or {}).get("untrusted") or [])
    slots = stats._positions(season_dir)
    ratios: dict[str, dict[int, list[float]]] = {}  # source -> week -> r per position
    kinds: dict[str, str] = {}
    num = den = 0.0
    alpha_weeks: set[int] = set()
    used: list[int] = []
    for week, wd in stats._weeks(season_dir):
        if week > upto or not core:
            continue
        act_doc = storage.read_json(wd / "actuals.json") or {}
        settles, captured = sched.settles_at(week), act_doc.get("captured_at")
        if settles is None or captured is None or datetime.fromisoformat(captured) < settles:
            continue  # stat corrections still open (§6.5 Actuals)
        lines = act_doc.get("stats") or {}
        act = {pid: scoring.points(line, core) for pid, line in lines.items() if weeklog.played(line)}
        preds, rows, kd, pall = stats.source_points(wd, core, slots)
        for name in untrusted & preds.keys():  # an archive that hasn't passed its check: live rows only
            live = {
                p for p, row in rows[name].items() if not (row or {}).get("backfill") and not (row or {}).get("late")
            }
            preds[name] = {p: v for p, v in preds[name].items() if p in live}
        kinds.update(kd)

        def groups(pid, preds=preds, kd=kd):
            p = [v[pid] for n, v in preds.items() if kd[n] == "projection" and pid in v]
            r = [v[pid] for n, v in preds.items() if kd[n] == "rank" and pid in v]
            return (statistics.fmean(p) if p else None), (statistics.fmean(r) if r else None)

        def blend(pid):
            p, r = groups(pid)
            if p is not None and r is not None:
                return prior * p + (1 - prior) * r
            return p if p is not None else r

        counted = False
        for slot in IDP_SLOTS:
            cand = {pid: v for pid, v in pall.items() if slots.get(pid) == slot and pid in act}
            pool = sorted(cand, key=lambda pid: (-cand[pid], pid))[: stats.N_EVAL]
            for name, vals in preds.items():
                cov = [pid for pid in pool if pid in vals and blend(pid) is not None]
                if len(cov) < MIN_COVER:
                    continue
                d = _demeaned_ss([blend(pid) - act[pid] for pid in cov])
                if d > 0:
                    ratios.setdefault(name, {}).setdefault(week, []).append(
                        _demeaned_ss([vals[pid] - act[pid] for pid in cov]) / d
                    )
                    counted = True
            trip = [(act[pid], *groups(pid)) for pid in pool]
            trip = [t for t in trip if t[1] is not None and t[2] is not None]
            if len(trip) >= MIN_COVER:
                m = [statistics.fmean(t[i] for t in trip) for i in range(3)]
                for y, p, r in trip:
                    y, p, r = y - m[0], p - m[1], r - m[2]
                    num += (y - r) * (p - r)
                    den += (p - r) ** 2
                alpha_weeks.add(week)
        if counted:
            used.append(week)
    return {
        "from_weeks": used,
        "sources": {
            name: {
                "kind": kinds.get(name, "projection"),
                "n": len(by_week),
                "mean_r": statistics.fmean(r for rs in by_week.values() for r in rs),
            }
            for name, by_week in sorted(ratios.items())
        },  # fmt: skip
        "alpha_hat_raw": num / den if den > 0 else None,
        "alpha_n": len(alpha_weeks),
    }


def weights(m: dict, cfg: dict) -> dict:
    """The weights in force from a `measure` result: prior until `ACTIVATE` weeks are evaluated."""
    wcfg = cfg.get("weights") or {}
    k = float(wcfg.get("k", 4))
    prior = float(wcfg.get("prior_split", 0.6))
    out = {"method": "prior", "alpha": prior, "alpha_hat": None, "alpha_n": m["alpha_n"],
           "from_weeks": m["from_weeks"], "k": k, "prior_split": prior, "sources": {}}  # fmt: skip
    if len(m["from_weeks"]) < ACTIVATE:
        out["reason"] = f"{len(m['from_weeks'])} evaluated week(s); data weights start at {ACTIVATE}"
        return out
    out["method"] = "accuracy"
    by_group: dict[str, list[str]] = {}
    for name, s in m["sources"].items():
        by_group.setdefault(s["kind"], []).append(name)
    for names in by_group.values():
        inv = {n: 1 / m["sources"][n]["mean_r"] for n in names if m["sources"][n]["mean_r"] > 0}
        scale = statistics.fmean(inv.values()) if inv else 1.0
        for n in names:
            s = m["sources"][n]
            w_data = inv[n] / scale if n in inv else 1.0
            w = s["n"] / (s["n"] + k) * w_data + k / (s["n"] + k) * 1.0
            out["sources"][n] = {"w": round(w, 4), "w_data": round(w_data, 4), "n": s["n"],
                                 "mean_r": round(s["mean_r"], 4)}  # fmt: skip
    raw, n = m["alpha_hat_raw"], m["alpha_n"]
    if raw is not None and n:
        a_hat = min(max(raw, 0.2), 0.8)
        out.update(alpha_hat=round(a_hat, 4), alpha_hat_raw=round(raw, 4),
                   alpha=round(n / (n + k) * a_hat + k / (n + k) * prior, 4))  # fmt: skip
    return out


def in_force(data_dir: Path | str, season: int, week: int, sched: Schedule, cfg: dict, now: datetime) -> dict:
    """Week `week`'s recorded weights, written at its first sync (§6.5 Implementation). A week whose page
    was first synced before accuracy weights existed keeps the prior shares it was shown with."""
    wdir = storage.week_dir(data_dir, season, week)
    path = wdir / "weights.json"
    rec = storage.read_json(path)
    if rec:
        return rec
    if (wdir / "consensus.json").exists():
        rec = weights({"from_weeks": [], "sources": {}, "alpha_hat_raw": None, "alpha_n": 0}, cfg)
        rec["reason"] = "the week's first sync came before accuracy weights"
    else:
        rec = weights(measure(data_dir, season, week - LAG, sched, cfg), cfg)
    rec.update(schema_version=1, week=week, computed_at=now.isoformat())
    wdir.mkdir(parents=True, exist_ok=True)
    storage.write_json(path, rec)
    return rec


def source_weights(rec: dict) -> dict[str, float]:
    return {name: float(s["w"]) for name, s in (rec.get("sources") or {}).items()}
