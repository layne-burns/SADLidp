"""Imputation of uncovered keys (SPEC.md §6.2): priors, shrinkage, anchors, fallbacks, and the validation
against Sleeper's own projected TFL and QB hits."""

from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

import pytest

from sadlidp import impute
from sadlidp.impute import IMPUTABLE, RATES, Imputer, build_priors, granular, load_or_build_priors

FIX = Path(__file__).resolve().parents[1] / "fixtures"
CHECKED = ("idp_tkl_loss", "idp_qb_hit")


def _recorded():
    """2025 weekly lines for a sample of IDPs, 2026 weeks 1–4 for the week-5 projection sample, and positions."""
    fx = json.loads((FIX / "sleeper_stats_idp_sample.json").read_text())

    def unpack(values):
        return dict(zip(fx["keys"], values, strict=False))  # trailing zeros were dropped

    weekly = {int(w): {pid: unpack(v) for pid, v in lines.items()} for w, lines in fx["last_season"].items()}
    history = {
        pid: [unpack(v) for _, v in sorted(weeks.items(), key=lambda kv: int(kv[0]))]
        for pid, weeks in fx["history"].items()
    }
    return weekly, history, fx["players"]


def _projections() -> list[dict]:
    return json.loads((FIX / "sleeper_projections_week05_sample.json").read_text())


def _validation_errors(imp: Imputer) -> dict[str, list[float]]:
    errors: dict[str, list[float]] = {k: [] for k in CHECKED}
    for row in _projections():
        stats = row["stats"]
        out, keys = imp.fill(row["player_id"], stats, set(stats) - set(CHECKED))
        assert set(CHECKED) <= set(keys)
        for k in CHECKED:
            errors[k].append(out[k] - (stats.get(k) or 0.0))
    return errors


def test_validation_against_sleeper_projected_tfl_and_qb_hits():
    """§6.2 validation: TFL and QB hits imputed from each Sleeper line's own tackles and sacks stay close to
    what Sleeper projects. Measured (priors from the recorded sample): TFL MAE 0.076 (mean projected 0.16 on the
    full week), QB hits MAE 0.203, of which ~0.09 is two rows where Sleeper projects ~1.9 QB hits on 0.05 sacks."""
    weekly, history, players = _recorded()
    priors = build_priors(weekly, players)
    errors = _validation_errors(Imputer(priors, history, players))
    mae = {k: statistics.mean(abs(e) for e in v) for k, v in errors.items()}
    assert len(errors["idp_tkl_loss"]) == 40
    assert mae["idp_tkl_loss"] < 0.10
    assert mae["idp_qb_hit"] < 0.25
    assert statistics.median(abs(e) for e in errors["idp_qb_hit"]) < 0.06
    points = [2 * tfl + qbh for tfl, qbh in zip(errors["idp_tkl_loss"], errors["idp_qb_hit"], strict=True)]
    assert abs(statistics.mean(points)) < 0.15  # SADL points bias per player (TFL 2, QB hit 1)
    # the player level of the hierarchy earns its place: position rates alone are clearly worse
    pos_only = _validation_errors(Imputer({"pos_rate": priors["pos_rate"], "m": priors["m"]}, {}, players))
    for k in CHECKED:
        assert mae[k] < 0.9 * statistics.mean(abs(e) for e in pos_only[k])


def test_build_priors_from_recorded_sample():
    weekly, _, players = _recorded()
    priors = build_priors(weekly, players)
    assert json.loads(json.dumps(priors)) == priors
    assert priors["version"] == 1
    assert set(priors["pos_rate"]) == {"DE", "DT", "LB", "CB", "S"}
    assert all(set(rates) == set(RATES) for rates in priors["pos_rate"].values())
    assert set(priors["m"]) == set(RATES)
    assert all(1 <= m <= 500 for m in priors["m"].values())
    tfl = {g: r["idp_tkl_loss"] for g, r in priors["pos_rate"].items()}
    assert tfl["DE"] > tfl["DT"] > tfl["LB"] > tfl["CB"]
    assert priors["pos_rate"]["DE"]["idp_qb_hit_pg"] > priors["pos_rate"]["CB"]["idp_qb_hit_pg"]
    # a 2025 starter (Dee Winters, LB): 17 games, his own TFL rate shrunk toward the LB rate
    counts, rate = priors["player_counts"]["11017"], priors["player_rate"]["11017"]["idp_tkl_loss"]
    assert counts["games"] == 17
    own = counts["idp_tkl_loss"] / counts["tkl"]
    m, pos = priors["m"]["idp_tkl_loss"], tfl["LB"]
    assert rate == pytest.approx((counts["idp_tkl_loss"] + m * pos) / (counts["tkl"] + m), rel=1e-3)
    assert min(own, pos) <= rate <= max(own, pos)
    assert set(priors["player_rate"]) == set(priors["player_counts"])


