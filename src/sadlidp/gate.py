"""The schedule gate (SPEC.md §4.3, D13).

Cron fires every 3 hours (hourly on Sunday mornings); the gate decides whether a window is due.
Windows are [open, close) intervals checked against the actual run time, so a delayed run still
catches up. A window is marked done only after a successful sync. The manual button always forces.

- Pre-waiver: SADL processes weekly waivers on `waiver_day_of_week` (Monday = 0; verified 2 =
  Wednesday) at 00:00 Pacific. Open 12 h before, close 1 h before.
- Saturday: 12:00–18:00 ET.
- Sunday: from 4 h before the first Sunday kickoff to 15 min before it.
- Results (D22): Sunday evening, from 3¼ h after the last Sunday daytime kickoff, for 6 h, so the page
  shows the afternoon's scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .schedule import ET, Schedule

PT = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Window:
    id: str
    kind: str
    open: datetime
    close: datetime

    def contains(self, now: datetime) -> bool:
        return self.open <= now < self.close


def waiver_instant(schedule: Schedule, week: int, settings: dict) -> datetime | None:
    """The weekly waiver run that decides week `week` claims: the last waiver day on or before its first game."""
    if settings.get("daily_waivers"):
        return None  # daily mode is not used by SADL; manual + weekend windows still apply
    kicks = [g.kickoff for g in schedule.week_games(week) if g.kickoff]
    if not kicks:
        return None
    day = min(kicks).astimezone(PT).date()
    target = int(settings.get("waiver_day_of_week", 2))
    while day.weekday() != target:
        day -= timedelta(days=1)
    instant = datetime.combine(day, time(0, 0), tzinfo=PT)
    return instant if instant < min(kicks) else None


def last_fantasy_week(settings: dict) -> int:
    """The league's last scored week: playoff_week_start + playoff weeks − 1, capped at 18.

    Rounds = ⌈log2(playoff_teams)⌉. `playoff_round_type` 0 = one week per round; any other value is
    read as two weeks per round, which can only end the season later (never stops a needed sync).
    """
    start = int(settings.get("playoff_week_start") or 0)
    if start <= 0:
        return 18
    teams = int(settings.get("playoff_teams") or 0)
    rounds = math.ceil(math.log2(teams)) if teams > 1 else 1
    weeks = rounds if int(settings.get("playoff_round_type") or 0) == 0 else 2 * rounds
    return min(18, start + weeks - 1)


def _hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def windows(schedule: Schedule, season: int, week: int, settings: dict, gcfg: dict) -> list[Window]:
    out: list[Window] = []
    prefix = f"{season}-w{week:02d}"
    w = waiver_instant(schedule, week, settings)
    if w:
        out.append(
            Window(
                f"{prefix}-prewaiver",
                "pre-waiver",
                w - timedelta(hours=float(gcfg.get("pre_waiver_open_h", 12))),
                w - timedelta(hours=float(gcfg.get("pre_waiver_close_h", 1))),
            )
        )
    sunday = schedule.first_kickoff_on(week, 6)
    if sunday:
        sat = sunday.astimezone(ET).date() - timedelta(days=1)
        out.append(
            Window(
                f"{prefix}-saturday",
                "saturday",
                datetime.combine(sat, _hhmm(gcfg.get("saturday_open_et", "12:00")), tzinfo=ET),
                datetime.combine(sat, _hhmm(gcfg.get("saturday_close_et", "18:00")), tzinfo=ET),
            )
        )
        out.append(
            Window(
                f"{prefix}-sunday",
                "sunday",
                sunday - timedelta(hours=float(gcfg.get("sunday_open_h", 4))),
                sunday - timedelta(minutes=float(gcfg.get("sunday_close_min", 15))),
            )
        )
    day = [
        g.kickoff
        for g in schedule.week_games(week)
        if g.kickoff and g.kickoff.astimezone(ET).weekday() == 6 and g.kickoff.astimezone(ET).hour < 17
    ]
    if day:
        start = max(day) + timedelta(hours=float(gcfg.get("results_after_h", 3.25)))
        out.append(
            Window(
                f"{prefix}-results", "results", start, start + timedelta(hours=float(gcfg.get("results_window_h", 6)))
            )
        )
    return out


def due(now: datetime, wins: list[Window], done: set[str]) -> Window | None:
    for w in wins:
        if w.contains(now) and w.id not in done:
            return w
    return None
