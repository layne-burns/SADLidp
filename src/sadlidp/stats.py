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

import math
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from . import metrics, scoreboard, scoring, storage, swing, weeklog
from .positions import IDP_SLOTS, slot_of
from .rankmap import rank_values
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


def _names(season_dir: Path) -> dict[str, str]:
    """pid -> name, from every saved players file (the latest week wins)."""
    names: dict[str, str] = {}
    for d in sorted(season_dir.glob("week*")):
        for name in ("players_idp.json", "players.json"):
            data = storage.read_json(d / name) or {}
            data = data.get("players", data) if isinstance(data, dict) else {}
            for pid, p in data.items():
                full = (p or {}).get("full_name") or " ".join(x for x in ((p or {}).get("first_name"),
                                                                          (p or {}).get("last_name")) if x)  # fmt: skip
                if full:
                    names[pid] = full
    return names


def _projection_sources(wd: Path, kind: str = "projection", as_of: datetime | None = None) -> dict[str, dict]:
    """name -> rows of every source of `kind` saved for the week, in the sync's source order. With `as_of`, rows
    first read after it are left out (what the week's files held then)."""
    order = list(SOURCE_LABELS)
    out = {}
    for path in sorted((wd / "sources").glob("*.json"), key=lambda p: (order.index(p.stem) if p.stem in order
                                                                       else len(order), p.stem)):  # fmt: skip
        doc = storage.read_json(path) or {}
        if as_of is not None:
            doc = {**doc, "rows": {pid: r for pid, r in (doc.get("rows") or {}).items() if not r.get("fetched_at")
                                   or datetime.fromisoformat(r["fetched_at"]) <= as_of}}  # fmt: skip
        if doc.get("kind", "projection") == kind:
            out[path.stem] = (doc.get("rows") or {}) if kind == "projection" else doc
    return out


def source_points(wd: Path, sc: dict, slots: dict[str, str], as_of: datetime | None = None):
    """Each saved source's SADL points for the week: (points[source][pid], rows[source], kind[source],
    equal-weight projection mean). Rank sources are mapped to points against that mean (§6.4)."""
    sources = _projection_sources(wd, as_of=as_of)
    kinds = dict.fromkeys(sources, "projection")
    preds = {
        name: {pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items()}
        for name, rows in sources.items()
    }
    covering: dict[str, list[float]] = {}
    for pred_s in preds.values():
        for pid, v in pred_s.items():
            covering.setdefault(pid, []).append(v)
    pred_all = {pid: sum(vs) / len(vs) for pid, vs in covering.items()}  # equal-weight consensus (§6.8)
    for name, doc in _projection_sources(wd, "rank", as_of).items():  # scored on their mapped points (§6.4)
        rows = {pid: r for pid, r in (doc.get("rows") or {}).items() if pid in slots}
        universe, members = doc.get("universe") or {}, doc.get("lists") or {}
        preds[name], _ = rank_values(rows, pred_all, slots.get, universe, lambda pid: False, members=members)
        sources[name] = rows
        kinds[name] = "rank"
    return preds, sources, kinds, pred_all


def _shares(kinds: dict[str, str], names, split: float) -> list[dict]:
    """Each source's prior share of a player's number when every source covers him (§6.4, D11)."""
    by = {g: [n for n in names if kinds.get(n, "projection") == g] for g in ("projection", "rank")}
    group = {"projection": split, "rank": 1 - split} if all(by.values()) else {g: 1.0 for g in by}
    return [
        {"name": SOURCE_LABELS.get(n, n), "share": group[g] / len(ns), "kind": g} for g, ns in by.items() for n in ns
    ]


def _order(path: Path) -> tuple[int, str]:
    """Sources in the site's order (Sleeper, ESPN, FantasyPros, then any other)."""
    order = list(SOURCE_LABELS)
    return (order.index(path.stem) if path.stem in order else len(order), path.stem)


