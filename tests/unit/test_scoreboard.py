"""The decision scoreboard (SPEC §6.8): the ladder of rungs on the shown band, top-k lift with a player who
didn't play scoring 0, rank skill among those who played, paired differences with Sleeper alone, and pick
timing (Tuesday vs kickoff)."""

import json
from pathlib import Path

import pytest

from sadlidp import scoreboard
from sadlidp.config import load_config
from sadlidp.stats import evaluate

ROOT = Path(__file__).parents[2]
KINDS = {"sleeper_proj": "projection", "espn_proj": "projection", "fp_ecr": "rank", "rotoballer": "rank"}


def test_prior_blend_uses_the_starting_shares():
    parts = {"sleeper_proj": 10.0, "espn_proj": 14.0, "fp_ecr": 9.0, "rotoballer": 12.0}
    priors = {"fp_ecr": 2.0, "rotoballer": 1.0}
    assert scoreboard.prior_blend(parts, KINDS, priors, 0.6) == pytest.approx(0.6 * 12.0 + 0.4 * 10.0)
    assert scoreboard.prior_blend({"fp_ecr": 9.0}, KINDS, priors, 0.6) == 9.0  # one group: it alone


def _band(n=10, dnp=()):
    """n shown players: Sleeper orders them backwards, ESPN and our number forwards; scores n−i."""
    rows = []
    for i in range(n):
        pid = f"p{i}"
        rows.append({"pid": pid, "pts": 0.0 if pid in dnp else float(n - i), "played": pid not in dnp,
                     "tue": float(n - i), "vals": {"sleeper": float(i), "espn": float(n - i), "blend": float(n - i),
                                                   "weights": float(n - i), "form": float(n - i)}})  # fmt: skip
    return rows


def test_lift_counts_a_player_who_did_not_play_as_zero_and_skill_leaves_him_out():
    rows = _band(dnp={"p0"})
    ws = scoreboard.week_stats({"LB": rows})["LB"]
    mean = sum(r["pts"] for r in rows) / 10
    assert ws["rungs"]["form"]["top3"] == pytest.approx((0 + 9 + 8) / 3 - mean)  # his top pick sat: 0
    assert ws["rungs"]["form"]["r"] == pytest.approx(1.0) and ws["rungs"]["sleeper"]["r"] == pytest.approx(-1.0)
    assert ws["timing"]["dnp_top5_tue"] == 1 and ws["timing"]["tue"] == ws["timing"]["kick"]


def test_aggregate_pairs_each_rung_with_sleeper_week_by_week():
    weeks = {w: scoreboard.week_stats({"LB": _band(), "DL": _band()}) for w in (1, 2, 3)}
    out = scoreboard.aggregate(weeks)
    form = next(r for r in out["all"]["rows"] if r["key"] == "form")
    assert form["vs_sleeper"]["mean"] == pytest.approx(2.0) and form["vs_sleeper"]["weeks"] == 3
    assert out["DB"]["rows"][0]["r"]["weeks"] == 0 and out["all"]["weeks"] == [1, 2, 3]


def test_evaluate_scores_the_shown_band_from_the_frozen_record(tmp_path):
    data = tmp_path / "data"
    season = data / "2026"
    season.mkdir(parents=True)
    (season / "league.json").write_text(json.dumps({"scoring_settings": {"idp_tkl_solo": 1.0}}))
    (season / "schedule.json").write_text((ROOT / "data" / "2026" / "schedule.json").read_text())
    players = {f"p{i}": {"position": "LB", "team": "NYJ"} for i in range(30)}
    for w in (1, 2):
        wd = season / f"week{w:02d}"
        (wd / "sources").mkdir(parents=True)
        (wd / "players.json").write_text(json.dumps({"players": players}))
        rows = {pid: {"stats": {"idp_tkl_solo": 5.0}} for pid in players}
        (wd / "sources" / "sleeper_proj.json").write_text(json.dumps({"rows": rows}))
        stats = {f"p{i}": {"gp": 1, "idp_tkl_solo": 30 - i} for i in range(30)}
        (wd / "actuals.json").write_text(json.dumps({"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats}))
        log = {f"p{i}": {"slot": "LB", "tier": 1 + i // 10, "mu": 20.0 - i * 0.5} for i in range(30)}
        (wd / "tiers_log.json").write_text(json.dumps({"prewaiver": {"players": log}}))
        frozen = {f"p{i}": {"mu": 20.0 - i * 0.5, "base": 20.0 - i * 0.5,
                            "parts": {"sleeper_proj": float(i)}} for i in range(30)}  # fmt: skip
        (wd / "frozen.json").write_text(json.dumps({"players": frozen}))
    ev = evaluate(data, 2026, load_config(ROOT / "config" / "sadl.toml"))
    sb = {v["key"]: v for v in ev["views"]}["season"]["scoreboard"]
    rows = {r["key"]: r for r in sb["LB"]["rows"]}
    assert rows["form"]["r"]["mean"] == pytest.approx(1.0) and rows["sleeper"]["r"]["mean"] == pytest.approx(-1.0)
    assert (
        rows["form"]["vs_sleeper"]["mean"] == pytest.approx(2.0) and sb["LB"]["timing"]["kick_minus_tue"]["weeks"] == 2
    )
    assert {v["key"]: v for v in ev["views"]}["w1"]["scoreboard"] is None  # one week: not shown
