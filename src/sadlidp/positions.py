"""Positions: the three IDP slots (D14, verified in Phase 1: Sleeper counts its designated `position` field),
the kicker slot (added 2026-11-17) and the team-defense slot (added 2026-11-25, D25).

Two tuples, on purpose. `IDP_SLOTS` is the fitted IDP model and every IDP statistic (form, swing fit, accuracy
weights, scoreboard, backtests): it never holds K or DEF. `SLOTS` is what the pipeline tracks; each league's
page shows the slots its roster starts (SADL: DL, LB, DB, K; Crooked: K, DEF; D26). A slot belongs to one
group (`GROUPS`); sources, weights and accuracy are kept apart per group, so nothing measured on kickers or
team defenses can move an IDP number, or the other way round.
"""

from __future__ import annotations

POSITION_TO_SLOT = {
    "DL": "DL", "DE": "DL", "DT": "DL", "NT": "DL",
    "LB": "LB", "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "DB": "DB", "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB",
}  # fmt: skip
IDP_SLOTS = ("DL", "LB", "DB")
K = "K"
DEF = "DEF"  # a team defense: Sleeper `position` "DEF", player id = team code ("KC"), no `status` (D25)
SLOTS = IDP_SLOTS + (K, DEF)  # every tracked slot, in page order
GROUPS = {"idp": IDP_SLOTS, "k": (K,), "def": (DEF,)}
ROOM_SLOTS = (K, DEF)  # no position limit: roster room decides an add (§6.7)
# Evaluation pool sizes per group (SPEC §6.8, §6.5): the best `pool` by prediction among those who played,
# `top` for the top-k hit rate, `min_cover` pool players a source must cover in a position-week to count.
# About 28 kickers play a week, so the IDP sizes would leave every kicker week "partial".
# Team defenses: 32, about 28 play a week, the same sizes as kickers.
EVAL = {
    "idp": {"pool": 50, "top": 12, "min_cover": 15},
    "k": {"pool": 20, "top": 5, "min_cover": 10},
    "def": {"pool": 20, "top": 5, "min_cover": 10},
}
_GROUP_OF = {K: "k", DEF: "def"}


def group_of(slot: str | None) -> str:
    """The group a slot belongs to: "k" for the kicker slot, "def" for team defenses, "idp" for everything
    else."""
    return _GROUP_OF.get(slot or "", "idp")


def eligibility(player: dict) -> list[str]:
    """IDP slots in the order Sleeper lists them in `fantasy_positions` (that order is canonical, not primary)."""
    out: list[str] = []
    for fp in player.get("fantasy_positions") or []:
        slot = POSITION_TO_SLOT.get(str(fp).upper())
        if slot and slot not in out:
            out.append(slot)
    return out


def slot_of(player: dict) -> str | None:
    """The one slot a tracked player counts at (D14): Sleeper's `position`, else his first IDP eligibility.
    A kicker is a player whose `position` is "K" and who has no IDP eligibility. `fantasy_positions` never
    makes a kicker (punters are listed ["K", "P"]). Everyone who had an IDP slot before kickers were tracked
    keeps it: Sleeper id 5251 is listed with position "K" and fantasy_positions ["DL"], but he is a defensive
    lineman Sleeper projects IDP stats for every week, so he stays DL, as he always was. A team defense is a
    record whose `position` is "DEF" (D25); no player record lists "DEF" in `fantasy_positions`."""
    position = str(player.get("position") or "").upper()
    slot = POSITION_TO_SLOT.get(position)
    if slot:
        return slot
    elig = eligibility(player)
    if elig:
        return elig[0]
    return position if position in (K, DEF) else None


def idp_slot_of(player: dict) -> str | None:
    """`slot_of` for IDP-only code (archives, backtests): a kicker has no IDP slot."""
    slot = slot_of(player)
    return slot if slot in IDP_SLOTS else None


def is_idp(player: dict) -> bool:
    return slot_of(player) in IDP_SLOTS


def is_kicker(player: dict) -> bool:
    return slot_of(player) == K


def is_defense(player: dict) -> bool:
    return slot_of(player) == DEF


def in_group(players: dict[str, dict], group: str) -> dict[str, dict]:
    """The players of one group. "idp" is everyone who is neither a kicker nor a team defense, so a players
    dict handed to an IDP source is exactly what it was before kickers and defenses were tracked; "k" is the
    kickers alone and "def" the team defenses alone."""
    if group in ("k", "def"):
        return {pid: p for pid, p in players.items() if slot_of(p or {}) in GROUPS[group]}
    return {pid: p for pid, p in players.items() if slot_of(p or {}) not in ROOM_SLOTS}


def also_eligible(player: dict) -> list[str]:
    """Other IDP slots a dual-eligible player could fill (for the 'LB · also DL' tag); none for a kicker or a
    team defense."""
    own = slot_of(player)
    return [] if own in ROOM_SLOTS else [s for s in eligibility(player) if s != own]


def player_name(player: dict | None) -> str:
    if not player:
        return "?"
    name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    return name or "?"
