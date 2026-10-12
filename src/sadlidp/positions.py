"""IDP positions (D14, verified in Phase 1: Sleeper counts its designated `position` field)."""

from __future__ import annotations

POSITION_TO_SLOT = {
    "DL": "DL", "DE": "DL", "DT": "DL", "NT": "DL",
    "LB": "LB", "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "DB": "DB", "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB",
}  # fmt: skip
IDP_SLOTS = ("DL", "LB", "DB")


def eligibility(player: dict) -> list[str]:
    """IDP slots in the order Sleeper lists them in `fantasy_positions` (that order is canonical, not primary)."""
    out: list[str] = []
    for fp in player.get("fantasy_positions") or []:
        slot = POSITION_TO_SLOT.get(str(fp).upper())
        if slot and slot not in out:
            out.append(slot)
    return out


def slot_of(player: dict) -> str | None:
    """The one slot a player counts at (D14): Sleeper's `position`, else his first IDP eligibility."""
    slot = POSITION_TO_SLOT.get(str(player.get("position") or "").upper())
    if slot:
        return slot
    elig = eligibility(player)
    return elig[0] if elig else None


def is_idp(player: dict) -> bool:
    return slot_of(player) is not None


def also_eligible(player: dict) -> list[str]:
    """Other IDP slots a dual-eligible player could fill (for the 'LB · also DL' tag)."""
    own = slot_of(player)
    return [s for s in eligibility(player) if s != own]


def player_name(player: dict | None) -> str:
    if not player:
        return "?"
    name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    return name or "?"
