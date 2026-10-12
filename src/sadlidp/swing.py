"""Per-player game-to-game swing (SPEC §6.7, added 2026-10-12; docs/methods-review.md step 3).

One σ_out per position says a pass rusher and a tackle machine swing alike. They don't: SADL pays 6 for
a sack and 2 for a solo tackle, so a player's week swings with his projected stat mix. If each projected
stat were an independent Poisson count, his points' variance would be V = Σ_k coef_k² · λ_k (a sack
projected at 0.5 alone adds 18). On Sleeper's 2025 archive that scales well at DL and partly at LB and
DB (`sadlidp backtest run`), so each player's swing is

    swing_i² = a_p + b_p · V_i

with a_p and b_p fitted per position on 2025 (`[swing]`) and λ the weighted mean of the projection
sources' stats for him (the breakdown's parts). START/UPGRADE odds use σ_pred² = σ_μ² + swing² in place
of the position's σ_out; a player no projection source has stats for keeps σ_out. Each card also shows
a floor and a ceiling, μ + lo·swing and μ + hi·swing, where lo and hi are the 20th and 80th percentiles
of (actual − μ)/swing on 2025: one game in five lands below the floor, one in five above the ceiling.
Tiers are unchanged (they use σ_μ, the sources' disagreement).
"""

from __future__ import annotations

import math

from . import scoring
from .positions import IDP_SLOTS


def coefficients(cfg: dict) -> dict | None:
    """The `[swing]` settings, or None when the per-player swing is off."""
    s = cfg.get("swing") or {}
    if not s.get("enabled"):
        return None
    return {
        "a": {p: float((s.get("a") or {}).get(p, 0.0)) for p in IDP_SLOTS},
        "b": {p: float((s.get("b") or {}).get(p, 1.0)) for p in IDP_SLOTS},
        "lo": float(s.get("lo", -0.8)),
        "hi": float(s.get("hi", 0.75)),
    }


def poisson_var(stats: dict, sc: dict) -> float:
    """Outcome variance if every projected stat were an independent Poisson count: Σ coef² · λ."""
    return sum(scoring.num(sc.get(k)) ** 2 * scoring.num(v) for k, v in stats.items() if k in sc)


def mix(pid: str, source_rows: dict, groups: dict, sc: dict, eff: dict | None = None) -> dict | None:
    """His projected IDP stats, averaged over the projection sources that have a row with stats for him,
    weighted by his effective source shares (`eff`, SPEC §6.4) or equally; None when no source does."""
    rows = []
    for name, rows_s in source_rows.items():
        if groups.get(name) != "projection":
            continue
        stats = ((rows_s or {}).get(pid) or {}).get("stats") or {}
        if stats:
            rows.append((name, stats))
    if not rows:
        return None
    weights = {name: float((eff or {}).get(name, 0.0)) for name, _ in rows}
    if sum(weights.values()) <= 0:
        weights = dict.fromkeys(weights, 1.0)
    total = sum(weights.values())
    out: dict[str, float] = {}
    for name, stats in rows:
        for k in scoring.idp_keys(sc):
            out[k] = out.get(k, 0.0) + weights[name] / total * scoring.num(stats.get(k))
    return out


def sigma(lam: dict, slot: str | None, coef: dict, sc: dict) -> float:
    """His swing from his projected stat mix: √(a_p + b_p · V)."""
    return math.sqrt(max(0.0, coef["a"].get(slot, 0.0) + coef["b"].get(slot, 1.0) * poisson_var(lam, sc)))


def band(mu: float, swing: float, coef: dict) -> tuple[float, float]:
    """(floor, ceiling): μ + lo·swing (never below 0) and μ + hi·swing."""
    return max(0.0, mu + coef["lo"] * swing), mu + coef["hi"] * swing


def apply(cons: dict[str, dict], slot_of: dict[str, str | None], source_rows: dict, groups: dict, sc: dict,
          coef: dict) -> dict:  # fmt: skip
    """Set `swing`, `floor` and `ceiling` on every player a projection source has stats for, in place.
    Returns what run.json records: how many players got one, and the mean swing per position."""
    sums: dict[str, list[float]] = {p: [] for p in IDP_SLOTS}
    for pid, c in cons.items():
        lam = mix(pid, source_rows, groups, sc, c.get("eff"))
        if lam is None:
            continue
        slot = slot_of.get(pid)
        s = sigma(lam, slot, coef, sc)
        lo, hi = band(c["mu"], s, coef)
        c.update(swing=round(s, 2), floor=round(lo, 1), ceiling=round(hi, 1))
        if slot in sums:
            sums[slot].append(s)
    return {"players": sum(len(v) for v in sums.values()),
            "mean": {p: round(sum(v) / len(v), 2) if v else None for p, v in sums.items()}}  # fmt: skip