def _poisson(rng: random.Random, lam: float) -> int:
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def test_m_by_method_of_moments_recovers_gamma_poisson_shrinkage():
    """400 players, 16 games of 5 tackles; true TFL rates ~ Gamma with mean μ_p and m = μ/τ² = 50."""
    rng = random.Random(0)
    weekly: dict[int, dict[str, dict]] = {w: {} for w in range(1, 17)}
    players = {}
    for i in range(400):
        pid, pos = str(i), ("LB", "DE")[i % 2]
        mu = {"LB": 0.08, "DE": 0.2}[pos]
        lam = rng.gammavariate(mu * 50, 1 / 50)
        players[pid] = {"position": pos}
        for w in weekly:
            weekly[w][pid] = {"gp": 1, "idp_tkl_solo": 3, "idp_tkl_ast": 2, "idp_tkl_loss": _poisson(rng, lam * 5)}
    priors = build_priors(weekly, players)
    assert 35 <= priors["m"]["idp_tkl_loss"] <= 70
    assert priors["pos_rate"]["LB"]["idp_tkl_loss"] == pytest.approx(0.08, rel=0.1)
    assert priors["pos_rate"]["DE"]["idp_tkl_loss"] == pytest.approx(0.2, rel=0.1)
    assert priors["m"]["idp_def_td"] == impute.DEFAULT_M["idp_def_td"]  # no data: the default


def test_m_falls_back_to_default_without_excess_variance():
    weekly = {w: {str(i): {"gp": 1, "idp_tkl_solo": 5, "idp_tkl_loss": 0.5} for i in range(30)} for w in (1, 2)}
    players = {str(i): {"position": "LB"} for i in range(30)}
    assert build_priors(weekly, players)["m"]["idp_tkl_loss"] == impute.DEFAULT_M["idp_tkl_loss"]


def test_granular_positions():
    assert granular({"position": "NT"}) == "DT"
    assert granular({"position": "OLB"}) == "LB"
    assert granular({"position": "SS"}) == "S"
    assert granular({"position": "DB"}) == "CB"
    assert granular({"position": "DB", "depth_chart_position": "FS"}) == "S"
    assert granular({"position": "DB", "depth_chart_position": "NB"}) == "CB"
    assert granular({"position": "DL"}) == "DE"
    assert granular({"position": "DL", "depth_chart_position": "RDT"}) == "DT"
    assert granular({"position": "WR"}) is None
    assert granular(None) is None


def _rates(tfl: float, qbh_sack: float, qbh_game: float, rare: float) -> dict[str, float]:
    return {
        "idp_tkl_loss": tfl, "idp_qb_hit": qbh_sack, "idp_qb_hit_pg": qbh_game,
        "idp_def_td": rare, "idp_safe": rare / 2, "idp_blk_kick": rare / 4,
    }  # fmt: skip


PRIORS = {
    "version": 1,
    "pos_rate": {
        "DE": _rates(0.2, 2.3, 0.18, 0.004),
        "DT": _rates(0.12, 2.3, 0.14, 0.002),
        "LB": _rates(0.08, 2.1, 0.08, 0.004),
        "CB": _rates(0.04, 1.6, 0.02, 0.006),
        "S": _rates(0.035, 1.7, 0.05, 0.005),
    },
    "m": {k: 10.0 for k in RATES},
    "player_rate": {"1": _rates(0.1, 2.5, 0.1, 0.008), "2": _rates(0.25, 3.0, 0.2, 0.004)},
    "player_counts": {"1": {"games": 17, "tkl": 90, "sack": 1.0}, "2": {"games": 15, "tkl": 30, "sack": 1.5}},
}
PLAYERS = {"1": {"position": "LB"}, "2": {"position": "DE"}, "3": {"position": "DB", "depth_chart_position": "SS"}}
ESPN_KEYS = {"idp_tkl_solo", "idp_tkl_ast", "idp_tkl", "idp_sack", "idp_int", "idp_ff", "idp_fum_rec"}
ESPN_KEYS |= {"idp_pass_def", "idp_blk_kick"}


