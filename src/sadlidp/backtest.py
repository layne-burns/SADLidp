"""Last-season backtest (owner request 2026-10-12, docs/methods-review.md).

Sleeper still serves every week of last season's IDP projections and final stats. `sadlidp backtest
fetch` stores them compactly under `data/<season>/archive/week<NN>.json` (one player per line: slot,
team, projected IDP stats, the stat line if he played, defensive snaps). `sadlidp backtest run`
scores both under the current SADL settings and runs the standard checks on ~12,000 player-games:

- **skill**: within-week correlation and slope of projection vs points, per position, for the top 50,
  the waiver band (ranks 15-60 by projection) and everyone;
- **form**: whether a player's earlier misses (shrunk by n/(n+k)) and his last game's defensive snap
  share add to the projection, fitted on odd weeks and scored on even weeks;
- **swing**: a per-player outcome SD from the projected stat mix (Poisson), swing² = a + b·V fitted per
  position, against one SD per position: calibration by fifth, the pairwise "who outscores whom" Brier
  score, and the `[swing]` settings (a, b and the 1-in-5 band) the page applies (SPEC §6.7);
- **sacks**: whether a player's sack excess persists, and the sack projections' calibration;
- **coefficients**: the `[form]` settings the page applies (SPEC §6.4), refitted on the whole archive.

Every number is within-week (the week's average is removed from both sides first). Nothing here feeds
the live page; it is where an idea earns its place before it goes into the consensus.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from . import scoring, storage, weeklog
from .metrics import brier, pairwise
from .positions import IDP_SLOTS, slot_of
from .schedule import ET
from .sleeper import Sleeper
from .swing import poisson_var

WEEKS = range(1, 19)
BANDS = (("top 50", 0, 50), ("ranks 15-60", 15, 60), ("all", 0, None))
FIT_TOP = 60  # the form coefficients are fitted on each position's 60 best-projected players (form.TOP)
SNAP_KEYS = ("def_snp", "tm_def_snp")


def archive_dir(data_dir: Path | str, season: int) -> Path:
    return Path(data_dir) / str(season) / "archive"


def _compact(stats: dict, keys: Iterable[str], digits: int = 2) -> dict:
    out = {}
    for k in keys:
        v = scoring.num(stats.get(k))
        if v:
            out[k] = round(v, digits)
    return out


def fetch_week(sleeper: Sleeper, season: int, week: int, players: dict, sc: dict) -> dict | None:
    """One week's archive: every player with a non-zero projection or a stat line, or None if either
    endpoint failed. Projected and actual stats keep only the IDP keys SADL scores (and snaps)."""
    proj, err = sleeper.projections(season, week)
    if not isinstance(proj, list):
        return None
    stats, err = sleeper.stats(season, week)
    if not isinstance(stats, dict):
        return None
    keys = [k for k in sc if k.startswith("idp_")]
    rows: dict[str, dict] = {}
    for r in proj:
        pid = str(r.get("player_id") or "")
        slot = slot_of(r.get("player") or players.get(pid) or {})
        p = _compact(r.get("stats") or {}, keys)
        if not pid or slot is None or not p:
            continue
        rows[pid] = {"slot": slot, "team": (r.get("player") or {}).get("team"), "proj": p}
    for pid, line in stats.items():
        if not isinstance(line, dict) or not weeklog.played(line):
            continue
        pid = str(pid)
        row = rows.get(pid)
        if row is None:
            slot = slot_of(players.get(pid) or {})
            if slot is None:
                continue
            row = rows[pid] = {"slot": slot, "team": None, "proj": {}}
        row["act"] = {"gp": 1, **_compact(line, keys, 1)}
        if scoring.num(line.get("tm_def_snp")):
            row["snap"] = [int(scoring.num(line.get("def_snp"))), int(scoring.num(line.get("tm_def_snp")))]
    return {"season": season, "week": week, "source": "sleeper_proj", "fetched_at": datetime.now(ET).isoformat(),
            "rows": rows}  # fmt: skip


def fetch(sleeper: Sleeper, data_dir: Path | str, season: int, players: dict, sc: dict,
          log: Callable[[str], None] = print, redo: bool = False) -> list[int]:  # fmt: skip
    """Store every week of `season` not yet archived. Returns the weeks written."""
    written = []
    for week in WEEKS:
        path = archive_dir(data_dir, season) / f"week{week:02d}.json"
        if path.exists() and not redo:
            continue
        doc = fetch_week(sleeper, season, week, players, sc)
        if doc is None:
            log(f"backtest: {season} week {week}: unavailable; stopped")
            break
        storage.write_json(path, doc)
        played = sum(1 for r in doc["rows"].values() if r.get("act"))
        log(f"backtest: {season} week {week}: {len(doc['rows'])} rows, {played} played")
        written.append(week)
    return written


def load(data_dir: Path | str, season: int, sc: dict) -> list[dict]:
    """Player-weeks with a projection, scored under `sc`: week, pid, slot, proj, ps (projected stats),
    act (None if he didn't play), snap (share of the team's defensive snaps), sack."""
    rows = []
    for path in sorted(archive_dir(data_dir, season).glob("week*.json")):
        doc = storage.read_json(path) or {}
        for pid, r in (doc.get("rows") or {}).items():
            if not r.get("proj"):
                continue
            act = r.get("act")
            snap = r.get("snap")
            rows.append({
                "week": doc["week"], "pid": pid, "slot": r["slot"],
                "proj": scoring.projected_points(r["proj"], sc), "ps": r["proj"],
                "act": scoring.points(act, sc) if act else None,
                "snap": snap[0] / snap[1] if snap and snap[1] else None,
                "sack": scoring.num(act.get("idp_sack")) if act else None,
            })  # fmt: skip
    return rows


# ---- helpers -------------------------------------------------------------------------------------


def band(rows: list[dict], slot: str, lo: int, hi: int | None) -> list[dict]:
    """Per week, the players at `slot` ranked lo..hi by projection among those who played."""
    out = []
    for w in sorted({r["week"] for r in rows}):
        g = sorted((r for r in rows if r["week"] == w and r["slot"] == slot and r["act"] is not None),
                   key=lambda r: (-r["proj"], r["pid"]))  # fmt: skip
        out += g[lo:hi]
    return out


def demean(rows: list[dict], cols: dict[str, Callable[[dict], float]]) -> dict[str, list[float]]:
    """Each column with its week's average removed, aligned across columns."""
    out: dict[str, list[float]] = {name: [] for name in cols}
    for w in sorted({r["week"] for r in rows}):
        g = [r for r in rows if r["week"] == w]
        if len(g) < 2:
            continue
        for name, f in cols.items():
            vals = [f(r) for r in g]
            m = statistics.fmean(vals)
            out[name] += [v - m for v in vals]
    return out


