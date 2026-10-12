"""Rank → value (SPEC.md §6.4): the V curve, effective rank, the Q tail and small-V rules, censoring, P⁰-rank and
the multi-list rule, on hand-computed examples."""

from __future__ import annotations

import pytest

from sadlidp.rankmap import censored_values, effective_ranks, list_values, p0_ranks, pick_list, rank_value

# twelve members ranked 1..12 whose P values don't follow the list order
P12 = {f"p{i}": p for i, p in enumerate([10, 24, 2, 22, 20, 18, 16, 14, 12, 8, 6, 4], 1)}
R12 = {f"p{i}": float(i) for i in range(1, 13)}
V12 = [24.0, 22.0, 20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 4.0, 2.0]


def test_rank_one_maps_to_largest_value():
    vals, info = list_values(R12, P12, None)
    assert vals["p1"] == 24  # P 10, but rank 1 → V_1
    assert vals["p12"] == 2  # rank 12 → V_12
    assert [vals[f"p{i}"] for i in range(1, 13)] == V12
    assert info["V_len"] == 12 and info["Q_len"] == 0 and info["used_Q"] is False
    assert info["V"] == V12


def test_fractional_ranks_interpolate():
    assert rank_value(2.5, V12, []) == pytest.approx(21.0)
    assert rank_value(2.25, V12, []) == pytest.approx(21.5)
    # an averaged tie in the list: two valued members at 2.5 sit at positions 2 and 3
    ranks = dict(R12, p2=2.5, p3=2.5)
    vals, info = list_values(ranks, P12, None)
    assert info["effective"]["p2"] == info["effective"]["p3"] == 2.5
    assert vals["p2"] == vals["p3"] == pytest.approx((22 + 20) / 2)
    assert vals["p1"] == 24 and vals["p4"] == 18


def test_effective_rank_skips_members_without_p():
    ranks = {"x": 1.0, "y": 2.0, "z": 3.0, "w": 4.0}
    assert effective_ranks(ranks, {"y", "w"}) == {"x": 1, "y": 1, "z": 2, "w": 2}
    # the members at ranks 1, 5 and 9 have no P (Out, or no projection)
    p = {k: v for k, v in P12.items() if k not in ("p1", "p5", "p9")}
    vals, info = list_values(R12, p, None)
    v9 = sorted(p.values(), reverse=True)  # 24, 22, 18, 16, 14, 8, 6, 4, 2
    assert info["V_len"] == 9 and info["used_Q"] is False  # no Q: V is used even though |V| < 10
    eff = info["effective"]
    assert eff["p2"] == 1 and eff["p3"] == 2 and eff["p4"] == 3 and eff["p6"] == 4 and eff["p10"] == 7
    assert vals["p2"] == v9[0] and vals["p10"] == v9[6]


def test_listed_member_without_p_takes_one_plus_valued_above():
    p = {k: v for k, v in P12.items() if k != "p5"}
    vals, info = list_values(R12, p, None)
    v11 = sorted(p.values(), reverse=True)  # 24, 22, 18, 16, 14, 12, 10, 8, 6, 4, 2
    assert info["effective"]["p5"] == 5  # p1..p4 valued above him
    assert vals["p5"] == v11[4] == 14
    assert info["effective"]["p6"] == 5 and vals["p6"] == 14


def test_tail_beyond_v_uses_q():
    V = [float(v) for v in range(19, 9, -1)]  # 19..10, |V| = 10
    Q_high = [float(v) for v in range(40, 20, -1)]  # 40..21: Q_11 = 30 > V_10
    Q_low = [20.0, 19, 18, 17, 16, 15, 14, 13, 12, 11, 9.5, 9.0]  # Q_11 = 9.5, Q_12 = 9
    assert rank_value(11, V, Q_high) == 10  # min(V_10, Q_11)
    assert rank_value(11, V, Q_low) == 9.5
    assert rank_value(10.5, V, Q_low) == 9.5  # ⌈10.5⌉ = 11
    assert rank_value(30, V, Q_low) == 9.0  # Q_min(30, 12)
    assert rank_value(30, V, []) == 10  # no Q: V_|V|
    # a listed member without P below every valued one
    ranks = {f"m{i}": float(i) for i in range(1, 12)}
    p = {f"m{i}": V[i - 1] for i in range(1, 11)}
    vals, info = list_values(ranks, p, Q_low)
    assert info["effective"]["m11"] == 11 and vals["m11"] == 9.5
    assert info["used_Q"] is False and info["Q_len"] == 12


