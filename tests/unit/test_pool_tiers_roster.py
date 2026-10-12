from datetime import UTC, datetime, timedelta

import pytest

from sadlidp import roster as rs
from sadlidp.pool import PlayerStatus, player_status, rostered_ids
from sadlidp.schedule import Game, Schedule
from sadlidp.tiers import build_tiers, place

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
SCHED = Schedule(
    2026,
    [
        Game("g1", 6, NOW - timedelta(hours=2), home="BUF", away="SF"),  # already kicked off
        Game("g2", 6, NOW + timedelta(days=3), home="KC", away="DAL"),
    ],
)


def test_player_status_filters():
    ok = {"status": "Active", "team": "KC", "injury_status": None}
    assert player_status(ok, SCHED, 6, NOW).playable
    q = player_status({**ok, "injury_status": "Questionable"}, SCHED, 6, NOW)
    assert q.playable and q.flags == ["Q"] and q.opponent == "DAL"
    assert player_status({**ok, "injury_status": "Out"}, SCHED, 6, NOW).reasons == ["Out"]
    assert player_status({**ok, "status": "Inactive"}, SCHED, 6, NOW).reasons == ["inactive"]
    assert player_status({**ok, "team": None}, SCHED, 6, NOW).reasons == ["no team"]
    assert player_status({**ok, "team": "LAR"}, SCHED, 6, NOW).reasons == ["bye"]
    locked = player_status({**ok, "team": "BUF"}, SCHED, 6, NOW)
    assert locked.locked and not locked.playable
    odd = player_status({**ok, "injury_status": "Weird"}, SCHED, 6, NOW)
    assert odd.playable and odd.flags == ["unknown injury code Weird"]
    assert rostered_ids([{"players": ["a"], "reserve": ["b"], "taxi": None}]) == {"a", "b"}


def test_tiers_anchor_prevents_chaining():
    # each step is 0.9 apart (within z·√2·σ ≈ 1.06) but the 3rd is 1.8 below the anchor: new tier
    pool = {"a": (10.0, 0.75), "b": (9.1, 0.75), "c": (8.2, 0.75), "d": (8.1, 0.75)}
    tiers = build_tiers(pool, z=1.0)
    assert [t["players"] for t in tiers] == [["a", "b"], ["c", "d"]]
    assert build_tiers({"x": (5.0, 0.75), "y": (5.0, 0.75)}, 1.0)[0]["players"] == ["x", "y"]  # tie broken by id


def test_place_rostered_players():
    tiers = build_tiers({"a": (10.0, 0.75), "b": (6.0, 0.75)}, 1.0)
    assert place(14.0, 0.75, tiers, 1.0) == "above"
    assert place(10.5, 0.75, tiers, 1.0) == 1
    assert place(6.2, 0.75, tiers, 1.0) == 2
    assert place(2.0, 0.75, tiers, 1.0) == "below"
    assert place(5.0, 0.75, [], 1.0) == "below"


def test_p_outscores_and_lineup_slots():
    assert rs.p_outscores(10, 3, 10, 4) == pytest.approx(0.5)
    assert rs.p_outscores(12, 3, 10, 4) == pytest.approx(rs.phi(2 / 5))
    assert rs.p_outscores(1, 0, 0, 0) == 1.0
    assert rs.starter_slots(["QB", "DL", "LB", "DB", "BN"], ["q", "dl", "0", "db"]) == {
        "DL": "dl",
        "LB": None,
        "DB": "db",
    }


PLAYERS = {
    "s": {"position": "LB"}, "o": {"position": "LB"}, "ir": {"position": "LB"},
    "w1": {"position": "LB"}, "w2": {"position": "LB"}, "w3": {"position": "LB"},
}  # fmt: skip
CONS = {
    pid: {"mu": mu, "sigma": 1.0}
    for pid, mu in {"s": 10.0, "o": 6.0, "ir": 12.0, "w1": 14.0, "w2": 8.0, "w3": 3.0}.items()
}


def _statuses(**overrides):
    st = {pid: PlayerStatus() for pid in PLAYERS}
    st.update(overrides)
    return st


def _panel(roster, statuses=None, lineup=None, limit=2):
    return rs.panel(
        roster, PLAYERS, CONS, statuses or _statuses(), {"LB": ["w1", "w2", "w3"]}, {"DL": 2, "LB": limit, "DB": 2},
        {"LB": 2.0}, 0.6, lineup or {},
    )["LB"]  # fmt: skip


