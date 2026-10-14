"""The owner's team-defense idea, backtested (D18 amended; SPEC §5.2 "D/ST signal", PR 6b).

The idea: an IDP's week depends on his defense's matchup, so the team's FantasyPros D/ST rank, or the points
Vegas expects the opponent to score, should say something the IDP projections miss. Both are tested on last
season's archive before either may enter the consensus.

`sadlidp backtest dst-fetch` stores, per week of the season, each team's FantasyPros D/ST rank (`dst.php?year=
Y&week=N`, the page as it stood before that week) and its opponent's implied points from the nflverse lines
(implied = (total ∓ spread)/2), as `data/<Y>/archive/dst.json`.

`sadlidp backtest dst` then takes each position's 60 best-projected players who played, every week, and
regresses their miss (SADL points − Sleeper's projection) on the two signals, all within week, with standard
errors clustered by team-week (teammates share a game). A signal passes at a position only if it is clear of
noise over the season (|t| ≥ 2), points the same way in both halves, and lowers the held-out miss (fit on odd
weeks, scored on even). The summary, `data/<Y>/dst_backtest.json`, is shown on the Stats page.
"""

from __future__ import annotations

import csv
import io
import math
import statistics
from datetime import datetime
from pathlib import Path

from . import backtest, storage
from .config import load_alias
from .positions import IDP_SLOTS
from .schedule import ET, NFLVERSE_GAMES
from .sources.fp_ecr import parse_ecr, team_of

URL = "https://www.fantasypros.com/nfl/rankings/dst.php?year={year}&week={week}"
SIGNALS = (("rank", "team D/ST rank (1 = best matchup)"), ("opp_implied", "opponent's implied points"))
T_PASS = 2.0


def path_of(data_dir: Path | str, season: int) -> Path:
    return backtest.archive_dir(data_dir, season) / "dst.json"


def lines(csv_text: str, season: int, aliases: dict[str, str]) -> dict[int, dict[str, dict]]:
    """week -> team -> {opp_implied, total} from nflverse games.csv (spread_line > 0: the home team favoured)."""
    out: dict[int, dict[str, dict]] = {}
    for g in csv.DictReader(io.StringIO(csv_text)):
        if g.get("season") != str(season) or g.get("game_type") != "REG" or not g.get("total_line"):
            continue
        try:
            week, total, spread = int(g["week"]), float(g["total_line"]), float(g["spread_line"])
        except (KeyError, ValueError):
            continue
        home, away = (aliases.get(g[k], g[k]) for k in ("home_team", "away_team"))
        out.setdefault(week, {})[home] = {"opp_implied": (total - spread) / 2, "total": total}
        out.setdefault(week, {})[away] = {"opp_implied": (total + spread) / 2, "total": total}
    return out


def fetch(client, data_dir: Path | str, season: int, cfg: dict, log=print) -> Path:
    """Each week's D/ST ranks and lines (a failed week is left out and said so)."""
    teams = load_alias(cfg, "fp_teams")
    resp = client.get(NFLVERSE_GAMES)
    if not resp.ok:
        raise RuntimeError(f"nflverse games.csv: {resp.error or resp.status}")
    by_week = lines(resp.text(), season, load_alias(cfg, "nflverse_teams"))
    weeks: dict[str, dict] = {}
    for week in backtest.WEEKS:
        r = client.get(URL.format(year=season, week=week))
        data = parse_ecr(r.text()) if r.ok else None
        if not data or (str(data.get("year")), str(data.get("week"))) != (str(season), str(week)):
            log(f"backtest dst: week {week}: no D/ST list for {season} ({r.error or r.status}); left out")
            continue
        ranks = {}
        for p in data.get("players") or []:
            team = team_of((p or {}).get("player_team_id"), teams) if isinstance(p, dict) else None
            if team and isinstance(p.get("rank_ecr"), int | float):
                ranks[team] = p["rank_ecr"]
        weeks[str(week)] = {t: {"rank": ranks[t], **(by_week.get(week) or {}).get(t, {})} for t in ranks}
    path = path_of(data_dir, season)
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write_json(path, {"season": season, "fetched_at": datetime.now(ET).isoformat(), "weeks": weeks})
    return path


def _ols_clustered(rows: list[dict], cols: list[str]) -> dict:
    """Within-week OLS of the miss on `cols`, standard errors clustered by team-week (sandwich estimator)."""
    X, y, cl = [], [], []
    for w in sorted({r["week"] for r in rows}):
        g = [r for r in rows if r["week"] == w]
        if len(g) < 2:
            continue
        means = {c: statistics.fmean(r[c] for r in g) for c in cols}
        my = statistics.fmean(r["act"] - r["proj"] for r in g)
        for r in g:
            X.append([r[c] - means[c] for c in cols])
            y.append(r["act"] - r["proj"] - my)
            cl.append((w, r["team"]))
    k = len(cols)
    if len(y) <= k + 10:
        return {"n": len(y), "beta": None}
    beta = backtest.lstsq(X, y)
    xtx = [[sum(x[i] * x[j] for x in X) for j in range(k)] for i in range(k)]
    inv = _inverse(xtx)
    scores: dict = {}
    for x, v, c in zip(X, y, cl, strict=True):
        e = v - sum(b * xi for b, xi in zip(beta, x, strict=True))
        s = scores.setdefault(c, [0.0] * k)
        for i in range(k):
            s[i] += x[i] * e
    meat = [[sum(s[i] * s[j] for s in scores.values()) for j in range(k)] for i in range(k)]
    var = [[sum(inv[i][a] * meat[a][b] * inv[b][j] for a in range(k) for b in range(k)) for j in range(k)]
           for i in range(k)]  # fmt: skip
    se = [math.sqrt(max(var[i][i], 0.0)) for i in range(k)]
    return {"n": len(y), "clusters": len(scores), "beta": dict(zip(cols, beta, strict=True)),
            "se": dict(zip(cols, se, strict=True)),
            "t": {c: (b / s if s else None) for c, b, s in zip(cols, beta, se, strict=True)}}  # fmt: skip