def test_small_v_uses_q_rank_to_rank():
    ranks = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
    p = {"a": 5.0, "b": 7.0, "c": 3.0}
    Q = [3.0, 9.0, 8.0, 7.0, 6.0, 5.0]
    vals, info = list_values(ranks, p, Q)
    assert info["used_Q"] is True and info["V_len"] == 3 and info["Q_len"] == 6
    assert vals == {"a": 9.0, "b": 8.0, "c": 7.0, "d": 6.0}  # d: no P, 3 valued above → Q_4
    Q = sorted(Q, reverse=True)
    assert rank_value(10, [], Q) == 3.0  # beyond Q: Q_|Q|
    assert rank_value(1.5, [], Q) == pytest.approx(8.5)


def test_censoring_observed_within_depth_plus_m():
    V = [float(v) for v in range(30, 20, -1)]  # 30..21, D = 10
    Q = [float(v) for v in range(40, 10, -1)]  # 40..11: Q_r = 41 - r
    p0_rank = {"listed": 3, "u5": 5, "u15": 15, "u20": 20, "u21": 21, "unobs": 12}
    observed = {"listed", "u5", "u15", "u20", "u21", "nop0"}
    out = censored_values({"listed"}, 10, p0_rank, observed, 10, V, Q)
    assert out == {
        "u5": (11.0, 21.0),  # rank max(11, 5) = 11 → min(V_10 = 21, Q_11 = 30)
        "u15": (15.0, 21.0),
        "u20": (20.0, 21.0),  # D + M = 20 is still in
    }
    Q_low = [float(v) for v in range(30, 0, -1)]  # Q_r = 31 - r
    out = censored_values(set(), 10, {"a": 4, "b": 18}, {"a", "b"}, 10, V, Q_low)
    assert out == {"a": (11.0, 20.0), "b": (18.0, 13.0)}
    # V empty: Q_rank
    assert censored_values(set(), 0, {"a": 4}, {"a"}, 10, [], Q_low) == {"a": (4.0, 27.0)}
    assert censored_values(set(), 0, {"a": 4}, {"a"}, 10, [], []) == {}


def test_p0_ranks_within_slot_ties_by_pid():
    p0 = {"b": 9.0, "a": 9.0, "c": 12.0, "d": 3.0, "e": 20.0}
    slot_of = {"a": "LB", "b": "LB", "c": "LB", "d": "LB", "e": "DL"}
    assert p0_ranks(p0, slot_of, "LB") == {"c": 1, "a": 2, "b": 3, "d": 4}
    assert p0_ranks(p0, slot_of, "DB") == {}


def test_pick_list():
    assert pick_list({"DL": 12.0, "LB": 30.0}, "LB") == "LB"
    assert pick_list({"LB": 30.0, "DL": 12.0}, "DB") == "DL"
    assert pick_list({"DB": 4.0, "LB": 30.0}, "DL") == "LB"
    assert pick_list({"S": 4.0}, "DB") == "S"
    assert pick_list({}, "DL") is None


def test_empty_inputs():
    vals, info = list_values({}, {}, None)
    assert vals == {} and info["V_len"] == 0 and info["effective"] == {}
    assert list_values({"a": 1.0}, {}, None)[0] == {}  # nothing valued and no Q
    assert list_values({"a": 1.0}, {}, [5.0])[0] == {"a": 5.0}
    assert censored_values(set(), 10, {}, set(), 10, [1.0], []) == {}
    assert p0_ranks({}, {}, "DL") == {}
