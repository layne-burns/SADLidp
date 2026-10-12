"""NFL schedule from nflverse (SPEC.md §3.2, §4.3).

Kickoff = gameday + gametime as America/New_York local time. Team codes are mapped to Sleeper's
(nflverse uses LA for the Rams). The schedule also carries Vegas lines (spread, total), kept for D18.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


@dataclass(frozen=True)
class Game:
    game_id: str
    week: int
    kickoff: datetime | None
    home: str
    away: str
    spread_line: float | None = None
    total_line: float | None = None


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_kickoff(gameday: str, gametime: str) -> datetime | None:
    if not gameday:
        return None
    try:
        hh, mm = (gametime or "13:00").split(":")[:2]
        return datetime.fromisoformat(gameday).replace(hour=int(hh), minute=int(mm), tzinfo=ET)
    except ValueError:
        return None


class Schedule:
    def __init__(self, season: int, games: list[Game]):
        self.season = season
        self.games = games
        self._by_week: dict[int, list[Game]] = {}
        for g in games:
            self._by_week.setdefault(g.week, []).append(g)

    @classmethod
    def from_csv(cls, text: str, season: int, aliases: dict[str, str] | None = None) -> Schedule:
        aliases = aliases or {}
        games = []
        for row in csv.DictReader(io.StringIO(text)):
            if str(row.get("season")) != str(season) or row.get("game_type") != "REG":
                continue
            games.append(
                Game(
                    game_id=row.get("game_id", ""),
                    week=int(row["week"]),
                    kickoff=parse_kickoff(row.get("gameday", ""), row.get("gametime", "")),
                    home=aliases.get(row["home_team"], row["home_team"]),
                    away=aliases.get(row["away_team"], row["away_team"]),
                    spread_line=_float(row.get("spread_line")),
                    total_line=_float(row.get("total_line")),
                )
            )
        return cls(season, games)

    @classmethod
    def from_json(cls, data: dict) -> Schedule:
        games = [
            Game(**{**g, "kickoff": datetime.fromisoformat(g["kickoff"]) if g.get("kickoff") else None})
            for g in data.get("games", [])
        ]
        return cls(int(data["season"]), games)

    def to_json(self) -> dict:
        return {
            "season": self.season,
            "games": [{**asdict(g), "kickoff": g.kickoff.isoformat() if g.kickoff else None} for g in self.games],
        }

    @property
    def teams(self) -> set[str]:
        return {t for g in self.games for t in (g.home, g.away)}

    def week_games(self, week: int) -> list[Game]:
        return self._by_week.get(week, [])

    def target_week(self, now: datetime) -> int | None:
        """The week of the earliest game not yet kicked off (SPEC §4.3)."""
        upcoming = [(g.kickoff, g.week) for g in self.games if g.kickoff and g.kickoff > now]
        return min(upcoming)[1] if upcoming else None

    def settled_week(self, now: datetime, days: int = 4) -> int | None:
        """The latest week whose last game kicked off at least `days` days (+4 h) ago."""
        last: dict[int, datetime] = {}
        for g in self.games:
            if g.kickoff:
                last[g.week] = max(last.get(g.week, g.kickoff), g.kickoff)
        done = [w for w, k in last.items() if k + timedelta(hours=4, days=days) <= now]
        return max(done) if done else None

    def game_for(self, team: str | None, week: int) -> Game | None:
        if not team:
            return None
        for g in self.week_games(week):
            if team in (g.home, g.away):
                return g
        return None

    def opponent(self, team: str | None, week: int) -> str | None:
        """'@KC' for an away game, 'KC' at home; None on bye."""
        g = self.game_for(team, week)
        if not g:
            return None
        return f"@{g.home}" if g.away == team else g.away

    def byes(self, week: int) -> set[str]:
        playing = {t for g in self.week_games(week) for t in (g.home, g.away)}
        return self.teams - playing

    def first_kickoff_on(self, week: int, weekday: int) -> datetime | None:
        """Earliest kickoff of `week` on a weekday (Mon=0 … Sun=6), in ET."""
        ks = [g.kickoff for g in self.week_games(week) if g.kickoff and g.kickoff.astimezone(ET).weekday() == weekday]
        return min(ks) if ks else None

    def unknown_teams(self, teams: set[str]) -> set[str]:
        """Teams with no game all season: a code mismatch, never a bye (SPEC §3.2)."""
        return {t for t in teams if t} - self.teams
