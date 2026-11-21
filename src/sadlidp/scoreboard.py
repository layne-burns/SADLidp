"""Decision scoreboard (SPEC §6.8 "Decision scoreboard", full review 2026-11-17).

The question it answers: do the picks rank the waiver pool right, and does each layer of the model earn its
place? It works on the waiver band of `docs/eval-protocol.md`, the players a position showed when you set
waivers (the pre-waiver tiers log; a rebuilt week's page). Each player is scored by every rung of the
ladder, from the frozen record:

- Sleeper alone, ESPN alone (each source's own points);
- all sources at their starting shares (60:40 projections : rankings, FantasyPros 2 : RotoBaller 1 :
  FantasyData 1, projection sources equal);
- with the accuracy shares (the record's `base`);
- with the form term (`mu`, the number the page showed).

Per position-week: rank skill is the rank correlation between a rung and the scores among band players who
played. Top-k lift is the average score of the rung's top k minus the band's average, where a player who
didn't play scores 0. That is what picking him would have got you. Rungs are compared with Sleeper alone
as paired per-week differences. Pick timing compares the Tuesday order with the kickoff order on the same
Tuesday band. Nothing here feeds the model.

Kickers (2026-11-17) have a band of their own, scored on the kicker sources (`rungs` names the two behind the
single-source rungs) and aggregated in a call of their own. The scope "all" is DL, LB and DB, always.
"""

from __future__ import annotations

from .metrics import cluster_mean, margin, spearman
from .positions import IDP_SLOTS

LADDER = (
    ("sleeper", "Sleeper alone"),
    ("espn", "ESPN alone"),
    ("blend", "All sources, starting shares"),
    ("weights", "+ accuracy shares"),
    ("form", "+ form term (our number)"),
)
KS = (1, 3, 5)
MIN_PLAYED = 5  # band players who played before a position-week's rank skill counts
IDP_RUNGS = {"sleeper": "sleeper_proj", "espn": "espn_proj"}  # the source behind each single-source rung


def prior_blend(parts: dict[str, float], kinds: dict[str, str], priors: dict[str, float], split: float) -> float | None:
    """A player's number at the starting shares, from his frozen record's source parts (§6.4, §6.5 priors)."""
    groups: dict[str, list[tuple[float, float]]] = {"projection": [], "rank": []}
    for name, v in parts.items():
        if v is not None and kinds.get(name) in groups:
            groups[kinds[name]].append((float(v), float(priors.get(name, 1.0))))
    means = {g: sum(v * w for v, w in xs) / sum(w for _, w in xs) for g, xs in groups.items() if xs}
    if len(means) == 2:
        return split * means["projection"] + (1 - split) * means["rank"]
    return next(iter(means.values()), None)


def band_rows(shown: dict[str, list[str]], frozen: dict, played: dict[str, float], tuesday: dict[str, float],
              kinds: dict[str, str], priors: dict[str, float], split: float,
              rungs: dict[str, str] = IDP_RUNGS) -> dict[str, list[dict]]:  # fmt: skip
    """Per position, the shown band: each player's score (0 if he didn't play), whether he played, his
    Tuesday μ and his value at every rung of the ladder (None where the record has none). `rungs` names the
    source behind the two single-source rungs; `kinds` and `priors` are those of the same group's sources."""
    out = {}
    for slot, pids in shown.items():
        rows = []
        for pid in pids:
            f = frozen.get(pid) or {}
            parts = f.get("parts") or {}
            vals = {
                "sleeper": parts.get(rungs["sleeper"]),
                "espn": parts.get(rungs["espn"]),
                "blend": prior_blend(parts, kinds, priors, split) if parts else None,
                "weights": f.get("base", f.get("mu")),
                "form": f.get("mu"),
            }
            rows.append({"pid": pid, "pts": played.get(pid, 0.0), "played": pid in played,
                         "tue": tuesday.get(pid), "vals": vals})  # fmt: skip
        out[slot] = rows
    return out


def _lift(rows: list[dict], key, k: int) -> float | None:
    """Average score of the top k by `key` (missing values last) minus the band's average; DNP = 0."""
    if len(rows) < k:
        return None
    order = sorted(rows, key=lambda r: (key(r) is None, -(key(r) or 0.0), r["pid"]))
    return sum(r["pts"] for r in order[:k]) / k - sum(r["pts"] for r in rows) / len(rows)


def _skill(rows: list[dict], key) -> float | None:
    pairs = [(key(r), r["pts"]) for r in rows if r["played"] and key(r) is not None]
    if len(pairs) < MIN_PLAYED:
        return None
    return spearman([p[0] for p in pairs], [p[1] for p in pairs], MIN_PLAYED)