def corr(x: list[float], y: list[float]) -> float | None:
    try:
        return statistics.correlation(x, y) if len(x) >= 3 else None
    except statistics.StatisticsError:
        return None


def lstsq(X: list[list[float]], y: list[float]) -> list[float]:
    """Least squares for a handful of regressors (normal equations, Gaussian elimination)."""
    k = len(X[0]) if X else 0
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] + [sum(r[i] * v for r, v in zip(X, y, strict=True))]
         for i in range(k)]  # fmt: skip
    for i in range(k):
        p = max(range(i, k), key=lambda r: abs(A[r][i]))
        A[i], A[p] = A[p], A[i]
        if abs(A[i][i]) < 1e-12:
            return [0.0] * k
        for r in range(k):
            if r != i:
                f = A[r][i] / A[i][i]
                A[r] = [a - f * b for a, b in zip(A[r], A[i], strict=True)]
    return [A[i][k] / A[i][i] for i in range(k)]


def held_out(rows: list[dict], cols: list[Callable[[dict], float]]) -> dict:
    """Fit on odd weeks, score on even weeks: r of the fitted prediction with points, its MAE and the
    coefficients. The first column is the projection itself."""
    d = demean(rows, {**{f"x{i}": f for i, f in enumerate(cols)}, "y": lambda r: r["act"],
                      "w": lambda r: r["week"] % 2})  # fmt: skip
    # demeaning zeroes the week parity within a week, so carry it separately
    parity = [r["week"] % 2 for w in sorted({r["week"] for r in rows}) for r in rows if r["week"] == w
              and sum(1 for q in rows if q["week"] == w) >= 2]  # fmt: skip
    X = [[d[f"x{i}"][n] for i in range(len(cols))] for n in range(len(d["y"]))]
    y = d["y"]
    train = [n for n in range(len(y)) if parity[n] == 1]
    test = [n for n in range(len(y)) if parity[n] == 0]
    if len(train) < 10 or len(test) < 10:
        return {"n": len(y), "r": None, "mae": None, "beta": []}
    beta = lstsq([X[n] for n in train], [y[n] for n in train])
    pred = [sum(b * x for b, x in zip(beta, X[n], strict=True)) for n in test]
    actual = [y[n] for n in test]
    mae = statistics.fmean(abs(a - p) for a, p in zip(actual, pred, strict=True))
    return {"n": len(test), "r": corr(pred, actual), "mae": mae, "beta": beta}


