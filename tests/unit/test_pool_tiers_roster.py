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


def test_panel_empty_slot_and_locked_starter():
    empty = _panel({"players": []})
    assert empty["starter"] is None and empty["badges"]["w3"]["badge"] == "START"  # μ_S = 0, σ_S = 0
    locked = _panel({"players": ["s", "o"]}, statuses=_statuses(s=PlayerStatus(reasons=["locked"])), lineup={"LB": "s"})
    assert locked["starter_locked"] and not any(b["badge"] == "START" for b in locked["badges"].values())
