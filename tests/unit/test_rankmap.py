"""Rank → value mapping and censoring (SPEC §6.4, §10)."""

import pytest

from sadlidp.rankmap import Curve, pick_list, rank_values


def _p(slot: str, values: list[float]) -> dict[str, float]:
    return {f"{slot}{i}": v for i, v in enumerate(values, start=1)}


def test_rank_one_maps_to_the_best_listed_value_and_fractions_interpolate():
    curve = Curve([float(x) for x in range(20, 8, -1)], [])
    assert curve(1) == 20 and curve(2) == 19 and curve(1.5) == pytest.approx(19.5)


def test_ranks_beyond_the_listed_values_take_the_tail_rule():
    v = [float(x) for x in range(20, 8, -1)]  # 12 values, V_12 = 9
    q = [30.0, *[float(x) for x in range(20, 0, -1)]]  # the position's pool, deeper and with a higher top
    curve = Curve(v, q)
    assert curve(13) == 9.0  # min(V_12, Q_13) = min(9, 9)
    assert curve(14) == 8.0 and curve(500) == 1.0  # Q_14 = 8 < V_12; past |Q|: its last value


def test_a_short_list_maps_rank_to_rank_on_the_positions_pool():
    curve = Curve([12.0, 3.0], [15.0, 12.0, 9.0, 3.0])
    assert curve(1) == 15.0 and curve(3) == 9.0  # |V| < 10: Q replaces V, no rescaling


def test_a_player_on_two_lists_uses_his_own_positions_list_else_his_best_rank():
    assert pick_list({"DL": 30, "LB": 12}, "DL") == "DL"
    assert pick_list({"DL": 30, "LB": 12}, "DB") == "LB"
    assert pick_list({}, "LB") is None


def test_rank_values_build_the_curve_from_the_lists_players_and_attach_to_the_d14_slot():
    p = {**_p("LB", [16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2]), **_p("DL", [11, 7, 5])}
    slots = {pid: pid[:2] for pid in p}
    # FP ranks the LBs in reverse, and a hybrid (Sleeper DL) on its LB list only
    rows = {f"LB{i}": {"ranks": {"LB": 13 - i}} for i in range(1, 13)}
    rows["DL1"] = {"ranks": {"LB": 13}}
    values, detail = rank_values(rows, p, slots.get, {"LB": {"depth": 13}}, lambda pid: True)
    assert values["LB12"] == 16 and values["LB1"] == 3  # rank 1 -> the best P among the listed players
    assert values["DL1"] == 2 and detail["DL1"] == {"list": "LB", "rank": 13}  # value from the LB curve


def test_whole_lists_keep_the_curve_when_most_rows_are_frozen_away():
    """Only one listed player is still to play: V still comes from the whole list as last read."""
    p = _p("LB", [20, 18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4])
    slots = {pid: "LB" for pid in p}
    members = {"LB": {pid: i for i, pid in enumerate(p, start=1)}}
    rows = {"LB12": {"ranks": {"LB": 3}}}
    values, _ = rank_values(rows, p, slots.get, {"LB": {"depth": 12}}, lambda pid: True, members=members)
    assert values["LB12"] == 16  # V_3, not the tail of a one-player curve


def test_censoring_ranks_unlisted_observed_players_past_the_end_of_the_list():
    p = _p("DB", [20, 18, 16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2.5, 2, 1.5, 1])  # 17 DBs
    slots = {pid: "DB" for pid in p}
    listed = [f"DB{i}" for i in range(1, 12)]  # D = 11, everyone but DB12–DB17
    rows = {pid: {"ranks": {"DB": i}} for i, pid in enumerate(listed, start=1)}
    unobserved = {"DB13"}
    values, detail = rank_values(rows, p, slots.get, {"DB": {"depth": 11}}, lambda pid: pid not in unobserved, m=4)
    assert detail["DB12"] == {"list": "DB", "rank": 12, "censored": True}  # P-rank 12 = D+1
    assert "DB13" not in values  # first read after his game started: missing, never censored
    assert detail["DB14"]["rank"] == 14 and detail["DB15"]["rank"] == 15  # P-rank ≤ D+M = 15
    assert "DB16" not in values and "DB17" not in values  # beyond D+M: missing
    assert values["DB12"] == min(p["DB11"], p["DB12"])  # the tail rule


def test_a_list_not_in_the_universe_censors_nobody():
    p = _p("DL", [10, 9, 8])
    rows = {"DL1": {"ranks": {"DL": 1}}}
    values, _ = rank_values(rows, p, lambda pid: "DL", {}, lambda pid: True)
    assert set(values) == {"DL1"}
