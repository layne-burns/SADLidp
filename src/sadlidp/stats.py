"""Season accuracy for the Stats page (D20, SPEC §6.8), computed from the committed week files.

Per week with final stats (actuals.json) and a projection snapshot (every saved projection source):
- prediction = the equal-weight mean of the sources' SADL points for each projected player (the PR 3
  consensus; with one source, that source), σ_pred = √(σ_μ² + σ_out²);
- evaluation pool per position = the top `N_eval` by prediction among players who played (a player
  with no stat line didn't play: a late scratch, out or on bye, and is left out);
- each source is scored on the pool players it covers, so no source picks its own pool (§6.8 per source);
- the naive baseline is the player's average SADL points over his earlier games this season (2+).
Suggestions come from the decision log (live weeks) or the rebuilt page (replayed weeks); tiers from
each week's page. Nothing here feeds back into the numbers on the main page.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import metrics, scoring, storage, weeklog
from .positions import IDP_SLOTS, slot_of
from .schedule import Schedule
from .sync import SOURCE_LABELS

N_EVAL = 50
MIN_CORR_N = 100  # correlations and R² only season to date, and only with this many player-games


def _weeks(season_dir: Path) -> list[tuple[int, Path]]:
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


def _projection_sources(wd: Path) -> dict[str, dict]:
    """name -> rows of every projection source saved for the week, in the sync's source order."""
    order = list(SOURCE_LABELS)
    out = {}
    for path in sorted((wd / "sources").glob("*.json"), key=lambda p: (order.index(p.stem) if p.stem in order
                                                                       else len(order), p.stem)):  # fmt: skip
        doc = storage.read_json(path) or {}
        if doc.get("kind", "projection") == "projection":
            out[path.stem] = doc.get("rows") or {}
    return out


def _naive(history: dict[str, list[float]], pid: str) -> float | None:
    """The naive baseline: his average SADL points over his earlier games this season (2+)."""
    past = history.get(pid) or []
    return sum(past) / len(past) if len(past) >= 2 else None


def _summary(pred: list[float], act: list[float], sig: list[float], base: list[tuple[float, float]]) -> dict:
    return {
        "n": len(pred),
        "mae": metrics.mae(pred, act),
        "rmse": metrics.rmse(pred, act),
        "bias": metrics.bias(pred, act),
        "base_n": len(base),
        "mae_ours_on_base": metrics.mae([b[0] for b in base], [b[1] for b in base]) if base else None,
        "mae_base": metrics.mae([b[2] for b in base], [b[1] for b in base]) if base else None,
        "within1": metrics.within(pred, act, sig, 1.0),
        "within2": metrics.within(pred, act, sig, 2.0),
        "pearson": metrics.pearson(pred, act, MIN_CORR_N),
        "spearman": metrics.spearman(pred, act, MIN_CORR_N),
        "r2": metrics.r2(pred, act, MIN_CORR_N),
    }