def _inverse(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        piv = m[i][i] or 1e-12
        m[i] = [v / piv for v in m[i]]
        for r in range(n):
            if r != i:
                f = m[r][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i], strict=True)]
    return [row[n:] for row in m]


def run(data_dir: Path | str, season: int, sc: dict) -> dict | None:
    doc = storage.read_json(path_of(data_dir, season))
    if not doc:
        return None
    signals = {(int(w), t): v for w, teams in (doc.get("weeks") or {}).items() for t, v in teams.items()}
    teams = {}
    for path in sorted(backtest.archive_dir(data_dir, season).glob("week*.json")):
        wk = storage.read_json(path) or {}
        teams.update({(wk["week"], pid): r.get("team") for pid, r in (wk.get("rows") or {}).items()})
    rows = []
    for r in backtest.load(data_dir, season, sc):
        team = teams.get((r["week"], r["pid"]))
        s = signals.get((r["week"], team)) or {}
        if team and s.get("rank") is not None and s.get("opp_implied") is not None:
            rows.append({**r, "team": team, "rank": float(s["rank"]), "opp_implied": float(s["opp_implied"])})
    cols = [c for c, _ in SIGNALS]
    out: dict = {"season": season, "weeks": sorted({r["week"] for r in rows}), "positions": {}}
    for slot in IDP_SLOTS:
        g = backtest.band(rows, slot, 0, backtest.FIT_TOP)
        if not g:
            continue
        mid = (min(r["week"] for r in g) + max(r["week"] for r in g)) // 2
        fits = {"season": _ols_clustered(g, cols),
                "first half": _ols_clustered([r for r in g if r["week"] <= mid], cols),
                "second half": _ols_clustered([r for r in g if r["week"] > mid], cols)}  # fmt: skip
        base = backtest.held_out(g, [lambda r: r["proj"]])
        both = backtest.held_out(g, [lambda r: r["proj"], *(lambda r, c=c: r[c] for c in cols)])
        verdict = {}
        for c in cols:
            s, h1, h2 = fits["season"], fits["first half"], fits["second half"]
            clear = s.get("t") and s["t"][c] is not None and abs(s["t"][c]) >= T_PASS
            same = all(f.get("beta") for f in (h1, h2)) and (h1["beta"][c] > 0) == (h2["beta"][c] > 0)
            verdict[c] = bool(clear and same and both["mae"] is not None and both["mae"] < base["mae"])
        out["positions"][slot] = {"fits": fits, "held_out": {"projection": base, "with signals": both},
                                  "passes": verdict}  # fmt: skip
    out["passes"] = any(v for p in out["positions"].values() for v in p["passes"].values())
    return out


def summary(rep: dict) -> dict:
    """What the Stats page shows (`data/<Y>/dst_backtest.json`)."""
    pos = {}
    for slot, p in rep["positions"].items():
        s = p["fits"]["season"]
        pos[slot] = {
            "n": s["n"],
            "signals": {c: {"per_unit": round(s["beta"][c], 3), "t": round(s["t"][c], 1),
                            "halves_agree": (p["fits"]["first half"]["beta"][c] > 0)
                            == (p["fits"]["second half"]["beta"][c] > 0),
                            "passes": p["passes"][c]} for c, _ in SIGNALS},
            "mae": {"projection": round(p["held_out"]["projection"]["mae"], 3),
                    "with signals": round(p["held_out"]["with signals"]["mae"], 3)},
        }  # fmt: skip
    return {"season": rep["season"], "weeks": rep["weeks"], "passes": rep["passes"], "positions": pos}


def format_report(rep: dict) -> str:
    out = [f"Team-defense backtest, {rep['season']} weeks {rep['weeks'][0]}–{rep['weeks'][-1]}: the 60 best-projected "
           "players per position each week; the miss (points − projection) on each signal, within week, "
           "standard errors clustered by team-week.", ""]  # fmt: skip
    for slot, p in rep["positions"].items():
        out.append(f"{slot} (n {p['fits']['season']['n']}, {p['fits']['season'].get('clusters')} team-weeks):")
        for c, label in SIGNALS:
            parts = []
            for name, f in p["fits"].items():
                if f.get("beta"):
                    parts.append(f"{name} {f['beta'][c]:+.3f} (t {f['t'][c]:+.1f})")
            out.append(f"  {label}: " + ", ".join(parts) + ("  → passes" if p["passes"][c] else "  → no"))
        b, w = p["held_out"]["projection"], p["held_out"]["with signals"]
        out.append(f"  held out (fit odd weeks, score even): MAE {b['mae']:.3f} → {w['mae']:.3f}, "
                   f"r {b['r']:+.3f} → {w['r']:+.3f}")  # fmt: skip
    verdict = "a signal passes; see above." if rep["passes"] else "neither signal passes at any position: it stays out."
    out += ["", f"Verdict: {verdict}"]
    return "\n".join(out)
