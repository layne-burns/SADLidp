"""SADL scoring (SPEC.md §6.3).

points = Σ scoring_settings[k] × stat[k] over every key, missing stats count 0. Phase 1 verified
this reproduces Sleeper's own `players_points` exactly. Threshold bonuses are supported for
projections as Poisson expectations; SADL currently scores none, so that path is dormant.
"""

from __future__ import annotations

import math

# Threshold bonus keys -> (stat, threshold), inclusive (>=).
THRESHOLD_BONUSES = {
    "bonus_tkl_10p": ("idp_tkl", 10),
    "bonus_sack_2p": ("idp_sack", 2),
    "idp_pass_def_3p": ("idp_pass_def", 3),
}


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def points(stats: dict, scoring: dict) -> float:
    """SADL points for a stat line, actual or projected (no bonus expectation)."""
    return sum(num(coef) * num(stats.get(k)) for k, coef in scoring.items() if stats.get(k) is not None)


def poisson_sf(lam: float, k: int) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k <= 0 else 0.0
    term = math.exp(-lam)
    cdf = 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def _bonus_stat(stats: dict, stat: str) -> float:
    if stat == "idp_tkl" and stats.get("idp_tkl") is None:
        return num(stats.get("idp_tkl_solo")) + num(stats.get("idp_tkl_ast"))
    return num(stats.get(stat))


def projected_points(stats: dict, scoring: dict) -> float:
    """Points for a projected line, adding E[bonus] for threshold keys SADL scores but the source omits."""
    total = points(stats, scoring)
    for key, (stat, threshold) in THRESHOLD_BONUSES.items():
        coef = num(scoring.get(key))
        if coef and stats.get(key) is None:
            total += coef * poisson_sf(_bonus_stat(stats, stat), threshold)
    return total


def idp_keys(scoring: dict) -> list[str]:
    """IDP stat keys SADL scores with a non-zero coefficient."""
    return sorted(k for k, v in scoring.items() if k.startswith("idp_") and num(v))
