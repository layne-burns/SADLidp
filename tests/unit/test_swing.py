"""Per-player game-to-game swing (SPEC §6.7): the stat mix, swing² = a + b·V, the band, and the odds."""

import math

import pytest

from sadlidp import roster, swing
from sadlidp.pool import PlayerStatus

SC = {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0, "idp_sack": 6.0, "idp_int": 6.0, "pts_bonus": 1.0}
COEF = {"a": {"DL": 0.0, "LB": 18.0, "DB": 27.0}, "b": {"DL": 2.5, "LB": 1.35, "DB": 0.65}, "lo": -0.8, "hi": 0.75}
ROWS = {
    "sleeper_proj": {"a": {"stats": {"idp_tkl_solo": 4.0, "idp_tkl_ast": 2.0, "idp_sack": 0.5}}, "z": None},
    "espn_proj": {"a": {"stats": {"idp_tkl_solo": 2.0, "idp_sack": 0.0, "adp": 5}}, "r": {"stats": {}}},
    "fp_ecr": {"a": {"rank": 3}, "r": {"rank": 9}},
}
GROUPS = {"sleeper_proj": "projection", "espn_proj": "projection", "fp_ecr": "rank"}


def test_coefficients_and_poisson_variance():
    assert swing.coefficients({}) is None and swing.coefficients({"swing": {"enabled": False}}) is None
    c = swing.coefficients({"swing": {"enabled": True, "a": {"LB": 18}, "b": {"DL": 2.5}}})
    assert c == {"a": {"DL": 0.0, "LB": 18.0, "DB": 0.0}, "b": {"DL": 2.5, "LB": 1.0, "DB": 1.0}, "lo": -0.8,
                 "hi": 0.75}  # fmt: skip
    assert swing.poisson_var({"idp_tkl_solo": 4, "idp_sack": 0.5, "def_snp": 60}, SC) == 4 * 4 + 36 * 0.5


def test_mix_averages_the_projection_sources_by_their_shares():
    eff = {"sleeper_proj": 0.3, "espn_proj": 0.3, "fp_ecr": 0.4}  # the rank source carries no stats
    lam = swing.mix("a", ROWS, GROUPS, SC, eff)
    assert lam == {"idp_int": 0.0, "idp_sack": 0.25, "idp_tkl_ast": 1.0, "idp_tkl_solo": 3.0}
    sleeper_only = {"idp_int": 0.0, "idp_sack": 0.5, "idp_tkl_ast": 2.0, "idp_tkl_solo": 4.0}
    assert swing.mix("a", ROWS, GROUPS, SC, {"sleeper_proj": 1.0}) == sleeper_only
    assert swing.mix("a", ROWS, GROUPS, SC)["idp_tkl_solo"] == 3.0  # no shares: equal weights
    assert swing.mix("z", ROWS, GROUPS, SC) is None  # zero-filled: no stats anywhere
    assert swing.mix("r", ROWS, GROUPS, SC) is None  # rank only


def test_sigma_and_band():
    lam = {"idp_tkl_solo": 3.0, "idp_tkl_ast": 1.0, "idp_sack": 0.25}
    v = 4 * 3 + 1 + 36 * 0.25
    assert swing.sigma(lam, "DL", COEF, SC) == pytest.approx(math.sqrt(2.5 * v))
    assert swing.sigma(lam, "DB", COEF, SC) == pytest.approx(math.sqrt(27 + 0.65 * v))
    assert swing.sigma(lam, None, COEF, SC) == pytest.approx(math.sqrt(v))  # unknown position: a 0, b 1
    assert swing.band(10.0, 5.0, COEF) == (6.0, 13.75) and swing.band(2.0, 5.0, COEF) == (0.0, 5.75)


def test_apply_sets_swing_floor_and_ceiling_where_a_source_has_stats():
    cons = {"a": {"mu": 12.0, "sigma": 1.0, "eff": {"sleeper_proj": 0.5, "espn_proj": 0.5}},
            "z": {"mu": 0.0, "sigma": 1.0}, "r": {"mu": 7.0, "sigma": 1.0}}  # fmt: skip
    info = swing.apply(cons, {"a": "DL", "z": "DL", "r": "LB"}, ROWS, GROUPS, SC, COEF)
    s = math.sqrt(2.5 * (4 * 3 + 1 + 36 * 0.25))
    assert cons["a"]["swing"] == round(s, 2) and cons["a"]["floor"] == round(12 - 0.8 * s, 1)
    assert cons["a"]["ceiling"] == round(12 + 0.75 * s, 1)
    assert "swing" not in cons["z"] and "swing" not in cons["r"]  # nothing to build a mix from: σ_out applies
    assert info == {"players": 1, "mean": {"DL": round(s, 2), "LB": None, "DB": None}}


def test_panel_uses_each_players_swing_and_falls_back_to_sigma_out():
    players = {"s": {"position": "LB"}, "w": {"position": "LB"}}
    cons = {"s": {"mu": 10.0, "sigma": 1.0, "swing": 3.0}, "w": {"mu": 12.0, "sigma": 1.0}}
    statuses = {pid: PlayerStatus() for pid in players}

    def p_start(so):
        out = roster.panel({"players": ["s"]}, players, cons, statuses, {"LB": ["w"]}, {"LB": 2}, {"LB": so}, 0.5, {})
        return out["LB"]["badges"]["w"]["p"]

    # w has no swing: the position's σ_out; s uses his own 3.0 whatever σ_out says
    assert p_start(4.0) == round(roster.p_outscores(12, math.sqrt(1 + 16), 10, math.sqrt(1 + 9)), 3)
    assert p_start(8.0) == round(roster.p_outscores(12, math.sqrt(1 + 64), 10, math.sqrt(1 + 9)), 3)