def weights_history(season_dir: Path, prior_split: float, priors: dict[str, float] | None = None) -> list[dict]:
    """Per synced week, the shares in force (§6.5 Implementation): each source's share of a player's number
    when every source that week covers him, from the week's `weights.json` ("default shares" before it)."""
    out = []
    for d in sorted(season_dir.glob("week*")):
        m = re.fullmatch(r"week(\d+)", d.name)
        if not m or not (d / "consensus.json").exists():
            continue
        kinds = {name: (doc.get("kind") or "projection") for name, doc in
                 ((p.stem, storage.read_json(p) or {}) for p in sorted((d / "sources").glob("*.json"), key=_order))
                 if (doc.get("kind") or "projection") in ("projection", "rank")}  # fmt: skip
        rec = storage.read_json(d / "weights.json") or {}
        pri = {**(priors or {}), **(rec.get("priors") or {})}
        w = {n: float(((rec.get("sources") or {}).get(n) or {}).get("w", pri.get(n, 1.0))) for n in kinds}
        alpha = float(rec.get("alpha", prior_split))
        groups = {g: [n for n in kinds if kinds[n] == g] for g in ("projection", "rank")}
        share_g = {"projection": alpha, "rank": 1 - alpha} if all(groups.values()) else {g: 1.0 for g in groups}
        shares = {SOURCE_LABELS.get(n, n): share_g[g] * w[n] / sum(w[x] for x in ns)
                  for g, ns in groups.items() for n in ns}  # fmt: skip
        out.append({"week": int(m.group(1)), "method": rec.get("method") or "default", "alpha": alpha,
                    "alpha_hat": rec.get("alpha_hat"), "from_weeks": rec.get("from_weeks") or [],
                    "reason": rec.get("reason"), "shares": shares,
                    "both_groups": all(groups.values())})  # fmt: skip
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


REFS = ("base", "plain")  # reference rows: before form (frozen base) and the plain average of the projections
BUCKETS = ((1, 5), (6, 10), (11, 25))  # shown-rank buckets of the tier table (§6.8)


def _decisions(picked: list[dict], kind: str) -> dict:
    """Suggestions counted as decisions (§6.8): one per (week, position, compared player, badge kind), the
    group's highest-p badge standing for it (ties: higher μ, then lower id). A push is decided but not paid
    off; a void one (a player who didn't play) is counted apart; reference badges are left out."""
    mine = [e for e in picked if e["kind"] == kind and not e.get("reference")]
    groups: dict[tuple, list[dict]] = {}
    for e in mine:
        groups.setdefault((e["week"], e.get("slot"), e.get("over"), e["badge"]), []).append(e)
    reps = [min(g, key=lambda e: (-(e.get("p") or 0.0), -(e.get("mu_w") or 0.0), str(e["w"]))) for g in groups.values()]
    decided = [e for e in reps if e["outcome"] in ("hit", "miss", "push")]
    hits = sum(e["outcome"] == "hit" for e in decided)
    by_week: dict[int, list[float]] = {}  # decisions in one week share its games: clustered by week
    for e in decided:
        by_week.setdefault(e["week"], []).append((e.get("pts") or 0.0) - (e.get("over_pts") or 0.0))
    net_mean, net_se = metrics.cluster_mean(list(by_week.values()))
    badges = [e for e in mine if e["outcome"] in ("hit", "miss", "push")]
    return {
        "hits": hits, "decided": len(decided), "void": sum(e["outcome"] == "void" for e in reps),
        "expected": sum(e.get("p") or 0.0 for e in decided) if decided else None,
        "net": net_mean, "net_margin": metrics.margin(net_se, len(by_week)), "weeks": len(by_week),
        "wilson": metrics.wilson(hits, len(decided)) if len(decided) >= 5 else None,
        "badges": {"hits": sum(e["outcome"] == "hit" for e in badges), "decided": len(badges)},
        "reps": {id(e) for e in reps},
    }  # fmt: skip