def add_history(rows: list[dict], k: float = 4.0) -> None:
    """Per player-week, from his earlier games this season: `form` = mean miss shrunk by n/(n+k),
    `lsnap` = last game's snap share, `sack_form` = mean sack excess, `games` = earlier games."""
    hist: dict[str, list[float]] = {}
    sacks: dict[str, list[float]] = {}
    snaps: dict[str, float] = {}
    for w in sorted({r["week"] for r in rows}):
        wk = [r for r in rows if r["week"] == w]
        for r in wk:
            h = hist.get(r["pid"], [])
            r["games"] = len(h)
            r["form"] = len(h) / (len(h) + k) * statistics.fmean(h) if h else 0.0
            r["lsnap"] = snaps.get(r["pid"])
            s = sacks.get(r["pid"], [])
            r["sack_form"] = statistics.fmean(s) if s else 0.0
        for r in wk:
            if r["act"] is None:
                continue
            hist.setdefault(r["pid"], []).append(r["act"] - r["proj"])
            sacks.setdefault(r["pid"], []).append(r["sack"] - scoring.num(r["ps"].get("idp_sack")))
            if r["snap"] is not None:
                snaps[r["pid"]] = r["snap"]


def fit_form(rows: list[dict], k: float = 4.0) -> dict[str, dict]:
    """Per position, the `[form]` coefficients: least squares of (points − projection) on the shrunk earlier
    miss and the last snap share, both within week, over the top `FIT_TOP` by projection in every week, the
    projection's own coefficient held at 1 as the page applies it. Call `add_history(rows, k)` first."""
    out = {}
    for slot in IDP_SLOTS:
        g = [r for r in band(rows, slot, 0, FIT_TOP) if r["lsnap"] is not None]
        d = demean(g, {"f": lambda r: r["form"], "s": lambda r: r["lsnap"], "y": lambda r: r["act"] - r["proj"]})
        X = [[f, s] for f, s in zip(d["f"], d["s"], strict=True)]
        beta = lstsq(X, d["y"]) if len(X) >= 10 else [0.0, 0.0]
        out[slot] = {"miss": beta[0], "snap": beta[1], "n": len(X)}
    return out


# ---- the report ----------------------------------------------------------------------------------


