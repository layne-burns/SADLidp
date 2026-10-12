from datetime import UTC, datetime, timedelta
from pathlib import Path

from sadlidp import gate
from sadlidp.schedule import ET, Schedule

FIXTURES = Path(__file__).parent.parent / "fixtures"
SCHED = Schedule.from_csv((FIXTURES / "nflverse_games_2026.csv").read_text(), 2026, {"LA": "LAR"})
SADL = {"waiver_day_of_week": 2, "daily_waivers": 0}  # Sleeper: Monday = 0, so 2 = Wednesday


def test_waiver_instant_is_wednesday_midnight_pacific_before_the_first_game():
    w = gate.waiver_instant(SCHED, 6, SADL)
    assert w == datetime(2026, 10, 14, 0, 0, tzinfo=gate.PT)  # week 6 opens Thu Oct 15
    assert w.astimezone(UTC) == datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    assert gate.waiver_instant(SCHED, 6, {**SADL, "daily_waivers": 1}) is None


def test_windows_follow_the_spec_offsets():
    wins = {w.kind: w for w in gate.windows(SCHED, 2026, 6, SADL, {})}
    assert wins["pre-waiver"].id == "2026-w06-prewaiver"
    assert (wins["pre-waiver"].open, wins["pre-waiver"].close) == (
        datetime(2026, 10, 13, 12, 0, tzinfo=gate.PT),
        datetime(2026, 10, 13, 23, 0, tzinfo=gate.PT),
    )
    assert (wins["saturday"].open, wins["saturday"].close) == (
        datetime(2026, 10, 17, 12, 0, tzinfo=ET),
        datetime(2026, 10, 17, 18, 0, tzinfo=ET),
    )
    # week 6's first Sunday game is the 9:30 ET international game
    assert (wins["sunday"].open, wins["sunday"].close) == (
        datetime(2026, 10, 18, 5, 30, tzinfo=ET),
        datetime(2026, 10, 18, 9, 15, tzinfo=ET),
    )


def test_hourly_sunday_cron_slots_land_in_the_sunday_window():
    sunday = next(w for w in gate.windows(SCHED, 2026, 6, SADL, {}) if w.kind == "sunday")
    slots = {h for h in range(0, 24, 3)} | {10, 11, 13, 14, 16}  # '17 */3 * * *' + '17 10,11,13,14,16 * * 0'
    inside = [h for h in sorted(slots) if sunday.contains(datetime(2026, 10, 18, h, 17, tzinfo=UTC))]
    assert len(inside) >= 3


def test_due_picks_an_open_window_once():
    wins = gate.windows(SCHED, 2026, 6, SADL, {})
    pre = wins[0]
    inside = pre.open + timedelta(hours=3)
    assert gate.due(inside, wins, set()) == pre
    assert gate.due(inside, wins, {pre.id}) is None
    assert gate.due(pre.close, wins, set()) is None  # [open, close)
    assert gate.due(pre.open - timedelta(seconds=1), wins, set()) is None


def test_last_fantasy_week():
    assert gate.last_fantasy_week({"playoff_week_start": 15, "playoff_teams": 6, "playoff_round_type": 0}) == 17  # SADL
    assert gate.last_fantasy_week({"playoff_week_start": 15, "playoff_teams": 4, "playoff_round_type": 0}) == 16
    assert (
        gate.last_fantasy_week({"playoff_week_start": 15, "playoff_teams": 6, "playoff_round_type": 1}) == 18
    )  # capped
    assert gate.last_fantasy_week({}) == 18


def test_sunday_results_window_follows_the_afternoon_games():
    res = next(w for w in gate.windows(SCHED, 2026, 5, SADL, {}) if w.kind == "results")
    assert res.id == "2026-w05-results"
    assert res.open == datetime(2026, 10, 11, 16, 25, tzinfo=ET) + timedelta(hours=4.25)  # last daytime kickoff 4:25
    assert res.close == res.open + timedelta(hours=6)
    # '17 */3' slots: 8:17 PM ET is too early (the 4:25 games are final at 8:25); 11:17 PM and 2:17 AM are in
    assert not res.contains(datetime(2026, 10, 12, 0, 17, tzinfo=UTC))
    assert all(res.contains(datetime(2026, 10, 12, h, 17, tzinfo=UTC)) for h in (3, 6))
