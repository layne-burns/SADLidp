"""Accuracy weights (SPEC §6.5 Implementation): error ratios, shrinkage, the group split, the lag, trust
rules and the write-once record."""

import json
import random
from datetime import datetime
from pathlib import Path

import pytest

from sadlidp import accuracy
from sadlidp.config import load_config
from sadlidp.consensus import consensus
from sadlidp.schedule import ET, Schedule

ROOT = Path(__file__).parents[2]
CFG = load_config(ROOT / "config" / "sadl.toml")
NOW = datetime(2026, 12, 1, 12, tzinfo=ET)


def _world(tmp_path, weeks=(1, 2, 3, 4), noisy="espn_proj", rank=False, backfill_espn=False):
    """60 LBs a week; Sleeper misses by ±1, `noisy` by ±6 (both centred), FantasyPros (if `rank`) by ±3."""
    data = tmp_path / "data"
    (data / "2026").mkdir(parents=True)
    (data / "2026" / "league.json").write_text(json.dumps({"scoring_settings": {"idp_tkl_solo": 1.0}}))
    sched = json.loads((ROOT / "data" / "2026" / "schedule.json").read_text())
    (data / "2026" / "schedule.json").write_text(json.dumps(sched))
    players = {f"p{i}": {"position": "LB", "team": "NYJ"} for i in range(60)}
    rng = random.Random(7)
    for week in weeks:
        wd = data / "2026" / f"week{week:02d}"
        (wd / "sources").mkdir(parents=True)
        truth = {pid: 4 + rng.randint(0, 12) for pid in players}
        miss = {"sleeper_proj": 1.0, noisy: 6.0}
        for name, sd in miss.items():
            rows = {pid: {"stats": {"idp_tkl_solo": max(0.0, t + rng.gauss(0, sd))}} for pid, t in truth.items()}
            if name == "espn_proj" and backfill_espn:
                rows = {pid: {**r, "backfill": True} for pid, r in rows.items()}
            (wd / "sources" / f"{name}.json").write_text(json.dumps({"rows": rows}))
        if rank:  # ranks in order of truth plus noise, mapped onto the projection mean
            order = sorted(players, key=lambda pid: -(truth[pid] + rng.gauss(0, 3)))
            rows = {pid: {"ranks": {"LB": i + 1}} for i, pid in enumerate(order)}
            doc = {"kind": "rank", "rows": rows, "universe": {"LB": {"complete": True, "depth": 60}},
                   "lists": {"LB": {pid: i + 1 for i, pid in enumerate(order)}}}  # fmt: skip
            (wd / "sources" / "fp_ecr.json").write_text(json.dumps(doc))
        stats = {pid: {"gp": 1, "idp_tkl_solo": t} for pid, t in truth.items()}
        (wd / "actuals.json").write_text(json.dumps({"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats}))
        (wd / "players.json").write_text(json.dumps({"players": players}))
    return data, Schedule.from_json(sched)


def test_the_noisier_source_loses_weight_shrunk_toward_equal(tmp_path):
    data, sched = _world(tmp_path)
    m = accuracy.measure(data, 2026, 4, sched, CFG)
    assert m["from_weeks"] == [1, 2, 3, 4]
    assert m["sources"]["espn_proj"]["mean_r"] > m["sources"]["sleeper_proj"]["mean_r"]
    rec = accuracy.weights(m, CFG)
    w = {n: s["w"] for n, s in rec["sources"].items()}
    assert rec["method"] == "accuracy" and w["sleeper_proj"] > 1 > w["espn_proj"]
    s = rec["sources"]["sleeper_proj"]  # 4 weeks against k = 4: halfway from 1 to the data weight
    assert s["w"] == pytest.approx(0.5 * s["w_data"] + 0.5, abs=1e-3)
    assert rec["alpha"] == CFG["weights"]["prior_split"] and rec["alpha_hat"] is None  # no rank source


def test_prior_until_three_weeks_and_the_two_week_lag(tmp_path):
    data, sched = _world(tmp_path, weeks=(1, 2, 3))
    assert accuracy.weights(accuracy.measure(data, 2026, 2, sched, CFG), CFG)["method"] == "prior"
    rec = accuracy.in_force(data, 2026, 5, sched, CFG, NOW)  # week 5 reads weeks ≤ 3
    assert rec["method"] == "accuracy" and rec["from_weeks"] == [1, 2, 3]
    rec4 = accuracy.in_force(data, 2026, 4, sched, CFG, NOW)  # week 4 reads weeks ≤ 2: too few
    assert rec4["method"] == "prior" and "2 evaluated" in rec4["reason"]


def test_unsettled_weeks_and_untrusted_archives_are_left_out(tmp_path):
    data, sched = _world(tmp_path, backfill_espn=True)
    act = data / "2026" / "week04" / "actuals.json"
    doc = json.loads(act.read_text())
    doc["captured_at"] = "2026-10-02T12:00:00+00:00"  # before week 4's stat corrections closed
    act.write_text(json.dumps(doc))
    cfg = {**CFG, "accuracy": {"untrusted": ["espn_proj"]}}
    m = accuracy.measure(data, 2026, 4, sched, cfg)
    assert m["from_weeks"] == [1, 2, 3] and "espn_proj" not in m["sources"]  # its rows are all archive
    assert "espn_proj" in accuracy.measure(data, 2026, 4, sched, CFG)["sources"]  # trusted: counted


def test_group_split_moves_toward_the_better_group(tmp_path):
    data, sched = _world(tmp_path, noisy="espn_proj", rank=True)
    rec = accuracy.weights(accuracy.measure(data, 2026, 4, sched, CFG), CFG)
    assert rec["alpha_n"] == 4 and 0.2 <= rec["alpha_hat"] <= 0.8
    k, prior = CFG["weights"]["k"], CFG["weights"]["prior_split"]
    assert rec["alpha"] == pytest.approx(4 / (4 + k) * rec["alpha_hat"] + k / (4 + k) * prior, abs=1e-3)
    assert rec["sources"]["fp_ecr"]["w"] == 1.0  # alone in its group: nothing to compare it with


def test_the_record_is_written_once_and_a_week_already_shown_keeps_its_shares(tmp_path):
    data, sched = _world(tmp_path)
    first = accuracy.in_force(data, 2026, 6, sched, CFG, NOW)
    path = data / "2026" / "week06" / "weights.json"
    assert json.loads(path.read_text()) == first and first["method"] == "accuracy"
    (data / "2026" / "week01" / "actuals.json").unlink()  # later data changes never rewrite a week's record
    assert accuracy.in_force(data, 2026, 6, sched, CFG, NOW) == first
    shown = data / "2026" / "week07"
    shown.mkdir()
    (shown / "consensus.json").write_text("{}")  # synced before PR 6: the shares it was shown with stay
    rec = accuracy.in_force(data, 2026, 7, sched, CFG, NOW)
    assert rec["method"] == "prior" and "before accuracy weights" in rec["reason"]


def test_consensus_applies_weights_and_alpha():
    values = {"a": {"x": 10.0}, "b": {"x": 4.0}, "r": {"x": 1.0}}
    groups = {"a": "projection", "b": "projection", "r": "rank"}
    out = consensus(values, groups, {"x": "LB"}, CFG, weights={"a": 3.0, "b": 1.0}, alpha=0.8)
    assert out["x"]["mu"] == pytest.approx(0.8 * (0.75 * 10 + 0.25 * 4) + 0.2 * 1)
    assert out["x"]["eff"] == {"a": 0.6, "b": 0.2, "r": 0.2}
