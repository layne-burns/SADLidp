"""Consensus mean and projection uncertainty (SPEC.md §6.4).

μ_i = α·P_i + (1−α)·R_i (or whichever group exists), each group a weighted mean of the sources that
cover the player. σ_μ is the between-source SD around μ, shrunk toward a position-typical s0 with ν0
pseudo-sources, floored per position; it is NOT divided by n_eff (sources are correlated) and never
includes game-outcome variance. A single-source player gets σ = max(s0, floor).
"""

from __future__ import annotations

import math
import statistics


def consensus(
    values: dict[str, dict[str, float]],
    groups: dict[str, str],
    slot_of: dict[str, str | None],
    cfg: dict,
    weights: dict[str, float] | None = None,
    private: frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """values[source][player] = SADL points; groups[source] in {"projection", "rank"}."""
    tcfg = cfg.get("tiers") or {}
    alpha = float((cfg.get("weights") or {}).get("prior_split", 0.6))
    nu0 = float(tcfg.get("nu0", 2))
    s0_default = float(tcfg.get("s0_default", 1.0))
    floors = tcfg.get("sigma_floor") or {}
    weights = weights or {}

    raw: dict[str, dict] = {}
    for pid in sorted({p for vals in values.values() for p in vals}):
        covering = [(src, vals[pid]) for src, vals in values.items() if pid in vals]
        by_group: dict[str, list[tuple[str, float]]] = {}
        for src, x in covering:
            by_group.setdefault(groups[src], []).append((src, x))
        group_share = {"projection": alpha, "rank": 1 - alpha} if len(by_group) == 2 else {g: 1.0 for g in by_group}
        eff: dict[str, float] = {}
        mu = 0.0
        for g, members in by_group.items():
            total_w = sum(weights.get(s, 1.0) for s, _ in members)
            for s, x in members:
                eff[s] = group_share[g] * weights.get(s, 1.0) / total_w
                mu += eff[s] * x
        s2 = sum(eff[s] * (x - mu) ** 2 for s, x in covering)
        public = [x for s, x in covering if s not in private]
        raw[pid] = {
            "mu": mu,
            "s2": s2,
            "n_sources": len(covering),
            "min": min(public) if public else None,
            "max": max(public) if public else None,
            "eff": {src: round(w, 4) for src, w in eff.items()},  # each source's share of μ (D19 breakdown)
        }

    s0_sq: dict[str | None, float] = {}
    for slot in {slot_of.get(pid) for pid in raw}:
        multi = [r["s2"] for pid, r in raw.items() if slot_of.get(pid) == slot and r["n_sources"] >= 3]
        s0_sq[slot] = statistics.median(multi) if multi else s0_default**2

    out: dict[str, dict] = {}
    for pid, r in raw.items():
        slot = slot_of.get(pid)
        n = r["n_sources"]
        shrunk = (nu0 * s0_sq[slot] + (n - 1) * r["s2"]) / (nu0 + n - 1)
        sigma = max(math.sqrt(shrunk), float(floors.get(slot, 0.75)))
        out[pid] = {**{k: v for k, v in r.items() if k != "s2"}, "sigma": sigma}
    return out