def test_panel_start_and_upgrade():
    lb = _panel({"players": ["s", "o", "ir"], "reserve": ["ir"]})
    assert (lb["starter"], lb["other"], lb["count"]) == ("s", "o", 2)  # IR player neither eligible nor counted
    assert lb["badges"]["w1"]["badge"] == "START" and lb["badges"]["w1"]["action"] == "add_drop"
    assert lb["badges"]["w1"]["drop"] == "o"
    assert lb["badges"]["w2"]["badge"] == "UPGRADE" and lb["badges"]["w2"]["over"] == "o"
    assert "w3" not in lb["badges"]


def test_panel_under_limit_and_unavailable_other():
    lb = _panel({"players": ["s"]})
    assert lb["badges"]["w1"]["action"] == "add" and lb["other"] is None and "w2" not in lb["badges"]
    out = _panel({"players": ["s", "o"]}, statuses=_statuses(o=PlayerStatus(reasons=["bye"])))
    assert out["other"] is None and out["badges"]["w1"]["action"] == "choose_drop"


def test_panel_empty_slot():
    empty = _panel({"players": []})
    assert empty["starter"] is None and empty["badges"]["w3"]["badge"] == "START"  # μ_S = 0, σ_S = 0
    assert empty["badges"]["w3"]["reference"] is False


LOCKED = PlayerStatus(reasons=["locked"])


def test_locked_starter_still_anchors_and_start_is_reference():
    # D15 amended: S's game started; he keeps his μ (10) as the comparison and START is shown for reference
    lb = _panel({"players": ["s", "o"]}, statuses=_statuses(s=LOCKED), lineup={"LB": "s"})
    assert lb["starter_locked"] and (lb["starter"], lb["other"]) == ("s", "o")
    start = lb["badges"]["w1"]
    assert (
        start["badge"] == "START" and start["over"] == "s" and start["reference"] and start["why"] == ["starter_locked"]
    )
    up = lb["badges"]["w2"]  # one LB lineup slot and it's locked: nothing added can score this week
    assert up["badge"] == "UPGRADE" and up["reference"] and up["why"] == ["starter_locked"]


def test_locked_drop_target_makes_suggestions_reference():
    lb = _panel({"players": ["s", "o"]}, statuses=_statuses(o=LOCKED), lineup={"LB": "s"})
    assert lb["badges"]["w1"]["badge"] == "START" and lb["badges"]["w1"]["why"] == ["drop_locked"]
    assert lb["badges"]["w2"]["badge"] == "UPGRADE" and lb["badges"]["w2"]["why"] == ["drop_locked"]
    both = _panel({"players": ["s", "o"]}, statuses=_statuses(s=LOCKED, o=LOCKED), lineup={"LB": "s"})
    assert both["badges"]["w1"]["why"] == ["starter_locked", "drop_locked"]


def test_unknown_lineup_uses_s_and_unavailable_players_stay_out():
    lb = _panel({"players": ["s", "o"]}, statuses=_statuses(s=LOCKED))  # no matchup lineup known
    assert lb["starter_locked"] and lb["badges"]["w1"]["reference"]
    # locked *and* ruled out at kickoff: not comparable, so the slot falls to o
    out = _panel({"players": ["s", "o"]}, statuses=_statuses(s=PlayerStatus(reasons=["Out", "locked"])))
    assert out["starter"] == "o" and out["other"] is None
    # choose_drop: the other player is out and locked, but S can still be dropped, so it's actionable
    out_locked = _panel({"players": ["s", "o"]}, statuses=_statuses(o=PlayerStatus(reasons=["Out", "locked"])))
    assert out_locked["badges"]["w1"]["action"] == "choose_drop" and out_locked["badges"]["w1"]["why"] == []
    # ... unless every droppable player has kicked off too
    both = _statuses(s=LOCKED, o=PlayerStatus(reasons=["Out", "locked"]))
    all_locked = _panel({"players": ["s", "o"]}, statuses=both, lineup={"LB": "s"})
    assert all_locked["badges"]["w1"]["why"] == ["starter_locked", "drop_locked"]


def test_empty_slot_in_a_known_lineup_is_not_locked():
    # the matchup is known and the LB slot is empty: W can be added and started even though S (benched) played
    lb = _panel({"players": ["s", "o"]}, statuses=_statuses(s=LOCKED), lineup={"LB": None})
    assert not lb["starter_locked"] and lb["badges"]["w1"]["reference"] is False
    assert rs.starter_slots(["DL", "LB", "DB"], None) == {} and rs.starter_slots(["DL", "LB", "DB"], []) == {}
    assert rs.starter_slots(["DL", "LB", "DB"], ["a", "0", "c"])["LB"] is None


def test_everything_unlocked_is_actionable():
    lb = _panel({"players": ["s", "o"]}, lineup={"LB": "s"})
    assert not lb["starter_locked"] and not any(b["reference"] for b in lb["badges"].values())
