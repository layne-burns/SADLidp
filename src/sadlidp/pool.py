"""Who can be streamed this week (SPEC.md §2 filters).

Hidden from the waiver pool: injury codes Out/Doubtful/IR/PUP/NFI/Sus/NA/DNR/COV, `status` other than
Active, `active` false, no team, on bye, or locked (his game has kicked off). Questionable is shown with
a flag; any other code is shown flagged as unknown. Your own players are always shown, with badges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .schedule import Schedule

HIDE_INJURY = {"Out", "Doubtful", "IR", "PUP", "NFI", "Sus", "NA", "DNR", "COV"}


@dataclass
class PlayerStatus:
    reasons: list[str] = field(default_factory=list)  # why he can't be played this week
    flags: list[str] = field(default_factory=list)  # shown, but worth a look (Q, unknown codes)
    kickoff: datetime | None = None
    opponent: str | None = None

    @property
    def playable(self) -> bool:
        return not self.reasons

    @property
    def locked(self) -> bool:
        return "locked" in self.reasons


def player_status(player: dict, schedule: Schedule, week: int, now: datetime) -> PlayerStatus:
    st = PlayerStatus()
    injury = player.get("injury_status")
    if injury in HIDE_INJURY:
        st.reasons.append(injury)
    elif injury == "Questionable":
        st.flags.append("Q")
    elif injury:
        st.flags.append(f"unknown injury code {injury}")
    if player.get("status") != "Active":
        st.reasons.append(str(player.get("status") or "no status").lower())
    if player.get("active") is False and "inactive" not in st.reasons:
        st.reasons.append("inactive")
    team = player.get("team")
    if not team:
        st.reasons.append("no team")
        return st
    game = schedule.game_for(team, week)
    if game is None:
        st.reasons.append("bye")
        return st
    st.kickoff = game.kickoff
    st.opponent = schedule.opponent(team, week)
    if game.kickoff and game.kickoff <= now:
        st.reasons.append("locked")
    return st


def rostered_ids(rosters: list[dict]) -> set[str]:
    return {pid for r in rosters for key in ("players", "reserve", "taxi") for pid in (r.get(key) or [])}