def report(rows: list[dict], sc: dict, k: float = 4.0) -> dict:
    add_history(rows, k)
    out: dict = {"weeks": sorted({r["week"] for r in rows}), "n": sum(1 for r in rows if r["act"] is not None),
                 "skill": {}, "form": {}, "swing": {}, "sacks": {}}  # fmt: skip
    for slot in IDP_SLOTS:
        out["skill"][slot] = {}
        out["form"][slot] = {}
        for name, lo, hi in BANDS:
            g = band(rows, slot, lo, hi)
            d = demean(g, {"p": lambda r: r["proj"], "a": lambda r: r["act"]})
            x, y = d["p"], d["a"]
            out["skill"][slot][name] = {
                "n": len(x), "r": corr(x, y), "slope": _slope(x, y),
                "sd_proj": statistics.pstdev(x) if x else None, "sd_act": statistics.pstdev(y) if y else None,
            }  # fmt: skip
            if name == "all":
                continue
            gs = [r for r in g if r["lsnap"] is not None]
            out["form"][slot][name] = {
                "proj": held_out(gs, [lambda r: r["proj"]]),
                "form": held_out(gs, [lambda r: r["proj"], lambda r: r["form"]]),
                "snap": held_out(gs, [lambda r: r["proj"], lambda r: r["lsnap"]]),
                "both": held_out(gs, [lambda r: r["proj"], lambda r: r["form"], lambda r: r["lsnap"]]),
            }
        # swing: players projected 3+ who played; swing² = a + b·V, V the Poisson variance of the stat mix
        g = [r for r in rows if r["slot"] == slot and r["act"] is not None and r["proj"] >= 3]
        g.sort(key=lambda r: poisson_var(r["ps"], sc))
        e = [r["act"] - r["proj"] for r in g]
        v = [poisson_var(r["ps"], sc) for r in g]
        if len(g) >= 50 and sum(v):
            a, b = lstsq([[1.0, x] for x in v], [x * x for x in e])
            a, b = max(a, 0.0), max(b, 0.0)

            def swing_of(r: dict, a: float = a, b: float = b) -> float:
                return math.sqrt(max(a + b * poisson_var(r["ps"], sc), 1e-6))

            fifths = []
            step = len(g) / 5
            for i in range(5):
                sl = slice(int(i * step), int((i + 1) * step))
                fifths.append(
                    {"predicted": math.sqrt(a + b * statistics.fmean(v[sl])), "realized": statistics.pstdev(e[sl])}
                )
            z = sorted(x / swing_of(r) for x, r in zip(e, g, strict=True))
            sd0 = statistics.pstdev(e)
            pairs0, pairs1 = [], []
            for w in out["weeks"]:
                gw = sorted((r for r in g if r["week"] == w), key=lambda r: (-r["proj"], r["pid"]))[:50]
                mu = {r["pid"]: r["proj"] for r in gw}
                act = {r["pid"]: r["act"] for r in gw}
                pairs0 += pairwise(mu, dict.fromkeys(mu, sd0), act)
                pairs1 += pairwise(mu, {r["pid"]: swing_of(r) for r in gw}, act)
            out["swing"][slot] = {"n": len(g), "a": a, "b": b, "sd": sd0, "fifths": fifths, "pairs": len(pairs0),
                                  "q20": z[len(z) // 5], "q80": z[len(z) * 4 // 5],
                                  "brier_const": brier(pairs0), "brier_player": brier(pairs1)}  # fmt: skip
    # sacks: DL + LB with 3+ earlier games
    g = [r for r in rows if r["slot"] != "DB" and r["act"] is not None]
    gh = [r for r in g if r["games"] >= 3]
    x = [r["sack_form"] for r in gh]
    y = [r["sack"] - scoring.num(r["ps"].get("idp_sack")) for r in gh]
    bins = []
    for lo, hi in ((0, 0.1), (0.1, 0.25), (0.25, 0.4), (0.4, 0.6), (0.6, 9)):
        gb = [r for r in g if lo <= scoring.num(r["ps"].get("idp_sack")) < hi]
        if gb:
            proj = statistics.fmean(scoring.num(r["ps"].get("idp_sack")) for r in gb)
            bins.append(
                {"lo": lo, "hi": hi, "n": len(gb), "proj": proj, "act": statistics.fmean(r["sack"] for r in gb)}
            )
    out["sacks"] = {"n": len(gh), "r": corr(x, y), "slope": _slope(x, y), "bins": bins}
    out["coef"] = fit_form(rows, k)
    return out


def _slope(x: list[float], y: list[float]) -> float | None:
    sxx = sum(v * v for v in x)
    return sum(a * b for a, b in zip(x, y, strict=True)) / sxx if sxx else None


def _r(v: float | None, n: int = 0) -> str:
    if v is None:
        return "   —  "
    return f"{v:+.2f}" + (f"±{2 / math.sqrt(n - 3):.2f}" if n > 4 else "")


def format_report(rep: dict, season: int) -> str:
    out = [
        f"{season} backtest: weeks {rep['weeks'][0]}–{rep['weeks'][-1]}, {rep['n']} player-games with a projection",
        "",
        "Skill (within-week r of projection with points; slope < 1: differences less real than projected):",
    ]
    for slot in IDP_SLOTS:
        cells = []
        for name, _, _ in BANDS:
            s = rep["skill"][slot][name]
            cells.append(
                f"{name}: r {_r(s['r'], s['n'])} slope {s['slope']:.2f} (n {s['n']})"
                if s["slope"] is not None
                else f"{name}: —"
            )
        out.append(f"  {slot}: " + " | ".join(cells))
    out += ["", "Form (held out: fit on odd weeks, scored on even; r of the prediction with points):",
            "  band          proj     +form (β)        +snap (pts/100%)   both      MAE proj → both"]  # fmt: skip
    for slot in IDP_SLOTS:
        for name, _, _ in BANDS[:2]:
            f = rep["form"][slot].get(name)
            if not f or f["proj"]["r"] is None:
                continue
            bf, bs = f["form"]["beta"][1], f["snap"]["beta"][1]
            out.append(f"  {slot} {name:<12} {_r(f['proj']['r'])}   {_r(f['form']['r'])} ({bf:+.2f})   "
                       f"{_r(f['snap']['r'])} ({bs:+.1f})      {_r(f['both']['r'])}   "
                       f"{f['proj']['mae']:.2f} → {f['both']['mae']:.2f}   (n {f['proj']['n']})")  # fmt: skip
    out += ["", "Swing (per-player SD from the projected stat mix vs one SD per position):"]
    for slot in IDP_SLOTS:
        s = rep["swing"].get(slot)
        if not s:
            continue
        fifths = "  ".join(f"[{f['predicted']:.1f} → {f['realized']:.1f}]" for f in s["fifths"])
        out.append(
            f"  {slot}: n {s['n']}, one SD {s['sd']:.1f}; by fifth of predicted swing [predicted → realized]: {fifths}"
        )
        out.append(f"      top-50 pairwise Brier: one SD {s['brier_const']:.4f}, per player {s['brier_player']:.4f} "
                   f"({s['pairs']} pairs); config [swing] a {s['a']:.1f} b {s['b']:.2f}, band lo {s['q20']:+.2f} "
                   f"hi {s['q80']:+.2f}")  # fmt: skip
    s = rep["sacks"]
    out += ["", f"Sacks (DL + LB): earlier sack excess vs this week's: r {_r(s['r'], s['n'])}, "
                f"slope {s['slope']:+.2f} (n {s['n']})"]  # fmt: skip
    out.append(
        "  projected vs actual sacks per game: "
        + " | ".join(f"[{b['lo']}, {b['hi']}) {b['proj']:.2f} → {b['act']:.2f} (n {b['n']})" for b in s["bins"])
    )
    out += ["", f"Form coefficients for config [form] (top {FIT_TOP} by projection, every week, projection held at 1):",
            "  " + " | ".join(f"{slot} miss {c['miss']:+.2f}, snap {c['snap']:+.1f} per 100% (n {c['n']})"
                              for slot, c in rep["coef"].items())]  # fmt: skip
    return "\n".join(out)
