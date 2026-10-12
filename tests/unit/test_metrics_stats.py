import json
from pathlib import Path

import pytest

from sadlidp import metrics
from sadlidp.config import load_config
from sadlidp.stats import evaluate, residual_sigma_out

ROOT = Path(__file__).parents[2]


def test_basic_errors_and_bias_sign():
    pred, act = [10.0, 6.0, 4.0], [8.0, 9.0, 4.0]
    assert metrics.mae(pred, act) == pytest.approx(5 / 3)
    assert metrics.rmse(pred, act) == pytest.approx((13 / 3) ** 0.5)
    assert metrics.bias(pred, act) == pytest.approx(-1 / 3)  # predicted − actual: runs low
    assert metrics.mae([], []) is None and metrics.bias([], []) is None


def test_small_or_constant_samples_say_nothing():
    assert metrics.spearman([1, 2], [2, 1]) is None  # too few
    assert metrics.pearson([1, 1, 1, 1], [1, 2, 3, 4], min_n=3) is None  # constant input
    assert metrics.r2([1, 2, 3], [2, 2, 2]) is None  # no variance to explain
    assert metrics.brier([(0.6, 1)] * 10) is None  # fewer than 30 pairs
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40], min_n=3) == pytest.approx(1.0)


def test_interval_hits_topk_and_calibration():
    assert metrics.within([10, 10, 10], [11, 14, 2], [2, 2, 2], 1) == pytest.approx(1 / 3)
    assert metrics.within([10, 10, 10], [11, 14, 2], [2, 2, 2], 2) == pytest.approx(2 / 3)
    pred = {"a": 9, "b": 8, "c": 1}
    assert metrics.top_k_hits(pred, {"a": 1, "b": 20, "c": 30, "d": 25}, k=2) == (0, 2)
    assert metrics.top_k_hits(pred, {"a": 1}, k=2) is None
    pairs = metrics.pairwise({"a": 12.0, "b": 10.0}, {"a": 2.0, "b": 2.0}, {"a": 5.0, "b": 9.0})
    assert len(pairs) == 1 and pairs[0][0] == pytest.approx(metrics.phi(2 / 8**0.5)) and pairs[0][1] == 0
    cal = metrics.calibration([(0.55, 1), (0.58, 0), (0.92, 1)])
    assert [(c["n"], round(c["happened"], 2)) for c in cal] == [(2, 0.5), (1, 1.0)]


def _season(tmp_path):
    """Three settled weeks: projections, final stats, league scoring and the schedule."""
    data = tmp_path / "data"
    league = {"scoring_settings": {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0}}
    (data / "2026").mkdir(parents=True)
    (data / "2026" / "league.json").write_text(json.dumps(league))
    sched = json.loads((ROOT / "data" / "2026" / "schedule.json").read_text())
    (data / "2026" / "schedule.json").write_text(json.dumps(sched))
    players = {f"p{i}": {"position": "LB", "team": "NYJ"} for i in range(60)}
    for week in (1, 2, 3):
        wd = data / "2026" / f"week{week:02d}"
        (wd / "sources").mkdir(parents=True)
        rows = {pid: {"stats": {"idp_tkl_solo": 3 + i * 0.05}} for i, pid in enumerate(players)}
        (wd / "sources" / "sleeper_proj.json").write_text(json.dumps({"rows": rows}))
        stats = {pid: {"gp": 1, "idp_tkl_solo": 3 + (i % 7)} for i, pid in enumerate(players) if i != 59}
        (wd / "actuals.json").write_text(json.dumps({"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats}))
        (wd / "players.json").write_text(json.dumps({"players": players}))
    return data


def test_evaluate_pools_baseline_and_residual_sigma(tmp_path):
    data = _season(tmp_path)
    cfg = load_config(ROOT / "config" / "sadl.toml")
    ev = evaluate(data, 2026, cfg)
    assert ev["weeks"] == [1, 2, 3]
    w1 = ev["weekly"][0]["by_slot"]["LB"]
    assert w1["n"] == 50 and w1["base_n"] == 0  # pool of 50; no earlier games in week 1
    assert ev["weekly"][2]["by_slot"]["LB"]["base_n"] == 50  # by week 3 everyone has 2 earlier games
    season = ev["season_by_slot"]["LB"]
    assert season["n"] == 150 and season["spearman"] is not None and season["mae"] > 0
    assert not ev["weekly"][0]["provisional"]
    sigma = residual_sigma_out(data, 2026, cfg, target_week=5)
    assert sigma is not None and sigma["LB"] > 0 and "DL" not in sigma  # no DL in this data: keeps last season
    assert residual_sigma_out(data, 2026, cfg, target_week=4) is None  # only weeks ≤ 2 count: too few