def week_stats(bands: dict[str, list[dict]]) -> dict[str, dict]:
    """Per position: rank skill and top-k lift per rung, and Tuesday vs kickoff lift, for one week."""
    out = {}
    for slot, rows in bands.items():
        if not rows:
            continue
        rungs = {}
        for key, _ in LADDER:
            get = lambda r, key=key: r["vals"][key]  # noqa: E731
            rungs[key] = {"r": _skill(rows, get), **{f"top{k}": _lift(rows, get, k) for k in KS}}
        timing = None
        if all(r["tue"] is not None for r in rows):

            def tue(r):
                return r["tue"]

            def kick(r):
                return r["vals"]["form"]

            played = [r for r in rows if r["played"]]
            top5_tue = sorted(rows, key=lambda r: (-r["tue"], r["pid"]))[:5]
            timing = {"tue": _lift(rows, tue, 5), "kick": _lift(rows, kick, 5),
                      "tue_played": _lift(played, tue, 5), "kick_played": _lift(played, kick, 5),
                      "dnp_top5_tue": sum(not r["played"] for r in top5_tue)}  # fmt: skip
        out[slot] = {"rungs": rungs, "timing": timing, "n": len(rows)}
    return out


def _summ(xs: list[float]) -> dict:
    m, se = cluster_mean([[x] for x in xs])
    return {"mean": m, "margin": margin(se, len(xs)), "weeks": len(xs)}


def aggregate(weeks: dict[int, dict[str, dict]], positions: tuple[str, ...] = IDP_SLOTS) -> dict:
    """Across weeks: per position and for all positions together (a week's three positions averaged first, so
    the week is the unit), each rung's rank skill, its paired difference with Sleeper alone, and its lifts;
    and the Tuesday vs kickoff pick lift on live weeks. "all" is always DL, LB and DB and nothing else:
    kickers are aggregated in a call of their own (`positions=("K",)`), whose "all" is not used."""
    out = {}
    for scope in (*positions, "all"):
        slots = IDP_SLOTS if scope == "all" else (scope,)
        rows = []
        for key, label in LADDER:
            r_w, d_w, lifts = [], [], {k: [] for k in KS}
            for ws in weeks.values():
                rs = [ws[s]["rungs"][key]["r"] for s in slots if s in ws and ws[s]["rungs"][key]["r"] is not None]
                both = [ws[s]["rungs"] for s in slots if s in ws]
                ds = [g[key]["r"] - g["sleeper"]["r"] for g in both if g[key]["r"] is not None
                      and g["sleeper"]["r"] is not None]  # fmt: skip
                if rs:
                    r_w.append(sum(rs) / len(rs))
                if ds:
                    d_w.append(sum(ds) / len(ds))
                for k in KS:
                    ls = [ws[s]["rungs"][key][f"top{k}"] for s in slots
                          if s in ws and ws[s]["rungs"][key][f"top{k}"] is not None]  # fmt: skip
                    if ls:
                        lifts[k].append(sum(ls) / len(ls))
            rows.append({"key": key, "label": label, "r": _summ(r_w),
                         "vs_sleeper": _summ(d_w) if key != "sleeper" else None,
                         **{f"top{k}": _summ(v) for k, v in lifts.items()}})  # fmt: skip
        timing = {}
        for part in ("tue", "kick", "tue_played", "kick_played"):
            xs = []
            for ws in weeks.values():
                vs = [ws[s]["timing"][part] for s in slots if s in ws and ws[s]["timing"]
                      and ws[s]["timing"][part] is not None]  # fmt: skip
                if vs:
                    xs.append(sum(vs) / len(vs))
            timing[part] = _summ(xs)
        diff = []
        for ws in weeks.values():
            vs = [ws[s]["timing"]["kick"] - ws[s]["timing"]["tue"] for s in slots if s in ws and ws[s]["timing"]
                  and ws[s]["timing"]["kick"] is not None and ws[s]["timing"]["tue"] is not None]  # fmt: skip
            if vs:
                diff.append(sum(vs) / len(vs))
        timing["kick_minus_tue"] = _summ(diff)
        timing["dnp_top5_tue"] = sum(ws[s]["timing"]["dnp_top5_tue"] for ws in weeks.values() for s in slots
                                     if s in ws and ws[s]["timing"])  # fmt: skip
        timing["top5_tue"] = 5 * sum(1 for ws in weeks.values() for s in slots if s in ws and ws[s]["timing"])
        out[scope] = {"rows": rows, "timing": timing, "weeks": sorted(weeks)}
    return out
