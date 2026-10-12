import json
from pathlib import Path

import pytest

from sadlidp.config import load_config
from sadlidp.tune import (
    Week,
    chance_out_of_order,
    format_report,
    load_weeks,
    report,
    shown,
    tier_table,
    within_r,
)

ROOT = Path(__file__).parents[2]


def _cfg(**tiers):
    cfg = load_config(ROOT / "config" / "sadl.toml")
    cfg["tiers"].update(tiers)
    cfg["display"]["depth"] = 5
    return cfg


def _week(n, scores, rostered=frozenset()):
    """One LB week: player i is projected 10 − i by Sleeper, scores scores[i]."""
    ids = [f"p{i}" for i in range(len(scores))]
    return Week(
        week=n,
        values={"sleeper_proj": {pid: 10.0 - i for i, pid in enumerate(ids)}},
        kinds={"sleeper_proj": "projection"},
        slots=dict.fromkeys(ids, "LB"),
        actual={pid: s for pid, s in zip(ids, scores, strict=True) if s is not None},
        rostered=set(rostered),
    )


def test_shown_lists_the_top_available_players_with_their_tiers():
    w = _week(1, [20, 18, None, 9, 8, 7, 3], rostered={"p1"})
    rows = shown(w, _cfg(z=0.25, s0_default=1.0))
    assert [r["pid"] for r in rows] == ["p0", "p2", "p3", "p4", "p5"]  # p1 is rostered; depth 5
    assert [r["tier"] for r in rows] == [1, 2, 3, 4, 5]  # 1-point gaps, z·√2·σ ≈ 0.35: every player a tier
    assert rows[1]["act"] is None  # didn't play: listed, not scored
    assert [r["tier"] for r in shown(w, _cfg(z=5.0))] == [1] * 5


def test_order_and_tier_signal_and_whats_left_inside_tiers():
    weeks = [_week(1, [12, 10, 8, 6, 4]), _week(2, [22, 20, 18, 16, 14])]  # perfect order, different levels
    rows = [r for w in weeks for r in shown(w, _cfg(z=0.25))]
    assert within_r(rows, lambda r: r["mu"])["LB"] == (pytest.approx(1.0), 10)  # week levels removed first
    assert within_r(rows, lambda r: -r["tier"])["LB"][0] == pytest.approx(1.0)
    one_tier = [r for w in weeks for r in shown(w, _cfg(z=5.0))]
    assert within_r(one_tier, lambda r: -r["tier"])["LB"][0] is None  # one tier: no tier signal
    inside = within_r(one_tier, lambda r: r["mu"], group=lambda r: (r["week"], r["tier"]))
    assert inside["LB"][0] == pytest.approx(1.0)  # all the order is left inside the one tier: too wide
    assert within_r(rows, lambda r: r["mu"])["DL"] == (None, 0)


def test_tier_table_margins_and_chance_out_of_order():
    rows = [{"week": 1, "slot": "LB", "tier": t, "mu": 10.0 - t, "act": a}
            for t, a in [(1, 10), (1, 14), (2, 9), (2, None)]]  # fmt: skip
    table = tier_table(rows)["LB"]
    assert [(t["tier"], t["n"], t["avg"]) for t in table] == [(1, 2, 12.0), (2, 1, 9.0)]
    assert table[0]["margin"] == pytest.approx(2 * 2.8284271 / 2**0.5) and table[1]["margin"] is None
    many = [{"week": w, "slot": "DL", "tier": 1 + i % 2, "mu": 9.0 - i % 2, "act": 8.0 + (i % 5)}
            for w in (1, 2) for i in range(20)]  # fmt: skip
    p = chance_out_of_order(many, sims=500)
    assert 0 < p["DL"] < 0.5 and p["LB"] is None  # a 1-point gap on 20 player-games a tier: often flipped


def test_report_sweeps_and_formats():
    weeks = [_week(n, [12 + n, 10, 11, 6, 7, 2]) for n in (1, 2, 3)]
    rep = report(weeks, _cfg())
    assert rep["weeks"] == [1, 2, 3] and rep["z"] == 0.75
    assert [row["z"] for row in rep["z_sweep"]][:2] == [0.25, 0.5]
    assert [row["split"] for row in rep["split_sweep"]] == [0.4, 0.5, 0.6, 0.7, 0.8]
    text = format_report(rep)
    assert "Tier averages" in text and "z sweep" in text and "LB" in text


def test_load_weeks_reads_saved_sources_and_final_stats(tmp_path):
    season = tmp_path / "2026"
    wd = season / "week01"
    (wd / "sources").mkdir(parents=True)
    (season / "league.json").write_text(json.dumps({"scoring_settings": {"idp_tkl_solo": 2.0}}))
    players = {"a": {"position": "LB", "team": "NYJ"}, "b": {"position": "DE", "team": "NYJ"}}
    (wd / "players.json").write_text(json.dumps({"players": players}))
    rows = {"a": {"stats": {"idp_tkl_solo": 4}}, "b": {"stats": {"idp_tkl_solo": 1}}}
    (wd / "sources" / "sleeper_proj.json").write_text(json.dumps({"kind": "projection", "rows": rows}))
    stats = {"a": {"gp": 1, "idp_tkl_solo": 5}, "b": {}}  # b didn't play
    (wd / "actuals.json").write_text(json.dumps({"stats": stats}))
    weeks = load_weeks(tmp_path, 2026, {1: {"x"}, 2: set()})
    assert len(weeks) == 1
    w = weeks[0]
    assert w.values == {"sleeper_proj": {"a": 8.0, "b": 2.0}} and w.slots == {"a": "LB", "b": "DL"}
    assert w.actual == {"a": 10.0} and w.rostered == {"x"}
    assert load_weeks(tmp_path, 2026, {}) == []  # no rosters for the week: skipped
