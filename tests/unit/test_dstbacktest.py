"""The team-defense backtest (D18, PR 6b): lines, the clustered fit and the pass rule."""

import json
import random
from pathlib import Path

from sadlidp import dstbacktest

GAMES = """game_id,season,game_type,week,home_team,away_team,spread_line,total_line
2025_05_SF_LA,2025,REG,5,LA,SF,8.5,43.5
2025_05_X_Y,2025,REG,5,KC,BUF,-3,50
2024_05_SF_LA,2024,REG,5,LA,SF,1,40
2025_19_A_B,2025,POST,19,KC,BUF,1,40
"""


def test_lines_give_each_team_its_opponents_implied_points():
    out = dstbacktest.lines(GAMES, 2025, {"LA": "LAR"})
    assert out[5]["LAR"] == {"opp_implied": 17.5, "total": 43.5}  # SF expected to score (43.5 − 8.5)/2
    assert out[5]["SF"] == {"opp_implied": 26.0, "total": 43.5}
    assert out[5]["KC"]["opp_implied"] == 26.5 and set(out) == {5}  # other seasons and playoffs left out


def _archive(tmp_path, effect: float) -> Path:
    """18 weeks, 32 teams × 3 LBs; the miss carries `effect` points per D/ST rank place plus noise."""
    data = tmp_path / "data"
    arch = data / "2025" / "archive"
    arch.mkdir(parents=True)
    rng = random.Random(3)
    teams = [f"T{i:02d}" for i in range(32)]
    weeks = {}
    for week in range(1, 19):
        order = rng.sample(teams, 32)
        weeks[str(week)] = {t: {"rank": order.index(t) + 1, "opp_implied": 17 + rng.random() * 12} for t in teams}
        rows = {}
        for t in teams:
            for j in range(3):
                proj = 6 + rng.random() * 6
                act = proj + effect * weeks[str(week)][t]["rank"] + rng.gauss(0, 3)
                rows[f"{t}{j}"] = {"slot": "LB", "team": t, "proj": {"idp_tkl_solo": proj},
                                   "act": {"gp": 1, "idp_tkl_solo": max(0.0, act)}}  # fmt: skip
        (arch / f"week{week:02d}.json").write_text(json.dumps({"season": 2025, "week": week, "rows": rows}))
    (arch / "dst.json").write_text(json.dumps({"season": 2025, "weeks": weeks}))
    return data


def test_a_real_effect_passes_and_noise_does_not(tmp_path):
    sc = {"idp_tkl_solo": 1.0}
    rep = dstbacktest.run(_archive(tmp_path / "a", 0.12), 2025, sc)
    lb = rep["positions"]["LB"]
    assert lb["passes"]["rank"] and not lb["passes"]["opp_implied"] and rep["passes"]
    assert 0.08 < lb["fits"]["season"]["beta"]["rank"] < 0.16 and lb["fits"]["season"]["clusters"] > 500
    flat = dstbacktest.run(_archive(tmp_path / "b", 0.0), 2025, sc)
    assert not flat["passes"]
    s = dstbacktest.summary(flat)["positions"]["LB"]
    assert set(s["signals"]) == {"rank", "opp_implied"} and "with signals" in s["mae"]
    assert "stays out" in dstbacktest.format_report(flat)
