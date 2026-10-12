import json
from pathlib import Path

import pytest

from sadlidp import scoring

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_ground_truth_acceptance_week4():
    """SPEC §10: our points equal Sleeper's players_points for every IDP with a stat line (week 4, real SADL data)."""
    gt = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    sleeper_points = {pid: pts for m in gt["matchups"] for pid, pts in (m.get("players_points") or {}).items()}
    mismatches = {
        pid: (round(scoring.points(st, gt["scoring_settings"]), 2), sleeper_points[pid])
        for pid, st in gt["stats"].items()
        if abs(scoring.points(st, gt["scoring_settings"]) - sleeper_points[pid]) > 0.01
    }
    assert len(gt["stats"]) >= 60
    assert mismatches == {}


def test_points_ignore_missing_and_unscored():
    sc = {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0, "pass_yd": 0.04}
    assert scoring.points({"idp_tkl_solo": 4, "idp_tkl_ast": 3, "gp": 1}, sc) == 11.0
    assert scoring.points({}, sc) == 0.0


def test_poisson_sf():
    assert scoring.poisson_sf(0, 1) == 0.0
    assert scoring.poisson_sf(2.0, 0) == 1.0
    assert scoring.poisson_sf(2.0, 1) == pytest.approx(1 - 0.1353352832, abs=1e-9)
    assert scoring.poisson_sf(10.0, 10) == pytest.approx(0.5420702855, abs=1e-6)


def test_projected_points_adds_bonus_expectation_only_when_scored_and_absent():
    sc = {"idp_tkl_solo": 1.0, "idp_tkl_ast": 0.5, "bonus_tkl_10p": 2.0}
    line = {"idp_tkl_solo": 6.0, "idp_tkl_ast": 4.0}
    base = scoring.points(line, sc)
    assert scoring.projected_points(line, sc) == pytest.approx(base + 2.0 * scoring.poisson_sf(10.0, 10))
    # a source-supplied bonus is used as-is, never added twice
    assert scoring.projected_points({**line, "bonus_tkl_10p": 0.3}, sc) == pytest.approx(base + 0.6)
    # SADL (no bonus keys) -> projected == plain points
    assert scoring.projected_points(line, {"idp_tkl_solo": 2.0}) == 12.0


def test_idp_keys():
    assert scoring.idp_keys({"idp_sack": 6, "idp_safe": 0, "pass_td": 4, "idp_int": 6.0}) == ["idp_int", "idp_sack"]
