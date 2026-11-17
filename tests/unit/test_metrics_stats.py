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


def test_a_tie_at_the_kth_score_counts_as_a_hit():
    pred = {f"p{i:02d}": 20 - i for i in range(12)}
    act = {f"p{i:02d}": 30 - i for i in range(11)} | {"p11": 10.0, "a99": 10.0}  # p11 tied for 12th
    assert metrics.top_k_hits(pred, act, 12) == (12, 12)


def test_partial_weeks_stay_out_of_the_season_and_tiers_come_from_the_prewaiver_log(tmp_path):
    data = _season(tmp_path)
    wd = data / "2026" / "week03"
    src = json.loads((wd / "sources" / "sleeper_proj.json").read_text())
    src["rows"] = dict(list(src["rows"].items())[:20])  # a snapshot saved after most games had started
    (wd / "sources" / "sleeper_proj.json").write_text(json.dumps(src))
    log = {"prewaiver": {"players": {"p0": {"slot": "LB", "tier": 1}, "p1": {"slot": "LB", "tier": 3}}}}
    (wd / "tiers_log.json").write_text(json.dumps(log))
    page = {"positions": {"LB": {"tiers": [{"tier": 1, "players": [{"id": "p5"}]}]}}}  # a live week's last page
    (wd / "consensus.json").write_text(json.dumps(page))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    w3 = ev["weekly"][2]
    assert w3["by_slot"]["LB"]["partial"] and w3["by_slot"]["LB"]["n"] == 20 and w3["partial_slots"] == ["LB"]
    assert ev["season_by_slot"]["LB"]["n"] == 100  # weeks 1-2 only
    assert ev["tiers"]["LB"] == [
        {"tier": 1, "n": 1, "avg": 6.0, "margin": None},
        {"tier": 3, "n": 1, "avg": 8.0, "margin": None},
    ]


def test_views_last_week_season_and_each_week(tmp_path):
    """§6.8 views: Last week = the newest week, All season = the season numbers, one view per week; a
    single week keeps its partial position, the season leaves it out; suggestions carry both scores."""
    data = _season(tmp_path)
    wd = data / "2026" / "week03"
    src = json.loads((wd / "sources" / "sleeper_proj.json").read_text())
    src["rows"] = dict(list(src["rows"].items())[:20])
    (wd / "sources" / "sleeper_proj.json").write_text(json.dumps(src))
    badges = {"k": {"w": "p0", "over": "p1", "badge": "UPGRADE", "slot": "LB", "p": 0.7, "active": True}}
    (data / "2026" / "week02" / "badges.json").write_text(json.dumps({"badges": badges}))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    views = {v["key"]: v for v in ev["views"]}
    assert [v["key"] for v in ev["views"]] == ["last", "season", "w1", "w2", "w3"]
    assert views["last"]["weeks"] == [3] and views["last"]["label"] == "Last week (week 3)"
    assert views["season"]["by_slot"]["LB"]["mae"] == ev["season_by_slot"]["LB"]["mae"]
    assert views["season"]["by_slot"]["LB"]["n"] == 100 and views["w3"]["by_slot"]["LB"]["n"] == 20
    assert views["w3"]["by_slot"]["LB"]["partial"] and views["w1"]["by_slot"]["LB"]["spearman"] is None
    call = views["w2"]["suggestions"]["list"][0]
    assert (call["name"], call["pts"], call["over_pts"], call["outcome"]) == ("p0", 6.0, 8.0, "miss")
    live = views["w2"]["suggestions"]["live"]
    assert (live["hits"], live["decided"], live["void"], live["expected"]) == (0, 1, 0, 0.7)
    assert views["w1"]["suggestions"]["list"] == [] and ev["suggestions"]["live"]["decided"] == 1