def evaluate(data_dir: Path | str, season: int, cfg: dict) -> dict | None:
    season_dir = Path(data_dir) / str(season)
    league = storage.read_json(season_dir / "league.json") or {}
    sc = league.get("scoring_settings") or {}
    weeks = _weeks(season_dir)
    if not sc or not weeks:
        return None
    sched = Schedule.from_json(storage.read_json(season_dir / "schedule.json") or {"season": season, "games": []})
    slots = _positions(season_dir)
    names = _names(season_dir)
    tcfg = cfg.get("tiers") or {}
    floor_cfg = tcfg.get("sigma_floor") or {}
    depth = int((cfg.get("display") or {}).get("depth", 25))
    so_saved = storage.read_json(Path(data_dir) / str(season - 1) / "sigma_out.json") or {}
    so = {s: float(so_saved.get(s) or (cfg.get("sigma_out") or {}).get(s, 5.0)) for s in IDP_SLOTS}
    s_mu = {s: max(float(tcfg.get("s0_default", 1.0)), float(floor_cfg.get(s, 0.75))) for s in IDP_SLOTS}

    history: dict[str, list[float]] = {}  # pid -> SADL points of earlier games this season
    resid: dict[str, dict[int, tuple[bool, list[float]]]] = {}  # slot -> week -> (provisional, actual − predicted)
    # per week, what every view (a single week, last week, the season) is summed from (§6.8 views)
    raw: dict[int, dict[str, dict]] = {}  # week -> slot -> the pool's numbers
    raw_src: dict[int, dict[str, dict[str, dict]]] = {}  # week -> source -> slot -> pred/act/sig/base/covered/pool
    tiers_wk: dict[int, dict[str, list[tuple[int, int, float]]]] = {}  # week -> slot -> (shown rank, tier, pts)
    board_wk: dict[int, dict] = {}  # week -> the decision scoreboard's per-position numbers (§6.8)
    wcfg = cfg.get("weights") or {}
    priors = {k: float(v) for k, v in (wcfg.get("prior") or {}).items()}
    split = float(wcfg.get("prior_split", 0.6))
    provisional_wk: dict[int, bool] = {}
    live_wk: dict[int, bool] = {}  # synced live before kickoff: neither replayed nor carrying rebuilt records
    played_out: dict[int, bool] = {}  # week -> every game over (last kickoff + 4 h) when its stats were captured
    weekly, suggestions = [], []
    kinds: dict[str, str] = {}  # source -> projection or rank
    for week, wd in weeks:
        act_doc = storage.read_json(wd / "actuals.json") or {}
        lines = act_doc.get("stats") or {}
        played = {pid: scoring.points(line, sc) for pid, line in lines.items() if weeklog.played(line)}
        settles, captured = sched.settles_at(week), act_doc.get("captured_at")
        provisional = settles is None or captured is None or datetime.fromisoformat(captured) < settles
        preds, sources, week_kinds, pred_all = source_points(wd, sc, slots)
        kinds.update(week_kinds)
        late = sum(1 for rows in sources.values() for r in rows.values() if r.get("late"))
        backfill = sum(1 for rows in sources.values() for r in rows.values() if r.get("backfill"))
        page = storage.read_json(wd / "consensus.json") or {}
        frozen = (storage.read_json(wd / "frozen.json") or {}).get("players") or {}
        row = {"week": week, "provisional": provisional, "late": late, "backfill": backfill, "by_slot": {},
               "by_source": {}, "replayed": bool(page.get("replayed")), "rebuilt": 0, "fallback": 0}  # fmt: skip
        so_week = {s: float((page.get("sigma_out") or {}).get(s) or so[s]) for s in IDP_SLOTS}  # as the page used
        swing_cfg = page.get("swing")  # the per-player swing the page used (§6.7), if any

        def out_of(pid: str, slot: str, rows=sources, kinds_w=week_kinds, cfg_w=swing_cfg, so_w=so_week) -> float:
            lam = swing.mix(pid, rows, kinds_w, sc) if cfg_w else None
            return swing.sigma(lam, slot, cfg_w, sc) if lam is not None else so_w[slot]

        def shown(pid: str, slot: str, frozen=frozen, out_of=out_of):
            """The number the page showed (§6.8): (μ, base, σ_pred, floor, ceiling, rebuilt), else None."""
            f = frozen.get(pid) or {}
            if f.get("mu") is None:
                return None
            sig_mu = float(f["sigma"]) if f.get("sigma") is not None else s_mu[slot]
            out = float(f["swing"]) if f.get("swing") is not None else out_of(pid, slot)
            return (float(f["mu"]), float(f.get("base", f["mu"])), (sig_mu**2 + out**2) ** 0.5, f.get("floor"),
                    f.get("ceiling"), bool(f.get("rebuilt")))  # fmt: skip

        for slot in IDP_SLOTS:
            cand = {pid: v for pid, v in pred_all.items() if slots.get(pid) == slot and pid in played}
            pool = sorted(cand, key=lambda pid: (-cand[pid], pid))[:N_EVAL]  # chosen by the plain average (§6.8)
            act = [played[pid] for pid in pool]
            plain = [cand[pid] for pid in pool]
            pred, base_pred, sig, cov = [], [], [], ([], [], [])
            for pid in pool:
                x = shown(pid, slot)
                if x is None:  # no frozen record: the plain average stands in, counted
                    row["fallback"] += 1
                    pred.append(cand[pid])
                    base_pred.append(cand[pid])
                    sig.append((s_mu[slot] ** 2 + out_of(pid, slot) ** 2) ** 0.5)
                    continue
                pred.append(x[0])
                base_pred.append(x[1])
                sig.append(x[2])
                row["rebuilt"] += x[5]
                if x[3] is not None and x[4] is not None:
                    cov[0].append(played[pid])
                    cov[1].append(float(x[3]))
                    cov[2].append(float(x[4]))
            ours = dict(zip(pool, pred, strict=True))
            partial = 0 < len(pool) < N_EVAL  # a snapshot missing games: shown, but kept out of the season numbers
            base = []
            for pid, p in ours.items():
                past = history.get(pid) or []
                if len(past) >= 2:
                    base.append((p, played[pid], sum(past) / len(past)))
            everyone = {pid: pts for pid, pts in played.items() if slots.get(pid) == slot}
            top = None if partial else metrics.top_k_hits(ours, everyone, 12)
            rand = None if partial else metrics.top_k_random(pool, everyone, 12)
            summ = _summary(pred, act, sig, base)
            summ.update(top12=top, pearson=None, spearman=None, r2=None, partial=partial,  # weekly: no correlations
                        ref={"base": metrics.mae(base_pred, act), "plain": metrics.mae(plain, act)})  # fmt: skip
            row["by_slot"][slot] = summ
            pairs = metrics.pairwise(ours, dict(zip(pool, sig, strict=True)), dict(zip(pool, act, strict=True)),
                                     tag=f"{week}:")  # fmt: skip
            raw.setdefault(week, {})[slot] = {
                "pred": pred, "act": act, "sig": sig, "base": base, "pairs": pairs, "top12": top, "rand": rand,
                "partial": partial, "base_pred": base_pred, "plain": plain, "cov": cov,
                "r": metrics.spearman(pred, act, 3),
            }  # fmt: skip
            for name, pred_s in preds.items():  # each source on the pool players it covers
                cv = [pid for pid in pool if pid in pred_s]
                p_s, a_s = [pred_s[pid] for pid in cv], [played[pid] for pid in cv]
                sig_s = [sig[pool.index(pid)] for pid in cv]
                base_s = [(pred_s[pid], played[pid], b) for pid in cv if (b := _naive(history, pid)) is not None]
                summ_s = _summary(p_s, a_s, sig_s, base_s)
                summ_s.update(covered=len(cv), pool=len(pool), pearson=None, spearman=None, r2=None)
                row["by_source"].setdefault(name, {})[slot] = summ_s
                raw_src.setdefault(week, {}).setdefault(name, {})[slot] = {
                    "pred": p_s, "act": a_s, "sig": sig_s, "base": base_s, "covered": len(cv), "pool": len(pool),
                    "partial": partial,
                }  # fmt: skip
            if not partial:
                resid.setdefault(slot, {})[week] = (provisional, [a - p for p, a in zip(pred, act, strict=True)])
        row["partial_slots"] = [slot for slot in IDP_SLOTS if row["by_slot"][slot]["partial"]]
        # a source read live for the first time partway through the week covers only the games still to come
        row["joined_midweek"] = sorted(
            name for name, rows in sources.items()
            if not any(r.get("backfill") for r in rows.values())
            and any(x["pool"] and x["covered"] < x["pool"] / 2 for x in row["by_source"].get(name, {}).values())
        )  # fmt: skip
        weekly.append(row)
        provisional_wk[week] = provisional
        live_wk[week] = not row["replayed"] and not row["rebuilt"] and not row["fallback"]
        kicks = [g.kickoff for g in sched.week_games(week) if g.kickoff]
        played_out[week] = bool(kicks and captured) and datetime.fromisoformat(captured) >= max(kicks) + timedelta(
            hours=4
        )

        # suggestions: issued live (decision log) or rebuilt (replayed page)
        res = {pid: weeklog.stat_result(line, sc, final=True) for pid, line in lines.items()}

        def result(pid, res=res):
            return res.get(pid) or {"pts": None, "state": "dnp"}

        issued = []
        badge_doc = (storage.read_json(wd / "badges.json") or {}).get("badges") or {}
        if badge_doc:
            issued = [{"w": e["w"], "over": e.get("over"), "badge": e["badge"], "reference": e.get("reference"),
                       "p": e.get("p"), "mu_w": e.get("mu_w"), "slot": e.get("slot"), "kind": "live"}
                      for e in badge_doc.values() if e.get("active")]  # fmt: skip
        elif page.get("replayed"):
            for slot_m, m in (page.get("my_idps") or {}).items():
                for s in m.get("suggestions") or []:
                    issued.append({"w": s["player"]["id"], "over": s.get("over"), "badge": s["badge"],
                                   "reference": s.get("reference"), "p": s.get("p"), "mu_w": s["player"].get("mu"),
                                   "slot": slot_m, "kind": "rebuilt"})  # fmt: skip
        for e in issued:
            w_res, o_res = result(e["w"]), result(e["over"]) if e["over"] else None
            e["week"] = week
            e["outcome"] = weeklog.outcome(w_res, e["over"], o_res)
            e.update(name=names.get(e["w"], e["w"]), over_name=names.get(e["over"]) if e["over"] else None,
                     pts=w_res.get("pts"), over_pts=(o_res or {}).get("pts"))  # fmt: skip
            suggestions.append(e)

        # tiers as shown when you set waivers (the pre-waiver sync's log); a rebuilt week's page is that
        # moment. A live week's final page is not: by then it lists only the players yet to play. Only the
        # `depth` players a position showed count (§6.8): the log holds the whole pool from week 6 on.
        log = ((storage.read_json(wd / "tiers_log.json") or {}).get("prewaiver") or {}).get("players") or {}
        by_slot_shown: dict[str, list[tuple[float, float, str]]] = {}
        for pid, r in log.items():
            if r.get("slot") in IDP_SLOTS and r.get("tier") is not None:
                by_slot_shown.setdefault(r["slot"], []).append((r["tier"], -(r.get("mu") or 0.0), pid))
        if not log and page.get("replayed"):
            for slot in IDP_SLOTS:
                for t in ((page.get("positions") or {}).get(slot) or {}).get("tiers") or []:
                    by_slot_shown.setdefault(slot, []).extend((t["tier"], -(p.get("mu") or 0.0), p["id"])
                                                              for p in t["players"])  # fmt: skip
        band = {}
        for slot, lst in by_slot_shown.items():
            band[slot] = [pid for _, _, pid in sorted(lst)[:depth]]
            for rank, (tier, _, pid) in enumerate(sorted(lst)[:depth], start=1):
                if pid in played:
                    tiers_wk.setdefault(week, {}).setdefault(slot, []).append((rank, tier, played[pid]))
        if band:  # the decision scoreboard on the same shown band (§6.8); Tuesday's μ only from a live log
            tue = {pid: float(r["mu"]) for pid, r in log.items() if r.get("mu") is not None}
            rows = scoreboard.band_rows(band, frozen, played, tue, week_kinds, priors, split)
            board_wk[week] = scoreboard.week_stats(rows)

        for pid, pts in played.items():
            history.setdefault(pid, []).append(pts)

    def view(sel: list[int]) -> dict:
        """The Stats page's numbers over the weeks in `sel`: a single week keeps a partial position-week
        (shown, marked), a span of weeks leaves it out (§6.8). Comparisons are per-week paired differences
        with a week-clustered margin (docs/eval-protocol.md)."""
        keep_partial = len(sel) == 1
        by_slot = {}
        for slot in IDP_SLOTS:
            acc = {"pred": [], "act": [], "sig": [], "base": [], "pairs": []}
            top12, partial, rand = [0, 0], False, [0.0, 0.0]
            wk = {"r": [], "bias": [], "sq": [], "diff": {k: [] for k in REFS}, "ref": {k: ([], []) for k in REFS},
                  "cov": ([], [], []), "live": ([], [])}  # fmt: skip
            for w in sel:
                r = (raw.get(w) or {}).get(slot)
                if not r or (r["partial"] and not keep_partial):
                    continue
                partial = partial or r["partial"]
                for k in acc:
                    acc[k] += r[k]
                if r["top12"]:
                    top12 = [top12[0] + r["top12"][0], top12[1] + r["top12"][1]]
                if r["rand"]:
                    rand = [rand[0] + r["rand"][0], rand[1] + r["rand"][1]]
                if r["r"] is not None:
                    wk["r"].append(r["r"])
                wk["bias"].append([p - a for p, a in zip(r["pred"], r["act"], strict=True)])
                wk["sq"].append([(x[0] - x[1]) ** 2 for x in r["pairs"]])
                ours = metrics.mae(r["pred"], r["act"])
                for k, key in (("base", "base_pred"), ("plain", "plain")):
                    other = metrics.mae(r[key], r["act"])
                    if ours is not None and other is not None:
                        wk["diff"][k].append(ours - other)
                    wk["ref"][k][0].extend(r[key])
                    wk["ref"][k][1].extend(r["act"])
                for i in range(3):
                    wk["cov"][i].extend(r["cov"][i])
                if live_wk.get(w):
                    wk["live"][0].extend(r["pred"])
                    wk["live"][1].extend(r["act"])
            summ = _summary(acc["pred"], acc["act"], acc["sig"], acc["base"])
            n_wk = len(wk["bias"])
            bias, bias_se = metrics.cluster_mean(wk["bias"])
            brier, brier_se = metrics.cluster_mean(wk["sq"])
            rr, rr_se = metrics.mean_se(wk["r"])
            refs = {}
            for k in REFS:
                d, d_se = metrics.mean_se(wk["diff"][k])
                refs[k] = {"mae": metrics.mae(*wk["ref"][k]), "diff": d, "margin": metrics.margin(d_se, n_wk),
                           "weeks": len(wk["diff"][k])}  # fmt: skip
            cov = metrics.coverage(*wk["cov"]) if wk["cov"][0] else None
            if cov:
                cov["spread"] = metrics.spread_ratio(acc["pred"], acc["act"], acc["sig"])
            summ.update(
                top12=tuple(top12), top12_random=(rand[0], rand[1] ** 0.5) if top12[1] else None,
                brier=metrics.brier(acc["pairs"]), pairs=len(acc["pairs"]),
                brier_margin=metrics.margin(brier_se, n_wk) if summ["n"] and len(acc["pairs"]) >= 30 else None,
                calibration=metrics.calibration(acc["pairs"]), partial=partial, weeks=n_wk,
                bias_margin=metrics.margin(bias_se, n_wk),
                median_miss=statistics.median(a - p for p, a in zip(acc["pred"], acc["act"], strict=True))
                if acc["pred"] else None,
                rank={"r": rr, "margin": metrics.margin(rr_se, len(wk["r"])), "weeks": len(wk["r"])},
                ref=refs, coverage=cov,
                live_mae=metrics.mae(*wk["live"]) if wk["live"][0] and len(sel) > 1 else None,
            )  # fmt: skip
            by_slot[slot] = summ
        by_src: dict[str, dict] = {}
        order = list(SOURCE_LABELS)
        names_src = sorted({n for w in sel for n in raw_src.get(w) or {}},
                           key=lambda n: (order.index(n) if n in order else len(order), n))  # fmt: skip
        for name in names_src:
            for slot in IDP_SLOTS:
                acc = {"pred": [], "act": [], "sig": [], "base": [], "covered": 0, "pool": 0}
                groups = []
                for w in sel:
                    r = ((raw_src.get(w) or {}).get(name) or {}).get(slot)
                    if not r or (r["partial"] and not keep_partial):
                        continue
                    for k in acc:
                        acc[k] += r[k]
                    groups.append([p - a for p, a in zip(r["pred"], r["act"], strict=True)])
                if acc["pool"]:
                    summ = _summary(acc["pred"], acc["act"], acc["sig"], acc["base"])
                    _, b_se = metrics.cluster_mean(groups)
                    summ.update(covered=acc["covered"], pool=acc["pool"],
                                bias_margin=metrics.margin(b_se, len([g for g in groups if g])))  # fmt: skip
                    by_src.setdefault(name, {})[slot] = summ
        picked = [e for e in suggestions if e["week"] in sel]
        tallies = {kind: _decisions(picked, kind) for kind in ("live", "rebuilt")}
        rep_ids = set().union(*(t.pop("reps") for t in tallies.values()))

        tier_rows, buckets, tier_r = {}, {}, {}
        for slot in IDP_SLOTS:  # margin = 2 standard errors of the tier's average (None below 2 player-games)
            pts: dict = {}
            bk: dict = {b: [] for b in BUCKETS}
            rs = []
            for w in sel:
                shown_w = (tiers_wk.get(w) or {}).get(slot) or []
                for rank, t, v in shown_w:
                    pts.setdefault(t, []).append(v)
                    for b in BUCKETS:
                        if b[0] <= rank <= b[1]:
                            bk[b].append(v)
                if len(shown_w) >= 5:
                    r = metrics.spearman([-x[0] for x in shown_w], [x[2] for x in shown_w], 3)
                    if r is not None:
                        rs.append(r)
            tier_rows[slot] = [
                {"tier": t, "n": len(v), "avg": sum(v) / len(v),
                 "margin": 2 * statistics.stdev(v) / math.sqrt(len(v)) if len(v) >= 2 else None}
                for t, v in sorted(pts.items()) if v
            ]  # fmt: skip
            buckets[slot] = [
                {"lo": b[0], "hi": b[1], "n": len(v), "avg": sum(v) / len(v) if v else None,
                 "margin": 2 * statistics.stdev(v) / math.sqrt(len(v)) if len(v) >= 2 else None}
                for b, v in bk.items()
            ]  # fmt: skip
            m, se = metrics.mean_se(rs)
            tier_r[slot] = {"r": m, "margin": metrics.margin(se, len(rs)), "weeks": len(rs)}
        listed = sorted(picked, key=lambda e: (e["week"], bool(e.get("reference")), e.get("slot") or "",
                                               -(e.get("pts") or -1.0)))  # fmt: skip
        keys = ("week", "slot", "badge", "kind", "reference", "p", "name", "pts", "over_name", "over_pts", "outcome")
        return {
            "weeks": sel,
            "live_weeks": [w for w in sel if live_wk.get(w)],
            "provisional": any(provisional_wk.get(w) for w in sel),
            "by_slot": by_slot,
            "by_source": by_src,
            "suggestions": {
                **tallies,
                "list": [{**{k: e.get(k) for k in keys}, "counted": id(e) in rep_ids} for e in listed],
            },
            "tiers": tier_rows,
            "tier_buckets": buckets,
            "tier_order": tier_r,
            "scoreboard": scoreboard.aggregate({w: board_wk[w] for w in sel if w in board_wk})
            if len(sel) > 1
            else None,
        }

    all_weeks = [w for w, _ in weeks]
    settled = [w for w in all_weeks if not provisional_wk.get(w)] or all_weeks  # season figures: settled weeks only
    season_view = view(settled)
    season_view["held_back"] = [w for w in all_weeks if w not in settled]  # still provisional: its own tab only
    last = max((w for w in all_weeks if played_out.get(w)), default=all_weeks[-1])  # not a week still being played
    views = [{"key": "last", "label": f"Last week (week {last})", **view([last])},
             {"key": "season", "label": "All season", **season_view}]  # fmt: skip
    views += [{"key": f"w{w}", "label": f"Week {w}", **view([w])} for w in all_weeks]
    season_out = season_view["by_slot"]
    season_by_source = season_view["by_source"]
    tier_rows = season_view["tiers"]
    to_date = {}  # week -> the decisions of weeks up to it (a past week's report card, §6.8)
    for w in all_weeks:
        upto = [e for e in suggestions if e["week"] <= w]
        to_date[w] = {kind: {k: v for k, v in _decisions(upto, kind).items() if k != "reps"}
                      for kind in ("live", "rebuilt")}  # fmt: skip
    db = season_out["DB"]["rank"]
    db_note = {"r": db["r"], "margin": db["margin"],
               "show": db["r"] is None or db["margin"] is None or db["r"] - db["margin"] <= 0.1}  # fmt: skip
    return {
        "season": season,
        "weeks": [w for w, _ in weeks],
        "weekly": weekly,
        "season_by_slot": season_out,
        "suggestions": {k: season_view["suggestions"][k] for k in ("live", "rebuilt")},
        "tiers": tier_rows,
        "views": views,
        "db_note": db_note,
        "decisions_to_date": to_date,
        "season_by_source": season_by_source,
        "source_labels": {name: SOURCE_LABELS.get(name, name) for name in season_by_source},
        # equal priors within each group, 60:40 between them (D11); PR 6 records the accuracy weights in force
        "sources": _shares(kinds, season_by_source, float((cfg.get("weights") or {}).get("prior_split", 0.6)))
        or [{"name": "Sleeper", "share": 1.0, "kind": "projection"}],
        "prior_split": float((cfg.get("weights") or {}).get("prior_split", 0.6)),
        "dst_backtest": storage.read_json(Path(data_dir) / str(season - 1) / "dst_backtest.json"),
        "weights_history": weights_history(
            season_dir,
            float((cfg.get("weights") or {}).get("prior_split", 0.6)),
            (cfg.get("weights") or {}).get("prior"),
        ),
        "weights_now": _weights_now(data_dir, season, all_weeks, sched, cfg),
        "sigma_out": so,
        "n_eval": N_EVAL,
        "depth": depth,
        "min_corr_n": MIN_CORR_N,
        "sigma_mu": s_mu,
        "_resid": resid,  # internal (σ_out from residuals); not written to stats.json
    }


def _weights_now(data_dir, season: int, weeks: list[int], sched: Schedule, cfg: dict) -> dict | None:
    """The projection : ranking mix and the sources' error ratios measured on every settled week so far, with
    their week-to-week margins (§6.8; display only: the weights in force are the recorded `weights.json`)."""
    from . import accuracy

    if not weeks:
        return None
    m = accuracy.measure(data_dir, season, max(weeks), sched, cfg)
    if not m["from_weeks"]:
        return None
    return {
        "weeks": m["from_weeks"],
        "alpha_hat": m["alpha_hat_raw"],
        "alpha_margin": metrics.margin(m.get("alpha_se"), m["alpha_n"]),
        "prior_split": float((cfg.get("weights") or {}).get("prior_split", 0.6)),
        "sources": {
            SOURCE_LABELS.get(k, k): {"mean_r": v["mean_r"], "margin": metrics.margin(v.get("r_se"), v["n"])}
            for k, v in m["sources"].items()
        },  # fmt: skip
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