def test_covered_keys_are_never_touched():
    line = {"idp_tkl_solo": 4.0, "idp_tkl_ast": 2.0, "idp_sack": 0.3, "idp_blk_kick": 0.01}
    out, keys = Imputer(PRIORS, {}, PLAYERS).fill("1", line, ESPN_KEYS)
    assert keys == ["idp_def_td", "idp_qb_hit", "idp_safe", "idp_tkl_loss"]
    assert out["idp_blk_kick"] == 0.01
    assert {k: out[k] for k in line} == line
    assert line == {"idp_tkl_solo": 4.0, "idp_tkl_ast": 2.0, "idp_sack": 0.3, "idp_blk_kick": 0.01}  # a copy
    out, keys = Imputer(PRIORS, {}, PLAYERS).fill("1", {"idp_tkl_solo": 4.0}, ESPN_KEYS)
    assert "idp_blk_kick" not in out  # an absent covered key stays absent (it counts as 0)
    out, keys = Imputer(PRIORS, {}, PLAYERS).fill("1", {"idp_tkl_solo": 4.0, "idp_tkl_loss": 0.7}, set(IMPUTABLE))
    assert keys == [] and out == {"idp_tkl_solo": 4.0, "idp_tkl_loss": 0.7}


def test_shrinks_this_seasons_rate_toward_the_prior():
    history = {
        "1": [
            {"gp": 1, "idp_tkl_solo": 4, "idp_tkl_ast": 2, "idp_tkl_loss": 1},
            {"gp": 1, "idp_tkl_solo": 3, "idp_tkl_ast": 1, "idp_tkl_loss": 1},
            {"pass_yd": 0},  # didn't play: not a game
        ]
    }
    out, _ = Imputer(PRIORS, history, PLAYERS).fill("1", {"idp_tkl_solo": 4, "idp_tkl_ast": 2}, ESPN_KEYS)
    r_hat = (2 + 10 * 0.1) / (10 + 10)  # x = 2 TFL on exposure 10 tackles, prior 0.1, m = 10
    assert out["idp_tkl_loss"] == pytest.approx(6 * r_hat, abs=1e-4)
    # total-tackles-only line: the anchor is the total
    out, _ = Imputer(PRIORS, history, PLAYERS).fill("1", {"idp_tkl": 6}, {"idp_tkl"})
    assert out["idp_tkl_loss"] == pytest.approx(6 * r_hat, abs=1e-4)


def test_no_leakage_rates_come_from_history_only():
    weeks = {
        1: {"gp": 1, "idp_tkl_solo": 5, "idp_tkl_loss": 0},
        2: {"gp": 1, "idp_tkl_solo": 5, "idp_tkl_loss": 1},
        3: {"gp": 1, "idp_tkl_solo": 5, "idp_tkl_loss": 4},  # the target week's actual line
    }
    before = {"1": [weeks[1], weeks[2]]}
    line = {"idp_tkl_solo": 5, "idp_tkl_loss": 4}  # an uncovered actual on the source row is not read
    out, _ = Imputer(PRIORS, before, PLAYERS).fill("1", line, ESPN_KEYS)
    assert out["idp_tkl_loss"] == pytest.approx(5 * (1 + 10 * 0.1) / (10 + 10), abs=1e-4)
    leaked, _ = Imputer(PRIORS, {"1": [*before["1"], weeks[3]]}, PLAYERS).fill("1", line, ESPN_KEYS)
    assert leaked["idp_tkl_loss"] != out["idp_tkl_loss"]  # so the caller's cut-off is what keeps it clean


def test_rare_keys_ride_on_games():
    imp = Imputer(PRIORS, {"1": [{"gp": 1, "idp_tkl_solo": 6}] * 4}, PLAYERS)
    small, _ = imp.fill("1", {"idp_tkl_solo": 1, "idp_sack": 0}, ESPN_KEYS)
    big, _ = imp.fill("1", {"idp_tkl_solo": 9, "idp_sack": 1.2}, ESPN_KEYS)
    for key, prior in (("idp_def_td", 0.008), ("idp_safe", 0.004)):
        expected = round((0 + 10 * prior) / (4 + 10), 4)  # no events in 4 games, m = 10 games
        assert small[key] == big[key] == expected
    assert big["idp_tkl_loss"] > small["idp_tkl_loss"]