def test_report_card_totals_beats_and_calls():
    from sadlidp.site.build import report_card

    fin = {"state": "final"}
    page = {"my_idps": {
        "LB": {"players": [{"name": "A", "mu": 10.0, "result": {**fin, "pts": 18.0}},
                           {"name": "B", "mu": 12.0, "result": {**fin, "pts": 5.0}},
                           {"name": "C", "mu": None, "result": {**fin, "pts": 9.0}},
                           {"name": "IR", "mu": 8.0, "reserve": True, "result": {**fin, "pts": 0.0}}]},
        "DB": {"players": [{"name": "D", "mu": 9.0, "result": {"state": "dnp", "pts": None}}]},
    }}  # fmt: skip
    calls = [{"name": "W", "pts": 20.0, "over_name": "B", "over_pts": 5.0, "outcome": "hit", "badge": "UPGRADE"},
             {"name": "X", "pts": 6.0, "over_name": "B", "over_pts": 5.0, "outcome": "hit", "badge": "UPGRADE"},
             {"name": "Y", "pts": 2.0, "over_name": "A", "over_pts": 18.0, "outcome": "miss", "badge": "START"},
             {"name": "R", "pts": 30.0, "over_pts": 0.0, "outcome": "hit", "reference": True}]  # fmt: skip
    view = {"suggestions": {"live": {"hits": 2, "decided": 3}, "rebuilt": {"hits": 0, "decided": 0}, "list": calls}}
    c = report_card(page, view, 5, True)
    assert [x["name"] for x in c["players"]] == ["A", "C", "B", "D"]  # IR left out
    assert (c["projected"], c["scored"], c["counted"]) == (22.0, 23.0, 2)
    assert c["beat"]["name"] == "A" and c["short"]["name"] == "B" and (c["hits"], c["decided"]) == (2, 3)
    assert c["best_call"]["name"] == "W" and c["worst_call"]["name"] == "Y" and not c["rebuilt"]


def test_last_week_skips_a_week_still_being_played(tmp_path):
    data = _season(tmp_path)
    act = data / "2026" / "week03" / "actuals.json"
    doc = json.loads(act.read_text())
    doc["captured_at"] = "2026-09-27T20:00:00+00:00"  # Sunday afternoon of week 3: games still to come
    act.write_text(json.dumps(doc))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    assert ev["views"][0]["weeks"] == [2] and ev["views"][-1]["key"] == "w3"


def test_uncertainty_helpers():
    assert metrics.margin(0.1, 9) == pytest.approx(0.2306)  # t(0.975, 8 df) at 9 weeks
    assert metrics.margin(0.1, 12) == pytest.approx(0.2) and metrics.margin(0.1, 1) is None
    m, se = metrics.cluster_mean([[1.0, 2.0], [3.0, 4.0], [5.0]])
    assert m == pytest.approx(3.0) and se == pytest.approx((1.5 * (3**2 + 1**2 + 2**2)) ** 0.5 / 5)
    lo, hi = metrics.wilson(9, 11)
    assert lo == pytest.approx(0.523, abs=1e-3) and hi == pytest.approx(0.949, abs=1e-3)
    cov = metrics.coverage([1.0, 5.0, 9.0, 4.0], [4.0, 4.0, 4.0, 4.0], [8.0, 8.0, 8.0, 8.0])
    assert (cov["below"], cov["inside"], cov["above"]) == (0.25, 0.5, 0.25)  # on the floor counts as inside
    assert metrics.spread_ratio([0.0, 0.0], [2.0, -2.0], [2.0, 2.0]) == pytest.approx(1.0)
    pool = [f"p{i}" for i in range(50)]
    actual = {pid: (100.0 if i < 6 else 1.0) for i, pid in enumerate(pool)} | {f"x{i}": 100.0 for i in range(6)}
    exp, var = metrics.top_k_random(pool, actual, 12)
    assert exp == pytest.approx(12 * 6 / 50) and var > 0  # 6 of the actual top 12 were in the pool
    pairs = [(0.85, 1, "1:a")] * 13 + [(0.55, 0, f"1:{i}") for i in range(25)]
    rows = {r["lo"]: r for r in metrics.calibration(pairs)}
    assert rows[0.8]["favourites"] == 1 and rows[0.8]["too_few"] and not rows[0.5]["too_few"]


