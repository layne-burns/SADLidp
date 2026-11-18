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

Two groups, measured apart (positions.GROUPS, 2026-11-17). Everything above is the IDP group: its five
sources over DL/LB/DB, its record the top level of `weights.json`. The kicker group is the same measurement
over its own sources (sources/registry.py) and the K slot, with a smaller pool (20 best, ≥ 10 covered:
about 28 kickers play a week); its record sits under the one key `"kickers"` of `weights.json` (`K_KEY`; the
record's own `"k"` is the shrinkage in weeks, so that name was taken). Neither group reads
the other's source files, players or results. The two-source "ls" rule applies only in a group that declares
exactly two projection sources, so kickers (three) use the ratio rule.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from pathlib import Path

from . import scoring, storage, weeklog
from .positions import EVAL, GROUPS, slot_of
from .rankmap import rank_values
from .schedule import Schedule
from .sources import registry

# rare, unpredictable scores left out of the error that drives the weights (§6.5 "Scoring for weights")
CORE_DROP = frozenset({
    "idp_def_td", "idp_safe", "idp_blk_kick", "blk_kick", "def_td", "def_st_td", "st_td", "fum_rec_td",
    "st_ff", "st_fum_rec", "def_st_ff", "def_st_fum_rec",
})  # fmt: skip
MIN_COVER = EVAL["idp"]["min_cover"]  # pool players an IDP source must cover in a position-week to count (15)
ACTIVATE = 3  # evaluated weeks before the data weights apply
K_KEY = "kickers"  # the kicker group's record inside weights.json (and run.json's `weights`)
LAG = 2  # week N uses weeks ≤ N − 2


def prior_weights(cfg: dict, group: str = "idp") -> dict[str, float]:
    """Each source's starting weight within its group (§6.5 "Prior weights"); a source not named is 1. Only
    the entries of `[weights.prior]` that belong to `group`'s sources."""
    prior = registry.of_group((cfg.get("weights") or {}).get("prior") or {}, group)
    return {name: float(w) for name, w in prior.items()}


def _weeks(season_dir: Path) -> list[tuple[int, Path]]:
    """(week, dir) for every saved week with results."""
    out = []
    for d in season_dir.glob("week*"):
        m = re.fullmatch(r"week(\d+)", d.name)
        if m and (d / "actuals.json").exists():
            out.append((int(m.group(1)), d))
    return sorted(out)


def _positions(season_dir: Path) -> dict[str, str]:
    """pid -> slot, from every saved players file (the latest week wins)."""
    slots: dict[str, str] = {}
    for d in sorted(season_dir.glob("week*")):
        for name in ("players_idp.json", "players.json"):
            data = storage.read_json(d / name) or {}
            data = data.get("players", data) if isinstance(data, dict) else {}
            for pid, p in data.items():
                s = slot_of(p or {})
                if s:
                    slots[pid] = s
    return slots


def source_points(wd: Path, sc: dict, slots: dict[str, str], as_of: datetime | None = None, group: str = "idp"):
    """Each saved source of `group` scored in SADL points for the week: (points[source][pid], rows[source],
    kind[source], equal-weight projection mean). Rank sources are mapped to points against that mean (§6.4).
    With `as_of`, rows first read after it are left out. Only the group's own files are opened, in the
    registry's order (the IDP order is what it always was), so the other group can't enter any number here."""
    order = list(registry.labels())
    docs: dict[str, dict] = {}
    for path in sorted((wd / "sources").glob("*.json"), key=lambda p: (order.index(p.stem) if p.stem in order
                                                                       else len(order), p.stem)):  # fmt: skip
        if registry.group_of_source(path.stem) != group:
            continue
        doc = storage.read_json(path) or {}
        if as_of is not None:
            doc = {**doc, "rows": {pid: r for pid, r in (doc.get("rows") or {}).items() if not r.get("fetched_at")
                                   or datetime.fromisoformat(r["fetched_at"]) <= as_of}}  # fmt: skip
        docs[path.stem] = doc
    sources = {n: doc.get("rows") or {} for n, doc in docs.items() if doc.get("kind", "projection") == "projection"}
    kinds = dict.fromkeys(sources, "projection")
    preds = {
        name: {pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items()}
        for name, rows in sources.items()
    }
    covering: dict[str, list[float]] = {}
    for pred_s in preds.values():
        for pid, v in pred_s.items():
            covering.setdefault(pid, []).append(v)
    pred_all = {pid: sum(vs) / len(vs) for pid, vs in covering.items()}
    for name, doc in docs.items():
        if doc.get("kind", "projection") != "rank":
            continue
        rows = {pid: r for pid, r in (doc.get("rows") or {}).items() if pid in slots}
        universe, members = doc.get("universe") or {}, doc.get("lists") or {}
        preds[name], _ = rank_values(rows, pred_all, slots.get, universe, lambda pid: False, members=members)
        sources[name] = rows
        kinds[name] = "rank"
    return preds, sources, kinds, pred_all


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    """Weighted mean of (value, weight) pairs, or None."""
    tot = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tot if pairs and tot > 0 else None


def core_scoring(sc: dict) -> dict:
    return {k: v for k, v in sc.items() if k not in CORE_DROP}


def _demeaned_ss(errors: list[float]) -> float:
    m = statistics.fmean(errors)
    return sum((e - m) ** 2 for e in errors)


def measure(data_dir: Path | str, season: int, upto: int, sched: Schedule, cfg: dict,
            as_of: datetime | None = None, group: str = "idp") -> dict:  # fmt: skip
    """Error ratios per source and the group-split estimate over the settled weeks ≤ `upto`, for one group's
    sources and slots ("idp", or "k" for the kickers). With `as_of`, only source rows first read by then count
    (the audit re-checks a recorded week against the data it saw)."""
    season_dir = Path(data_dir) / str(season)
    n_eval, min_cover = EVAL[group]["pool"], EVAL[group]["min_cover"]
    pair_rule = registry.projection_count(group) == 2  # the LS pair needs a group of exactly two (§6.5)
    league = storage.read_json(season_dir / "league.json") or {}
    core = core_scoring(league.get("scoring_settings") or {})
    prior = float((cfg.get("weights") or {}).get("prior_split", 0.6))
    untrusted = set((cfg.get("accuracy") or {}).get("untrusted") or [])
    slots = _positions(season_dir)
    pw = prior_weights(cfg, group)
    ratios: dict[str, dict[int, list[float]]] = {}  # source -> week -> r per position
    kinds: dict[str, str] = {}
    num = den = 0.0
    cells: dict[int, list[float]] = {}  # week -> [num, den] of the group-split estimate (for its jackknife SE)
    pair_cells: dict[int, list[float]] = {}  # week -> [num, den] of the projection pair's LS share (§6.5, PR 26)
    pair: list[str] = []
    used: list[int] = []
    for week, wd in _weeks(season_dir):
        if week > upto or not core:
            continue
        act_doc = storage.read_json(wd / "actuals.json") or {}
        settles, captured = sched.settles_at(week), act_doc.get("captured_at")
        if settles is None or captured is None or datetime.fromisoformat(captured) < settles:
            continue  # stat corrections still open (§6.5 Actuals)
        lines = act_doc.get("stats") or {}
        act = {pid: scoring.points(line, core) for pid, line in lines.items() if weeklog.played(line)}
        preds, rows, kd, pall = source_points(wd, core, slots, as_of, group)  # as_of: rows read by then only
        for name in untrusted & preds.keys():  # an archive that hasn't passed its check: live rows only
            live = {
                p for p, row in rows[name].items() if not (row or {}).get("backfill") and not (row or {}).get("late")
            }
            preds[name] = {p: v for p, v in preds[name].items() if p in live}
        kinds.update(kd)

        def groups(pid, preds=preds, kd=kd):
            p = [(v[pid], pw.get(n, 1.0)) for n, v in preds.items() if kd[n] == "projection" and pid in v]
            r = [(v[pid], pw.get(n, 1.0)) for n, v in preds.items() if kd[n] == "rank" and pid in v]
            return _wmean(p), _wmean(r)

        def blend(pid):
            p, r = groups(pid)
            if p is not None and r is not None:
                return prior * p + (1 - prior) * r
            return p if p is not None else r

        counted = False
        for slot in GROUPS[group]:
            cand = {pid: v for pid, v in pall.items() if slots.get(pid) == slot and pid in act}
            pool = sorted(cand, key=lambda pid: (-cand[pid], pid))[:n_eval]
            for name, vals in preds.items():
                cov = [pid for pid in pool if pid in vals and blend(pid) is not None]
                if len(cov) < min_cover:
                    continue
                d = _demeaned_ss([blend(pid) - act[pid] for pid in cov])
                if d > 0:
                    ratios.setdefault(name, {}).setdefault(week, []).append(
                        _demeaned_ss([vals[pid] - act[pid] for pid in cov]) / d
                    )
                    counted = True
            projs = [n for n in preds if kd[n] == "projection"]
            if pair_rule and len(projs) == 2:  # the LS share of the first projection source against the second
                pair = pair or projs
                a_s, b_s = preds[pair[0]], preds[pair[1]]
                both = [pid for pid in pool if pid in a_s and pid in b_s]
                if len(both) >= min_cover:
                    m3 = [statistics.fmean(v) for v in ([act[p] for p in both], [a_s[p] for p in both],
                                                        [b_s[p] for p in both])]  # fmt: skip
                    c = pair_cells.setdefault(week, [0.0, 0.0])
                    for p_ in both:
                        y, a, b = act[p_] - m3[0], a_s[p_] - m3[1], b_s[p_] - m3[2]
                        c[0] += (y - b) * (a - b)
                        c[1] += (a - b) ** 2
            trip = [(act[pid], *groups(pid)) for pid in pool]
            trip = [t for t in trip if t[1] is not None and t[2] is not None]
            if len(trip) >= min_cover:
                m = [statistics.fmean(t[i] for t in trip) for i in range(3)]
                cell = cells.setdefault(week, [0.0, 0.0])
                for y, p, r in trip:
                    y, p, r = y - m[0], p - m[1], r - m[2]
                    num += (y - r) * (p - r)
                    den += (p - r) ** 2
                    cell[0] += (y - r) * (p - r)
                    cell[1] += (p - r) ** 2
        if counted:
            used.append(week)
    return {
        "from_weeks": used,
        "sources": {
            name: {
                "kind": kinds.get(name, "projection"),
                "n": len(by_week),
                "mean_r": statistics.fmean(r for rs in by_week.values() for r in rs),
                "r_se": _se([statistics.fmean(rs) for rs in by_week.values()]),
            }
            for name, by_week in sorted(ratios.items())
        },  # fmt: skip
        "alpha_hat_raw": num / den if den > 0 else None,
        "alpha_n": len(cells),
        "alpha_se": _jackknife(cells, num, den),
        "pair": _pair_share(pair, pair_cells),
    }


def _pair_share(pair: list[str], cells: dict[int, list[float]]) -> dict | None:
    """The least-squares share of the first projection source in the best mix of the two (demeaned within each
    position-week): Σ(y−b)(a−b)/Σ(a−b)², with its leave-one-week-out SE (§6.5, PR 26)."""
    num, den = sum(c[0] for c in cells.values()), sum(c[1] for c in cells.values())
    if not pair or den <= 0:
        return None
    return {"sources": pair, "raw": num / den, "n": len(cells), "se": _jackknife(cells, num, den)}


def _se(xs: list[float]) -> float | None:
    """Standard error of a mean of per-week values (None below 2 weeks)."""
    return statistics.stdev(xs) / len(xs) ** 0.5 if len(xs) >= 2 else None


def _jackknife(cells: dict[int, list[float]], num: float, den: float) -> float | None:
    """Leave-one-week-out standard error of the group-split estimate num/den (display only, §6.8)."""
    loo = [(num - c[0]) / (den - c[1]) for c in cells.values() if den - c[1] > 0]
    if len(loo) < 2:
        return None
    m = statistics.fmean(loo)
    return ((len(loo) - 1) / len(loo) * sum((x - m) ** 2 for x in loo)) ** 0.5


EMPTY = {"from_weeks": [], "sources": {}, "alpha_hat_raw": None, "alpha_n": 0}  # a `measure` with nothing in it


def weights(m: dict, cfg: dict, group: str = "idp") -> dict:
    """The weights in force from a `measure` result: prior until `ACTIVATE` weeks are evaluated. `group` picks
    whose prior weights apply (the same group `measure` was run for)."""
    wcfg = cfg.get("weights") or {}
    k = float(wcfg.get("k", 4))
    prior = float(wcfg.get("prior_split", 0.6))
    pw = prior_weights(cfg, group)
    out = {"method": "prior", "alpha": prior, "alpha_hat": None, "alpha_n": m["alpha_n"],
           "from_weeks": m["from_weeks"], "k": k, "prior_split": prior, "priors": pw, "sources": {}}  # fmt: skip
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
            p = pw.get(n, 1.0)  # data move a share relative to its prior: w = p·(n/(n+k)·w_data + k/(n+k))
            w = p * (s["n"] / (s["n"] + k) * w_data + k / (s["n"] + k))
            out["sources"][n] = {"w": round(w, 4), "w_data": round(w_data, 4), "n": s["n"],
                                 "mean_r": round(s["mean_r"], 4)}  # fmt: skip
            if s.get("r_se") is not None:
                out["sources"][n]["r_se"] = round(s["r_se"], 4)
    pr = m.get("pair")
    if wcfg.get("projection_rule", "ratio") == "ls" and pr and pr["n"]:
        # the projection pair's shares follow their least-squares mix (§6.5, PR 26): clamped to .2-.8 and
        # shrunk n/(n+k) toward even; the two sources' w then carry those shares (their priors are 1)
        th = pr["n"] / (pr["n"] + k) * min(max(pr["raw"], 0.2), 0.8) + k / (pr["n"] + k) * 0.5
        a, b = pr["sources"]
        for name, share in ((a, th), (b, 1 - th)):
            entry = out["sources"].setdefault(name, {"n": pr["n"]})
            entry["w"] = round(2 * share, 4)
        out["projection_rule"] = {"rule": "ls", "sources": pr["sources"], "raw": round(pr["raw"], 4),
                                  "share": round(th, 4), "n": pr["n"],
                                  "se": round(pr["se"], 4) if pr.get("se") is not None else None}  # fmt: skip
    raw, n = m["alpha_hat_raw"], m["alpha_n"]
    if raw is not None and n:
        a_hat = min(max(raw, 0.2), 0.8)
        out.update(alpha_hat=round(a_hat, 4), alpha_hat_raw=round(raw, 4),
                   alpha=round(n / (n + k) * a_hat + k / (n + k) * prior, 4))  # fmt: skip
        if m.get("alpha_se") is not None:
            out["alpha_se"] = round(m["alpha_se"], 4)
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


def in_force_k(data_dir: Path | str, season: int, week: int, sched: Schedule, cfg: dict, now: datetime) -> dict:
    """Week `week`'s kicker weights: the `"kickers"` record of its `weights.json` (the same shape as the IDP record,
    with its own `computed_at`), measured and added the first time a sync asks for it. No other key of the
    file is touched. It is stored only beside the week's IDP record (`in_force` writes the file first); until
    then it is measured again each sync."""
    path = storage.week_dir(data_dir, season, week) / "weights.json"
    doc = storage.read_json(path) or {}
    if doc.get(K_KEY):
        return doc[K_KEY]
    rec = weights(measure(data_dir, season, week - LAG, sched, cfg, group="k"), cfg, group="k")
    rec.update(computed_at=now.isoformat())
    if doc.get("method"):
        storage.write_json(path, {**doc, K_KEY: rec})
    return rec


def source_weights(rec: dict, cfg: dict | None = None, group: str = "idp") -> dict[str, float]:
    """The within-group weights to apply: the record's, else each source's prior (1 if it has none)."""
    out = prior_weights(cfg or {}, group)
    out.update({name: float(s["w"]) for name, s in (rec.get("sources") or {}).items()})
    return out
