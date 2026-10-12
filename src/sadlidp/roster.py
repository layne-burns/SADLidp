"""My IDPs panel (D15, SPEC.md §6.7).

Per position: S = your comparable player with the highest μ, B = your other comparable player. A
player is comparable if he isn't on IR, has a μ, and nothing but `locked` keeps him off the field (a
locked player is compared on his frozen pre-kickoff μ and status). A waiver player W gets START when
P(W outscores S) ≥ threshold, and UPGRADE (only when B exists and W lacks START) when P(W outscores B)
≥ threshold. Outcome probabilities use predictive σ² = σ_μ² + σ_out². IR (`reserve`) players are shown
but never comparable and don't count toward the limit (Phase 1); taxi does.

A suggestion that can't pay off this week is still shown, flagged `reference` with its reasons:
`starter_locked` when the lineup starter at the position is locked (or, with no lineup known, S is): SADL
starts exactly one DL/LB/DB, so nothing added there can score this week; `drop_locked` when the player
it would drop is locked (Sleeper won't drop him).
"""

from __future__ import annotations

import math

from .pool import PlayerStatus
from .positions import IDP_SLOTS, slot_of


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def p_outscores(mu_w: float, sp_w: float, mu_ref: float, sp_ref: float) -> float:
    spread = math.sqrt(sp_w * sp_w + sp_ref * sp_ref)
    if spread == 0:
        return 1.0 if mu_w > mu_ref else 0.0
    return phi((mu_w - mu_ref) / spread)


def starter_slots(roster_positions: list[str], starters: list[str] | None) -> dict[str, str | None]:
    """Who sits in your lineup's DL/LB/DB slot (matchups `starters` is aligned with roster_positions)."""
    if not starters:
        return {}  # lineup unknown (distinct from an empty slot, which maps to None)
    out: dict[str, str | None] = {}
    for idx, slot in enumerate(roster_positions or []):
        if slot in IDP_SLOTS and slot not in out:
            pid = starters[idx] if idx < len(starters) else None
            out[slot] = pid if pid not in (None, "0") else None
    return out


def panel(
    roster: dict,
    players: dict[str, dict],
    cons: dict[str, dict],
    statuses: dict[str, PlayerStatus],
    waiver_ids: dict[str, list[str]],
    limits: dict[str, int],
    sigma_out: dict[str, float],
    threshold: float,
    lineup: dict[str, str | None],
) -> dict[str, dict]:
    """waiver_ids[slot] = available, playable players with a μ (best first)."""
    reserve = set(roster.get("reserve") or [])
    taxi = set(roster.get("taxi") or [])
    mine = [pid for pid in roster.get("players") or [] if slot_of(players.get(pid, {})) is not None]
    out: dict[str, dict] = {}
    for slot in IDP_SLOTS:
        so = float(sigma_out.get(slot) or 0.0)

        def sp(pid: str, so: float = so) -> float:
            return math.sqrt(cons[pid]["sigma"] ** 2 + so * so)

        def locked(pid: str | None) -> bool:
            return bool(pid and pid in statuses and statuses[pid].locked)

        here = [pid for pid in mine if slot_of(players[pid]) == slot]
        comparable = sorted(
            (pid for pid in here if pid not in reserve and pid in cons and statuses[pid].comparable),
            key=lambda pid: (-cons[pid]["mu"], pid),
        )
        starter = comparable[0] if comparable else None
        other = comparable[-1] if len(comparable) > 1 else None
        count = sum(1 for pid in here if pid not in reserve)
        lineup_pid = lineup.get(slot)
        starter_locked = locked(lineup_pid) if slot in lineup else locked(starter)
        mu_s, sp_s = (cons[starter]["mu"], sp(starter)) if starter else (0.0, 0.0)
        # with no comparable B, the drop is yours to choose among all non-IR players here (S included)
        drop_pool = [pid for pid in here if pid not in reserve]

        badges: dict[str, dict] = {}
        for w in waiver_ids.get(slot, []):
            p_start = p_outscores(cons[w]["mu"], sp(w), mu_s, sp_s)
            if p_start >= threshold:
                # under the limit: just add; else drop B; with no B, your other player is unavailable (you choose)
                action = "add" if count < limits.get(slot, 2) else ("add_drop" if other else "choose_drop")
                why = ["starter_locked"] if starter_locked else []
                if (action == "add_drop" and locked(other)) or (
                    action == "choose_drop" and drop_pool and all(locked(pid) for pid in drop_pool)
                ):
                    why.append("drop_locked")
                badges[w] = {"badge": "START", "over": starter, "p": round(p_start, 3), "action": action,
                             "drop": other if action == "add_drop" else None, "reference": bool(why),
                             "why": why}  # fmt: skip
            elif other:
                p_up = p_outscores(cons[w]["mu"], sp(w), cons[other]["mu"], sp(other))
                if p_up >= threshold:
                    why = (["starter_locked"] if starter_locked else []) + (["drop_locked"] if locked(other) else [])
                    badges[w] = {"badge": "UPGRADE", "over": other, "p": round(p_up, 3), "action": "add_drop",
                                 "drop": other, "reference": bool(why), "why": why}  # fmt: skip
        out[slot] = {
            "players": here,
            "reserve": sorted(pid for pid in here if pid in reserve),
            "taxi": sorted(pid for pid in here if pid in taxi),
            "starter": starter,
            "other": other,
            "count": count,
            "limit": limits.get(slot, 2),
            "starter_locked": starter_locked,
            "lineup_starter": lineup_pid,
            "badges": badges,
        }
    return out
