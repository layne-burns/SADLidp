"""Rank → value for rank sources (SPEC.md §6.4), as pure functions.

Within one list, V is the projection-group values P of the list's members that have one, sorted descending and
indexed from 1. A member's effective rank counts only V members above him; rank r maps to V_r (linear between
neighbours for fractional r), r > |V| to min(V_|V|, Q_min(⌈r⌉,|Q|)) with Q the P values of the full projection
pool at the list's position, and Q replaces V when |V| < 10. Censoring gives an unlisted, observed player whose
P⁰-rank is ≤ D+M the rank max(D+1, P⁰-rank). A player in several lists takes the one for his D14 slot.
"""

from __future__ import annotations

import math

MIN_V = 10  # below this many valued members, Q replaces V (rank to rank)
LIST_ORDER = ("DL", "LB", "DB")


def _interp(curve: list[float], r: float) -> float:
    """curve_r for 1 ≤ r ≤ |curve|, linear between neighbours (curve is indexed from 1)."""
    r = max(1.0, r)
    f = int(math.floor(r))
    t = r - f
    lo = curve[f - 1]
    return lo if t == 0 or f >= len(curve) else lo + t * (curve[f] - lo)


def rank_value(r: float, V: list[float], Q: list[float]) -> float | None:
    """Value of rank r on the curve V (desc), with the tail and small-V rules on Q (desc); None without either."""
    if len(V) < MIN_V and Q:
        V, Q = Q, Q
    if not V:
        return None
    if r <= len(V):
        return _interp(V, r)
    tail = V[-1]
    return min(tail, Q[min(math.ceil(r), len(Q)) - 1]) if Q else tail


def effective_ranks(ranks: dict[str, float], valued: set[str]) -> dict[str, float]:
    """1 + the number of valued members ranked strictly above; valued members tied on rank share the average of
    their positions (so averaged ties stay fractional and are interpolated). Members keep the given order."""
    order = sorted(ranks, key=lambda pid: ranks[pid])  # stable: ties keep the given order
    out: dict[str, float] = {}
    above = 0  # valued members with a strictly better rank
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and ranks[order[j]] == ranks[order[i]]:
            j += 1
        group = order[i:j]
        k = sum(1 for pid in group if pid in valued)
        for pid in group:
            out[pid] = above + (k + 1) / 2 if pid in valued else above + 1
        above += k
        i = j
    return {pid: out[pid] for pid in ranks}


def list_values(
    ranks: dict[str, float], p_values: dict[str, float], pool_by_pos: list[float] | None
) -> tuple[dict[str, float], dict]:
    """One list's {pid: rank} → ({pid: value}, info). p_values: projection-group P this week (players with one);
    pool_by_pos: Q, the P values of the list position's full projection pool (rostered included)."""
    V = sorted((p_values[pid] for pid in ranks if pid in p_values), reverse=True)
    Q = sorted(pool_by_pos or [], reverse=True)
    eff = effective_ranks(ranks, {pid for pid in ranks if pid in p_values})
    values = {}
    for pid, r in eff.items():
        v = rank_value(r, V, Q)
        if v is not None:
            values[pid] = v
    info = {"V_len": len(V), "Q_len": len(Q), "used_Q": len(V) < MIN_V and bool(Q), "effective": eff, "V": V}
    return values, info


def censored_values(
    listed: set[str], depth: int, p0_rank: dict[str, int], observed: set[str], M: int, V: list[float], Q: list[float]
) -> dict[str, tuple[float, float]]:
    """Unlisted observed players with P⁰-rank ≤ depth + M → (max(depth+1, P⁰-rank), its value) (§6.4 absence
    rules; depth is the list's effective depth |V|). V and Q descending, as in list_values."""
    out: dict[str, tuple[float, float]] = {}
    for pid in sorted(observed - listed):
        p0r = p0_rank.get(pid)
        if p0r is None or p0r > depth + M:
            continue
        r = max(depth + 1, p0r)
        v = rank_value(r, V, Q)
        if v is not None:
            out[pid] = (float(r), v)
    return out


def p0_ranks(p0: dict[str, float], slot_of: dict[str, str], slot: str) -> dict[str, int]:
    """Rank by P⁰ (desc) among the players in `slot`; ties by pid."""
    members = sorted((pid for pid in p0 if slot_of.get(pid) == slot), key=lambda pid: (-p0[pid], pid))
    return {pid: i for i, pid in enumerate(members, 1)}


def pick_list(pid_lists: dict[str, float], d14_slot: str | None, order: tuple[str, ...] = LIST_ORDER) -> str | None:
    """The list a player in several lists takes his value from: his D14 slot's, else the first he is in by
    `order` (then any other, in the given order); None if he is in none."""
    if d14_slot in pid_lists:
        return d14_slot
    for name in (*order, *pid_lists):
        if name in pid_lists:
            return name
    return None
