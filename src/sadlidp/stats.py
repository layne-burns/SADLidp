"""Season accuracy for the Stats page (D20, SPEC §6.8), computed from the committed week files.

Per week with final stats (actuals.json) and a projection snapshot:
- prediction = the source's SADL points for each projected player (Sleeper is the only source so far,
  so it is also the consensus), σ_pred = √(σ_μ² + σ_out²);
- evaluation pool per position = the top `N_eval` by prediction among players who played (a player
  with no stat line didn't play: a late scratch, out or on bye, and is left out);
- the naive baseline is the player's average SADL points over his earlier games this season (2+).
Suggestions come from the decision log (live weeks) or the rebuilt page (replayed weeks); tiers from
each week's page. Nothing here feeds back into the numbers on the main page.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import metrics, scoring, storage, weeklog
from .positions import IDP_SLOTS, slot_of
from .schedule import Schedule

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
    season_rows = {s: {"pred": [], "act": [], "sig": [], "base": [], "pairs": []} for s in IDP_SLOTS}
    weekly, suggestions, tiers = [], [], {s: {} for s in IDP_SLOTS}
    for week, wd in weeks:
        act_doc = storage.read_json(wd / "actuals.json") or {}
        lines = act_doc.get("stats") or {}
        played = {pid: scoring.points(line, sc) for pid, line in lines.items() if weeklog.played(line)}
        kicks = [g.kickoff for g in sched.week_games(week) if g.kickoff]
        settles = max(kicks) + timedelta(days=4, hours=4) if kicks else None
        provisional = bool(settles) and datetime.fromisoformat(act_doc.get("captured_at")) < settles
        src = storage.read_json(wd / "sources" / "sleeper_proj.json") or {}
        rows = src.get("rows") or {}
        pred_all = {pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items()}
        late = sum(1 for r in rows.values() if r.get("late"))
        backfill = sum(1 for r in rows.values() if r.get("backfill"))
        row = {"week": week, "provisional": provisional, "late": late, "backfill": backfill, "by_slot": {}}
        for slot in IDP_SLOTS:
            cand = {pid: v for pid, v in pred_all.items() if slots.get(pid) == slot and pid in played}
            pool = sorted(cand, key=lambda pid: (-cand[pid], pid))[:N_EVAL]
            pred = [cand[pid] for pid in pool]
            act = [played[pid] for pid in pool]
            sig = [(s_mu[slot] ** 2 + so[slot] ** 2) ** 0.5] * len(pool)
            base = []
            for pid in pool:
                past = history.get(pid) or []
                if len(past) >= 2:
                    base.append((cand[pid], played[pid], sum(past) / len(past)))
            everyone = {pid: pts for pid, pts in played.items() if slots.get(pid) == slot}
            top = metrics.top_k_hits({pid: cand[pid] for pid in pool}, everyone, 12)
            summ = _summary(pred, act, sig, base)
            summ.update(top12=top, pearson=None, spearman=None, r2=None)  # weekly: too few for correlations
            row["by_slot"][slot] = summ
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
        weekly.append(row)

        # suggestions: issued live (decision log) or rebuilt (replayed page)
        page = storage.read_json(wd / "consensus.json") or {}
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

        # tiers as shown that week: average score per tier among players who played
        for slot in IDP_SLOTS:
            for t in ((page.get("positions") or {}).get(slot) or {}).get("tiers") or []:
                bucket = tiers[slot].setdefault(t["tier"], [])
                bucket += [played[p["id"]] for p in t["players"] if p["id"] in played]

        for pid, pts in played.items():
            history.setdefault(pid, []).append(pts)

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
        "sources": [{"name": "Sleeper", "share": 1.0}],
        "sigma_out": so,
        "n_eval": N_EVAL,
        "min_corr_n": MIN_CORR_N,
    }
