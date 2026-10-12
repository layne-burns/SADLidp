from datetime import UTC, datetime
from pathlib import Path

from sadlidp import positions as pos
from sadlidp.schedule import ET, Schedule

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_slot_uses_sleeper_position_field():
    barham = {"position": "LB", "fantasy_positions": ["DL", "LB"]}  # verified D14 case
    assert pos.slot_of(barham) == "LB"
    assert pos.also_eligible(barham) == ["DL"]
    assert pos.slot_of({"position": "DE", "fantasy_positions": ["DL"]}) == "DL"
    assert pos.slot_of({"position": "SS"}) == "DB"
    assert pos.slot_of({"position": "WR", "fantasy_positions": ["WR", "DB"]}) == "DB"  # two-way player
    assert pos.slot_of({"position": "QB", "fantasy_positions": ["QB"]}) is None
    assert pos.player_name({"first_name": "A", "last_name": "B"}) == "A B"


def _schedule():
    return Schedule.from_csv((FIXTURES / "nflverse_games_2026.csv").read_text(), 2026, {"LA": "LAR"})


def test_schedule_weeks_from_real_2026_csv():
    s = _schedule()
    assert len(s.games) == 272 and "LAR" in s.teams and "LA" not in s.teams
    monday = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # before Monday night's game
    assert s.target_week(monday) == 5
    assert s.settled_week(monday) == 4
    tuesday = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)
    assert s.target_week(tuesday) == 6
    assert s.byes(5) == {"CAR", "KC"}


def test_kickoffs_are_eastern_and_lines_parsed():
    s = _schedule()
    g = s.game_for("DAL", 5)
    assert g.kickoff.tzinfo == ET and g.total_line == 49.5 and g.spread_line == 9.5
    assert s.opponent("TB", 5) == "@DAL" and s.opponent("DAL", 5) == "TB"
    assert s.opponent("KC", 5) is None  # bye
    sunday = s.first_kickoff_on(6, 6)
    assert sunday is not None and sunday.astimezone(ET).weekday() == 6


def test_unknown_teams_and_json_roundtrip():
    s = _schedule()
    assert s.unknown_teams({"LAR", "LA", None}) == {"LA"}
    s2 = Schedule.from_json(s.to_json())
    assert s2.game_for("DAL", 5) == s.game_for("DAL", 5)
