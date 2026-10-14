import json
import random

import pytest

from sadlidp import backtest

SC = {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0, "idp_sack": 6.0, "idp_tkl_loss": 2.0}


class FakeSleeper:
    def __init__(self, proj, stats):
        self._proj, self._stats = proj, stats

    def projections(self, season, week):
        return self._proj.get(week), None if week in self._proj else "HTTP 404"

    def stats(self, season, week):
        return self._stats.get(week), None if week in self._stats else "HTTP 404"


def _proj(pid, pos, **stats):
    return {
        "player_id": pid,
        "player": {"position": pos, "team": "NYJ"},
        "stats": {"gp": 1, "adp_dd_ppr": 999, **stats},
    }


def test_fetch_week_keeps_projected_and_played_rows_compactly():
    proj = [_proj("a", "LB", idp_tkl_solo=3.456, idp_sack=0.0), _proj("b", "DE", idp_sack=0.31),
            _proj("z", "LB"), _proj("q", "QB", idp_tkl_solo=1)]  # fmt: skip
    stats = {"a": {"gp": 1, "idp_tkl_solo": 4, "def_snp": 50, "tm_def_snp": 60, "pts_std": 9},
             "c": {"gp": 1, "idp_tkl_solo": 2}, "b": {"gms_active": 1}}  # fmt: skip
    players = {"c": {"position": "CB"}}
    doc = backtest.fetch_week(FakeSleeper({1: proj}, {1: stats}), 2025, 1, players, SC)
    rows = doc["rows"]
    assert set(rows) == {"a", "b", "c"}  # z: no projection; q: not an IDP; b: projected, did not play
    assert rows["a"] == {"slot": "LB", "team": "NYJ", "proj": {"idp_tkl_solo": 3.46},
                         "act": {"gp": 1, "idp_tkl_solo": 4.0}, "snap": [50, 60]}  # fmt: skip
    assert rows["b"] == {"slot": "DL", "team": "NYJ", "proj": {"idp_sack": 0.31}}
    assert rows["c"]["slot"] == "DB" and rows["c"]["proj"] == {} and rows["c"]["act"] == {"gp": 1, "idp_tkl_solo": 2.0}
    assert backtest.fetch_week(FakeSleeper({}, {1: stats}), 2025, 1, players, SC) is None


def test_fetch_stops_at_the_first_missing_week_and_skips_archived_ones(tmp_path):
    proj = {w: [_proj("a", "LB", idp_tkl_solo=3)] for w in (1, 2)}
    stats = {w: {"a": {"gp": 1, "idp_tkl_solo": 2}} for w in (1, 2)}
    logs = []
    assert backtest.fetch(FakeSleeper(proj, stats), tmp_path, 2025, {}, SC, log=logs.append) == [1, 2]
    assert sorted(p.name for p in backtest.archive_dir(tmp_path, 2025).iterdir()) == ["week01.json", "week02.json"]
    assert logs[-1].startswith("backtest: 2025 week 3: unavailable")
    assert backtest.fetch(FakeSleeper(proj, stats), tmp_path, 2025, {}, SC, log=logs.append) == []
    rows = backtest.load(tmp_path, 2025, SC)
    assert [(r["week"], r["proj"], r["act"], r["snap"]) for r in rows] == [(1, 6.0, 4.0, None), (2, 6.0, 4.0, None)]


def test_lstsq_and_held_out_recover_a_planted_relationship():
    assert backtest.lstsq([[1, 0], [0, 1], [1, 1]], [2, 3, 5]) == pytest.approx([2, 3])
    rng = random.Random(0)
    rows = []
    for w in range(1, 11):
        for i in range(100):
            proj = 5 + i * 0.1
            form = rng.uniform(-3, 3)
            rows.append({"week": w, "pid": f"p{i}", "slot": "LB", "proj": proj, "form": form, "lsnap": 0.5,
                         "act": proj + 0.5 * form + rng.gauss(0, 1)})  # fmt: skip
    fit = backtest.held_out(rows, [lambda r: r["proj"], lambda r: r["form"]])
    assert (
        fit["n"] == 500
        and fit["beta"][0] == pytest.approx(1.0, abs=0.1)
        and fit["beta"][1] == pytest.approx(0.5, abs=0.1)
    )
    assert fit["r"] > 0.9
    assert backtest.held_out(rows[:12], [lambda r: r["proj"]])["r"] is None  # too few to hold out


def test_add_history_uses_earlier_games_only():
    rows = [{"week": w, "pid": "a", "slot": "LB", "proj": 10.0, "ps": {"idp_sack": 0.5}, "act": 10.0 + w,
             "snap": 0.1 * w, "sack": 1.0} for w in (1, 2, 3)]  # fmt: skip
    backtest.add_history(rows, k=4)
    assert [r["games"] for r in rows] == [0, 1, 2]
    assert [r["form"] for r in rows] == [0.0, pytest.approx(1 / 5 * 1.0), pytest.approx(2 / 6 * 1.5)]
    assert [r["lsnap"] for r in rows] == [None, pytest.approx(0.1), pytest.approx(0.2)]
    assert rows[2]["sack_form"] == pytest.approx(0.5)


def test_report_runs_end_to_end_on_a_small_season(tmp_path):
    rng = random.Random(1)
    for w in range(1, 7):
        rows = {}
        for slot, n in (("DL", 70), ("LB", 70), ("DB", 70)):
            for i in range(n):
                lam = {"idp_tkl_solo": 1 + 4 * i / n, "idp_tkl_ast": 1.0, "idp_sack": 0.5 * (slot != "DB") * i / n}
                act = {k: sum(rng.random() < v / 4 for _ in range(4)) for k, v in lam.items()}
                rows[f"{slot}{i}"] = {"slot": slot, "team": "X", "proj": lam, "act": {"gp": 1, **act},
                                      "snap": [40 + i % 20, 60]}  # fmt: skip
        (backtest.archive_dir(tmp_path, 2025)).mkdir(parents=True, exist_ok=True)
        (backtest.archive_dir(tmp_path, 2025) / f"week{w:02d}.json").write_text(json.dumps({"week": w, "rows": rows}))
    rows = backtest.load(tmp_path, 2025, SC)
    rep = backtest.report(rows, SC)
    assert rep["weeks"] == [1, 2, 3, 4, 5, 6] and rep["n"] == 6 * 210
    assert rep["skill"]["LB"]["all"]["r"] > 0.3 and rep["skill"]["LB"]["top 50"]["n"] == 300
    assert rep["form"]["DL"]["top 50"]["both"]["beta"] and rep["swing"]["DL"]["fifths"][-1]["predicted"] > 0
    assert rep["swing"]["DL"]["b"] >= 0 and rep["swing"]["DL"]["q20"] < 0 < rep["swing"]["DL"]["q80"]
    assert rep["sacks"]["n"] > 0 and len(rep["sacks"]["bins"]) >= 2
    assert set(rep["coef"]) == {"DL", "LB", "DB"} and rep["coef"]["LB"]["n"] > 0 and rep["coef"]["LB"]["snap"]
    text = backtest.format_report(rep, 2025)
    assert "Skill" in text and "Form" in text and "Swing" in text and "Sacks" in text and "[form]" in text