def evaluate(data_dir: Path | str, season: int, cfg: dict) -> dict | None:
    season_dir = Path(data_dir) / str(season)
    league = storage.read_json(season_dir / "league.json") or {}
    sc = league.get("scoring_settings") or {}
    weeks = _weeks(season_dir)
    if not sc or not weeks:
        return None
    sched = Schedule.from_json(storage.read_json(season_dir / "schedule.json") or {"season": season, "games": []})
    slots = _positions(season_dir)
    tcfg = cfg.get("tiers") or {}
    floor = tcfg.get("sigma_floor") or {}
    so_saved = storage.read_json(Path(data_dir) / str(season - 1) / "sigma_out.json") or {}
    so = {s: float(so_saved.get(s) or (cfg.get("sigma_out") or {}).get(s, 5.0)) for s in IDP_SLOTS}
    s_mu = {s: max(float(tcfg.get("s0_default", 1.0)), float(floor.get(s, 0.75))) for s in IDP_SLOTS}

    history: dict[str, list[float]] = {}  # pid -> SADL points of earlier games this season
    resid: dict[str, dict[int, tuple[bool, list[float]]]] = {}  # slot -> week -> (provisional, actual − predicted)
    season_rows = {s: {"pred": [], "act": [], "sig": [], "base": [], "pairs": []} for s in IDP_SLOTS}
    by_source: dict[str, dict[str, dict]] = {}  # source -> slot -> pooled pred/act/sig/base, covered, pool
    weekly, suggestions, tiers = [], [], {s: {} for s in IDP_SLOTS}
    for week, wd in weeks:
        act_doc = storage.read_json(wd / "actuals.json") or {}
        lines = act_doc.get("stats") or {}
        played = {pid: scoring.points(line, sc) for pid, line in lines.items() if weeklog.played(line)}
        settles, captured = sched.settles_at(week), act_doc.get("captured_at")
        provisional = settles is None or captured is None or datetime.fromisoformat(captured) < settles
        sources = _projection_sources(wd)
        preds = {
            name: {pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items()}
            for name, rows in sources.items()
        }
        covering: dict[str, list[float]] = {}
        for pred_s in preds.values():
            for pid, v in pred_s.items():
                covering.setdefault(pid, []).append(v)
        pred_all = {pid: sum(vs) / len(vs) for pid, vs in covering.items()}  # equal-weight consensus (§6.8)
        late = sum(1 for rows in sources.values() for r in rows.values() if r.get("late"))
        backfill = sum(1 for rows in sources.values() for r in rows.values() if r.get("backfill"))
        row = {"week": week, "provisional": provisional, "late": late, "backfill": backfill, "by_slot": {},
               "by_source": {}}  # fmt: skip
        page = storage.read_json(wd / "consensus.json") or {}
        so_week = {s: float((page.get("sigma_out") or {}).get(s) or so[s]) for s in IDP_SLOTS}  # as the page used
        for slot in IDP_SLOTS:
            cand = {pid: v for pid, v in pred_all.items() if slots.get(pid) == slot and pid in played}
            pool = sorted(cand, key=lambda pid: (-cand[pid], pid))[:N_EVAL]
            pred = [cand[pid] for pid in pool]
            act = [played[pid] for pid in pool]
            sig = [(s_mu[slot] ** 2 + so_week[slot] ** 2) ** 0.5] * len(pool)
            partial = 0 < len(pool) < N_EVAL  # a snapshot missing games: shown, but kept out of the season numbers
            base = []
            for pid in pool:
                past = history.get(pid) or []
                if len(past) >= 2:
                    base.append((cand[pid], played[pid], sum(past) / len(past)))
            everyone = {pid: pts for pid, pts in played.items() if slots.get(pid) == slot}
            top = None if partial else metrics.top_k_hits({pid: cand[pid] for pid in pool}, everyone, 12)
            summ = _summary(pred, act, sig, base)
            summ.update(top12=top, pearson=None, spearman=None, r2=None, partial=partial)  # weekly: no correlations
            row["by_slot"][slot] = summ
            for name, pred_s in preds.items():  # each source on the pool players it covers
                cov = [pid for pid in pool if pid in pred_s]
                p_s, a_s = [pred_s[pid] for pid in cov], [played[pid] for pid in cov]
                sig_s = [(s_mu[slot] ** 2 + so_week[slot] ** 2) ** 0.5] * len(cov)
                base_s = [(pred_s[pid], played[pid], b) for pid in cov if (b := _naive(history, pid)) is not None]
                summ_s = _summary(p_s, a_s, sig_s, base_s)
                summ_s.update(covered=len(cov), pool=len(pool), pearson=None, spearman=None, r2=None)
                row["by_source"].setdefault(name, {})[slot] = summ_s
                if partial:
                    continue
                acc = by_source.setdefault(name, {}).setdefault(
                    slot, {"pred": [], "act": [], "sig": [], "base": [], "covered": 0, "pool": 0}
                )
                acc["pred"] += p_s
                acc["act"] += a_s
                acc["sig"] += sig_s
                acc["base"] += base_s
                acc["covered"] += len(cov)
                acc["pool"] += len(pool)
            if partial:
                continue
            resid.setdefault(slot, {})[week] = (provisional, [a - p for p, a in zip(pred, act, strict=True)])
            agg = season_rows[slot]
            agg["pred"] += pred
            agg["act"] += act
            agg["sig"] += sig
            agg["base"] += base
            agg["pairs"] += metrics.pairwise(dict(zip(pool, pred, strict=True)), dict(zip(pool, sig, strict=True)),
                                             dict(zip(pool, act, strict=True)))  # fmt: skip
            agg.setdefault("top12", [0, 0])
            if top:
                agg["top12"][0] += top[0]
                agg["top12"][1] += top[1]
        row["partial_slots"] = [slot for slot in IDP_SLOTS if row["by_slot"][slot]["partial"]]
        # a source read live for the first time partway through the week covers only the games still to come
        row["joined_midweek"] = sorted(
            name for name, rows in sources.items()
            if not any(r.get("backfill") for r in rows.values())
            and any(x["pool"] and x["covered"] < x["pool"] / 2 for x in row["by_source"].get(name, {}).values())
        )  # fmt: skip
        weekly.append(row)

        # suggestions: issued live (decision log) or rebuilt (replayed page)
        res = {pid: weeklog.stat_result(line, sc, final=True) for pid, line in lines.items()}

        def result(pid, res=res):
            return res.get(pid) or {"pts": None, "state": "dnp"}

        issued = []
        badge_doc = (storage.read_json(wd / "badges.json") or {}).get("badges") or {}
        if badge_doc:
            issued = [{"w": e["w"], "over": e.get("over"), "badge": e["badge"], "reference": e.get("reference"),
                       "p": e.get("p"), "kind": "live"} for e in badge_doc.values() if e.get("active")]  # fmt: skip
        elif page.get("replayed"):
            for m in (page.get("my_idps") or {}).values():
                for s in m.get("suggestions") or []:
                    issued.append({"w": s["player"]["id"], "over": s.get("over"), "badge": s["badge"],
                                   "reference": s.get("reference"), "p": s.get("p"), "kind": "rebuilt"})  # fmt: skip
        for e in issued:
            e["week"] = week
            e["outcome"] = weeklog.outcome(result(e["w"]), e["over"], result(e["over"]) if e["over"] else None)
            suggestions.append(e)

        # tiers as shown when you set waivers (the pre-waiver sync's log); a rebuilt week's page is that
        # moment. A live week's final page is not: by then it lists only the players yet to play.
        shown = ((storage.read_json(wd / "tiers_log.json") or {}).get("prewaiver") or {}).get("players") or {}
        if not shown and page.get("replayed"):
            for slot in IDP_SLOTS:
                for t in ((page.get("positions") or {}).get(slot) or {}).get("tiers") or []:
                    shown.update({p["id"]: {"slot": slot, "tier": t["tier"]} for p in t["players"]})
        for pid, r in shown.items():
            if pid in played and r.get("slot") in tiers:
                tiers[r["slot"]].setdefault(r["tier"], []).append(played[pid])

        for pid, pts in played.items():
            history.setdefault(pid, []).append(pts)

    season_by_source = {}
    for name, slots_acc in by_source.items():
        season_by_source[name] = {}
        for slot, acc in slots_acc.items():
            summ = _summary(acc["pred"], acc["act"], acc["sig"], acc["base"])
            summ.update(covered=acc["covered"], pool=acc["pool"])
            season_by_source[name][slot] = summ

    season_out = {}
    for slot, agg in season_rows.items():
        summ = _summary(agg["pred"], agg["act"], agg["sig"], agg["base"])
        summ["top12"] = tuple(agg.get("top12") or (0, 0))
        summ["brier"] = metrics.brier(agg["pairs"])
        summ["pairs"] = len(agg["pairs"])
        summ["calibration"] = metrics.calibration(agg["pairs"])
        season_out[slot] = summ

    def tally(kind):
        sel = [e for e in suggestions if e["kind"] == kind and not e.get("reference")]
        decided = [e for e in sel if e["outcome"] in ("hit", "miss", "push")]
        return {"hits": sum(e["outcome"] == "hit" for e in decided), "decided": len(decided),
                "void": sum(e["outcome"] == "void" for e in sel)}  # fmt: skip

    tier_rows = {
        slot: [{"tier": t, "n": len(v), "avg": sum(v) / len(v)} for t, v in sorted(tiers[slot].items()) if v]
        for slot in IDP_SLOTS
    }
    return {
        "season": season,
        "weeks": [w for w, _ in weeks],
        "weekly": weekly,
        "season_by_slot": season_out,
        "suggestions": {"live": tally("live"), "rebuilt": tally("rebuilt")},
        "tiers": tier_rows,
        "season_by_source": season_by_source,
        "source_labels": {name: SOURCE_LABELS.get(name, name) for name in season_by_source},
        # equal priors within the projection group (PR 3); PR 6 records the accuracy weights in force each week
        "sources": [{"name": SOURCE_LABELS.get(n, n), "share": 1 / len(season_by_source)} for n in season_by_source]
        or [{"name": "Sleeper", "share": 1.0}],
        "sigma_out": so,
        "n_eval": N_EVAL,
        "min_corr_n": MIN_CORR_N,
        "sigma_mu": s_mu,
        "_resid": resid,  # internal (σ_out from residuals); not written to stats.json
    }


def residual_sigma_out(
    data_dir: Path | str, season: int, cfg: dict, target_week: int, min_weeks: int = 3
) -> dict | None:
    """σ_out per position from this season's errors (SPEC §6.7, §F): σ_out² = mean(e²) − σ_μ², with e =
    actual − predicted over each week's evaluation pool, demeaned within the position-week (a level
    offset changes no decision). Uses settled weeks ≤ target_week − 2 only; a position without
    `min_weeks` such weeks is left out (it keeps last season's value)."""
    ev = evaluate(data_dir, season, cfg)
    if not ev:
        return None
    out = {}
    for slot in IDP_SLOTS:
        weeks = {
            w: e for w, (prov, e) in (ev["_resid"].get(slot) or {}).items() if not prov and w <= target_week - 2 and e
        }
        if len(weeks) < min_weeks:
            continue  # this position keeps last season's value
        sq = [(x - sum(e) / len(e)) ** 2 for e in weeks.values() for x in e]
        out[slot] = round(max(0.0, sum(sq) / len(sq) - ev["sigma_mu"][slot] ** 2) ** 0.5, 3)
    return out or None
