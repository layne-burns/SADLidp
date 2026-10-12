"""Rank → SADL points within a list, and censoring of unlisted players (SPEC.md §6.4).

For a list L: V = the projection-group values P of L's players that have one, sorted descending and indexed
from 1; Q = P of the whole projection pool at L's position (rostered players included). Rank r maps to V_r
(fractional ranks interpolated); past |V| it maps to min(V_|V|, Q_min(⌈r⌉,|Q|)); with |V| < 10, Q replaces V.
A player on two lists takes his own (D14) position's list, else the list where he ranks best. An unlisted
player the source observed whose P-rank at the list's position is ≤ D+M gets rank max(D+1, his P-rank).
"""

from __future__ import annotations

import math
from collections.abc import Callable

MIN_V = 10


def _at(values: list[float], r: float) -> float:
    """values[r] with 1-based, linearly interpolated r, clamped to the ends."""
    r = min(max(r, 1.0), float(len(values)))
    lo = math.floor(r)
    hi = min(lo + 1, len(values))
    frac = r - lo
    return values[lo - 1] + (values[hi - 1] - values[lo - 1]) * frac


class Curve:
    """One list's rank → points curve."""

    def __init__(self, v: list[float], q: list[float]):
        self.v = sorted(v, reverse=True)
        self.q = sorted(q, reverse=True)

    def __call__(self, r: float) -> float | None:
        if len(self.v) < MIN_V:
            return _at(self.q, r) if self.q else None  # rank to rank on the position's pool, no rescaling
        if r <= len(self.v):
            return _at(self.v, r)
        tail = self.v[-1]
        return min(tail, self.q[min(math.ceil(r), len(self.q)) - 1]) if self.q else tail


def pick_list(ranks: dict[str, float], own: str | None) -> str | None:
    if not ranks:
        return None
    if own in ranks:
        return own
    return min(ranks, key=lambda s: (ranks[s], s))


def rank_values(
    rows: dict[str, dict],
    p: dict[str, float],
    slot_of: Callable[[str], str | None],
    universe: dict[str, dict],
    observed: Callable[[str], bool],
    m: int = 10,
    members: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, float], dict[str, dict]]:
    """(points, detail) for one rank source. `rows[pid]["ranks"]` = {list: rank}; `p` = projection-group values;
    `universe[list]["depth"]` = D for each list the source publishes; `observed(pid)` per §5.3. `members` =
    each whole list as last read ({list: {pid: rank}}), players already played included: V is built from it,
    since `rows` holds only the players seen before their kickoff; without it, V comes from `rows`."""
    members = members or {}
    lists = sorted({s for r in rows.values() for s in (r.get("ranks") or {})} | set(universe) | set(members))
    pool: dict[str, list[str]] = {s: [] for s in lists}
    for pid in p:
        s = slot_of(pid)
        if s in pool:
            pool[s].append(pid)
    curves = {}
    for s in lists:
        on_list = members.get(s) or {pid: r["ranks"][s] for pid, r in rows.items() if s in (r.get("ranks") or {})}
        listed = [pid for pid in on_list if pid in p]
        curves[s] = Curve([p[pid] for pid in listed], [p[pid] for pid in pool[s]])

    values: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for pid, r in rows.items():
        ranks = r.get("ranks") or {}
        s = pick_list(ranks, slot_of(pid))
        if s is None:
            continue
        v = curves[s](float(ranks[s]))
        if v is not None:
            values[pid] = v
            detail[pid] = {"list": s, "rank": ranks[s]}

    for s in lists:  # censoring (§6.4): unlisted players near the top of the position by P
        depth = (universe.get(s) or {}).get("depth")
        if s not in universe or not depth:
            continue
        order = sorted(pool[s], key=lambda pid: (-p[pid], pid))
        for i, pid in enumerate(order[: depth + m], start=1):
            if pid in rows or pid in values or any(pid in mm for mm in members.values()) or not observed(pid):
                continue
            rank = max(depth + 1, i)
            v = curves[s](float(rank))
            if v is not None:
                values[pid] = v
                detail[pid] = {"list": s, "rank": rank, "censored": True}
    return values, detail