def _frozen(wd, recs):
    (wd / "frozen.json").write_text(json.dumps({"players": recs}))


def test_the_headline_grades_the_frozen_number_with_reference_rows(tmp_path):
    """§6.8: the number the page showed is graded, on the pool the plain average picks; before form and the
    plain average are reference rows; σ_pred and the range come from the frozen record."""
    data = _season(tmp_path)
    cfg = load_config(ROOT / "config" / "sadl.toml")
    plain = evaluate(data, 2026, cfg)
    wd = data / "2026" / "week02"
    src = json.loads((wd / "sources" / "sleeper_proj.json").read_text())["rows"]
    recs = {}
    for i, pid in enumerate(src):
        p = 2 * (3 + i * 0.05)
        recs[pid] = {"mu": p + 1.0, "base": p, "sigma": 1.0, "swing": 4.0, "floor": p - 3, "ceiling": p + 3}
    del recs["p30"]  # a pool player with no frozen record: the plain average stands in, counted
    _frozen(wd, recs)
    ev = evaluate(data, 2026, cfg)
    w2, before = ev["weekly"][1], plain["weekly"][1]
    assert w2["fallback"] == 1 and w2["by_slot"]["LB"]["n"] == 50
    assert w2["by_slot"]["LB"]["bias"] == pytest.approx(before["by_slot"]["LB"]["bias"] + 49 / 50)
    assert w2["by_slot"]["LB"]["ref"]["plain"] == pytest.approx(before["by_slot"]["LB"]["mae"])
    assert w2["by_slot"]["LB"]["ref"]["base"] == pytest.approx(before["by_slot"]["LB"]["mae"])  # base = plain here
    v = {x["key"]: x for x in ev["views"]}["w2"]["by_slot"]["LB"]
    assert v["coverage"]["n"] == 49 and v["ref"]["plain"]["margin"] is None  # one week: no margin
    t = cfg["tiers"]
    fallback_sig = (max(t["s0_default"], t["sigma_floor"]["LB"]) ** 2 + cfg["sigma_out"]["LB"] ** 2) ** 0.5
    ids = [i for i in range(9, 59)]  # the 50 best by the plain average who played (p59 didn't)
    pred = [2 * (3 + i * 0.05) + (0 if i == 30 else 1) for i in ids]
    sig = [fallback_sig if i == 30 else 17**0.5 for i in ids]  # σ_pred = √(σ_μ² + swing²) = √(1 + 16)
    assert v["coverage"]["spread"] == pytest.approx(metrics.spread_ratio(pred, [2 * (3 + i % 7) for i in ids], sig))
    season = ev["season_by_slot"]["LB"]
    assert season["ref"]["plain"]["weeks"] == 3 and season["ref"]["plain"]["margin"] is not None
    assert season["rank"]["weeks"] == 3 and ev["db_note"]["show"]  # no DBs at all: the note stays on