def test_qb_hits_ride_on_sacks_only_from_two_sacks():
    # "2": 1.5 sacks last season + 0.5 this season = 2 → per projected sack
    history = {"2": [{"gp": 1, "idp_sack": 0.5, "idp_qb_hit": 2}]}
    imp = Imputer(PRIORS, history, PLAYERS)
    out, _ = imp.fill("2", {"idp_sack": 0.4}, ESPN_KEYS)
    assert out["idp_qb_hit"] == pytest.approx(0.4 * (2 + 10 * 3.0) / (0.5 + 10), abs=1e-4)
    out, _ = imp.fill("2", {"idp_sack": 0}, ESPN_KEYS)
    assert out["idp_qb_hit"] == 0
    # "1": 1 sack last season, none this season → per game, whatever the projected sacks
    imp = Imputer(PRIORS, {"1": [{"gp": 1, "idp_qb_hit": 1}] * 2}, PLAYERS)
    a, _ = imp.fill("1", {"idp_sack": 0}, ESPN_KEYS)
    b, _ = imp.fill("1", {"idp_sack": 0.5}, ESPN_KEYS)
    assert a["idp_qb_hit"] == b["idp_qb_hit"] == pytest.approx((2 + 10 * 0.1) / (2 + 10), abs=1e-4)


def test_players_without_last_season_use_their_position_rate():
    imp = Imputer(PRIORS, {}, {**PLAYERS, "4": {"position": "WR"}})
    line = {"idp_tkl_solo": 4, "idp_tkl_ast": 1}
    assert imp.fill("3", line, ESPN_KEYS)[0]["idp_tkl_loss"] == pytest.approx(5 * 0.035)  # SS → S
    assert imp.fill("4", line, ESPN_KEYS)[0]["idp_tkl_loss"] == pytest.approx(5 * 0.08)  # not an IDP → LB
    assert imp.fill("999", line, ESPN_KEYS)[0]["idp_tkl_loss"] == pytest.approx(5 * 0.08)  # unknown → LB
    assert imp.fill("999", line, ESPN_KEYS)[0]["idp_qb_hit"] == pytest.approx(0.08)  # per game, no sacks known
    # with no priors at all, the built-in 2025 position rates
    fallback = Imputer({}, {}, PLAYERS).fill("2", line, ESPN_KEYS)[0]
    assert fallback["idp_tkl_loss"] == pytest.approx(5 * impute.FALLBACK_RATES["DE"][0], abs=1e-4)


def test_m_override_from_config():
    history = {"1": [{"gp": 1, "idp_tkl_solo": 10, "idp_tkl_loss": 5}]}
    line = {"idp_tkl_solo": 5}
    loose = Imputer(PRIORS, history, PLAYERS, m_override={"idp_tkl_loss": 1}).fill("1", line, ESPN_KEYS)[0]
    tight = Imputer(PRIORS, history, PLAYERS, m_override={"idp_tkl_loss": 1e9}).fill("1", line, ESPN_KEYS)[0]
    assert loose["idp_tkl_loss"] == pytest.approx(5 * (5 + 0.1) / (10 + 1), abs=1e-4)
    assert tight["idp_tkl_loss"] == pytest.approx(5 * 0.1, abs=1e-4)


class FakeSleeper:
    def __init__(self, weekly: dict[int, dict], missing: tuple[int, ...] = ()):
        self.weekly, self.missing, self.calls = weekly, missing, []

    def stats(self, season: int, week: int):
        self.calls.append((season, week))
        if week in self.missing:
            return None, "HTTP 404"
        return self.weekly.get(week, {}), None


def test_load_or_build_priors_builds_once_and_caches(tmp_path):
    weekly, _, players = _recorded()
    sleeper = FakeSleeper(weekly)
    priors = load_or_build_priors(sleeper, tmp_path, 2026, players, log=lambda m: None)
    assert [w for _, w in sleeper.calls] == list(range(1, 19)) and {s for s, _ in sleeper.calls} == {2025}
    assert priors["season"] == 2025 and priors["m"] == build_priors(weekly, players)["m"]
    assert (tmp_path / "2025" / "impute_priors.json").exists()
    again = FakeSleeper({})
    assert load_or_build_priors(again, tmp_path, 2026, players) == priors
    assert again.calls == []


def test_load_or_build_priors_gives_up_on_a_missing_week(tmp_path):
    logs: list[str] = []
    sleeper = FakeSleeper({}, missing=(7,))
    assert load_or_build_priors(sleeper, tmp_path, 2026, {}, log=logs.append) is None
    assert len(sleeper.calls) == 7
    assert "week 7" in logs[0] and "HTTP 404" in logs[0]
    assert not (tmp_path / "2025" / "impute_priors.json").exists()
