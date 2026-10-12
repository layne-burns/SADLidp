"""Anchored significance tiers (D12, SPEC.md §6.6).

Players sorted by (−μ, id). The first anchors Tier 1; each next player stays in the current tier while
μ_anchor − μ_i ≤ z·√(σ_anchor² + σ_i²), otherwise he anchors a new tier. Comparing to the tier's top
player (not the previous player) prevents chaining. Rostered players never anchor; they are placed.
"""

from __future__ import annotations

import math


def _close(mu_a: float, sg_a: float, mu: float, sg: float, z: float) -> bool:
    return mu_a - mu <= z * math.sqrt(sg_a * sg_a + sg * sg)


def build_tiers(pool: dict[str, tuple[float, float]], z: float) -> list[dict]:
    """pool[player_id] = (μ, σ). Returns [{"tier": 1, "anchor": id, "mu": μ_a, "sigma": σ_a, "players": [...]}, ...]."""
    tiers: list[dict] = []
    for pid, (mu, sg) in sorted(pool.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if tiers and _close(tiers[-1]["mu"], tiers[-1]["sigma"], mu, sg, z):
            tiers[-1]["players"].append(pid)
        else:
            tiers.append({"tier": len(tiers) + 1, "anchor": pid, "mu": mu, "sigma": sg, "players": [pid]})
    return tiers


def place(mu: float, sigma: float, tiers: list[dict], z: float) -> str | int:
    """Where a rostered player sits against the waiver tiers: "above", a tier number, or "below"."""
    if not tiers:
        return "below"
    top = tiers[0]
    if mu - top["mu"] > z * math.sqrt(top["sigma"] ** 2 + sigma**2):
        return "above"
    for t in tiers:
        if _close(t["mu"], t["sigma"], mu, sigma, z):
            return t["tier"]
    return "below"