def test_suggestions_count_as_decisions(tmp_path):
    """§6.8: badges over the same (week, position, compared player, kind) are one decision, represented by
    the highest-p badge (ties: higher μ); reference badges are left out; a push is decided, not paid off."""
    data = _season(tmp_path)
    badges = {
        "a": {"w": "p0", "over": "p9", "badge": "UPGRADE", "slot": "LB", "p": 0.62, "mu_w": 9.0, "active": True},
        "b": {"w": "p6", "over": "p9", "badge": "UPGRADE", "slot": "LB", "p": 0.70, "mu_w": 8.0, "active": True},
        "c": {"w": "p13", "over": "p9", "badge": "UPGRADE", "slot": "LB", "p": 0.70, "mu_w": 9.5, "active": True},
        "d": {"w": "p1", "over": "p3", "badge": "START", "slot": "LB", "p": 0.66, "active": True},
        "e": {"w": "p2", "over": "p9", "badge": "UPGRADE", "slot": "LB", "p": 0.95, "active": True,
              "reference": True},
    }  # fmt: skip
    (data / "2026" / "week02" / "badges.json").write_text(json.dumps({"badges": badges}))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    w2 = {x["key"]: x for x in ev["views"]}["w2"]["suggestions"]
    live = w2["live"]
    # actual points: p13 → 2·(3+13%7)=18, p9 → 10, p1 → 8, p3 → 12: c (higher μ of the p=0.70 pair) hits, d misses
    assert (live["decided"], live["hits"], live["badges"]["decided"]) == (2, 1, 4)
    assert live["expected"] == pytest.approx(0.70 + 0.66) and live["wilson"] is None  # under 5: too few
    counted = {e["name"] for e in w2["list"] if e["counted"]}
    assert counted == {"p13", "p1"}
    assert live["net"] == pytest.approx(((18 - 10) + (8 - 12)) / 2)


def test_the_tier_table_scores_only_the_players_shown(tmp_path):
    """§6.8: the pre-waiver log holds the whole pool from week 6 on; only the `depth` shown per position
    count, in (tier, −μ) order, with rank buckets and the order's correlation."""
    data = _season(tmp_path)
    log = {f"p{i}": {"slot": "LB", "tier": 1 + i // 10, "mu": 20.0 - i * 0.1} for i in range(60)}
    for week in (1, 2, 3):
        (data / "2026" / f"week{week:02d}" / "tiers_log.json").write_text(json.dumps({"prewaiver": {"players": log}}))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    rows = ev["tiers"]["LB"]
    assert sum(r["n"] for r in rows) == 3 * 25 and [r["tier"] for r in rows] == [1, 2, 3]
    season = {x["key"]: x for x in ev["views"]}["season"]
    assert [b["n"] for b in season["tier_buckets"]["LB"]] == [15, 15, 45]
    assert season["tier_order"]["LB"]["weeks"] == 3


def test_measure_reports_the_week_to_week_noise_of_the_mix():
    from sadlidp import accuracy

    cells = {1: [1.0, 2.0], 2: [3.0, 4.0], 3: [1.0, 1.0]}
    se = accuracy._jackknife(cells, 5.0, 7.0)
    loo = [(5 - 1) / (7 - 2), (5 - 3) / (7 - 4), (5 - 1) / (7 - 1)]
    m = sum(loo) / 3
    assert se == pytest.approx((2 / 3 * sum((x - m) ** 2 for x in loo)) ** 0.5)
    assert accuracy._jackknife({1: [1.0, 2.0]}, 1.0, 2.0) is None


def test_report_card_counts_decisions_and_the_season():
    from sadlidp.site.build import report_card

    page = {"my_idps": {"LB": {"players": [{"name": "A", "mu": 10.0, "result": {"state": "final", "pts": 12.0}}]}}}
    view = {"suggestions": {"live": {"hits": 1, "decided": 2, "badges": {"decided": 5}}, "rebuilt": {}, "list": []},
            "by_slot": {"LB": {"median_miss": -0.4}}}  # fmt: skip
    season = {"suggestions": {"live": {"hits": 4, "decided": 6, "net": 5.0}, "rebuilt": {"hits": 5, "decided": 5,
                                                                                         "net": 10.6}}}  # fmt: skip
    c = report_card(page, view, 9, False, season)
    assert (c["hits"], c["decided"], c["badges"]) == (1, 2, 5)
    assert (c["season_hits"], c["season_decided"]) == (9, 11)
    assert c["season_net"] == pytest.approx((5.0 * 6 + 10.6 * 5) / 11) and c["median_miss"] == {"LB": -0.4}
