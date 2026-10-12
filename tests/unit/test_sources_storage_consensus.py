import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sadlidp import storage
from sadlidp.consensus import consensus
from sadlidp.sigma import within_player_sd
from sadlidp.sources import sleeper_proj

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)


class FakeSleeper:
    def __init__(self, payload, error=None):
        self.payload, self.error = payload, error

    def projections(self, season, week):
        return self.payload, self.error


def test_sleeper_proj_keeps_real_rows_and_marks_placeholders():
    rows = json.loads((FIXTURES / "sleeper_projections_week05_sample.json").read_text())
    placeholder = {"player_id": "999", "week": 5, "opponent": None, "stats": {}}
    res = sleeper_proj.fetch(FakeSleeper(rows + [placeholder]), 2026, 5, NOW)
    assert res.ok and len(res.rows) == len(rows) and res.no_projection_ids == {"999"}
    some = next(iter(res.rows.values()))
    assert "gp" in some["stats"] and not any(k.startswith("pts_") for k in some["stats"])
    assert {"idp_tkl_solo", "idp_sack"} <= res.covered_keys
    assert res.universe["LB"] == {"complete": True, "depth": None}


def test_sleeper_proj_wrong_week_and_failure():
    res = sleeper_proj.fetch(
        FakeSleeper([{"player_id": "1", "week": 4, "stats": {"gp": 1}, "opponent": "X"}]), 2026, 5, NOW
    )
    assert res.status == "not_yet_published" and res.week == 4
    res = sleeper_proj.fetch(FakeSleeper(None, "HTTP 503"), 2026, 5, NOW)
    assert res.status == "failed" and res.error == "HTTP 503"


def test_merge_rows_freeze_rule():
    kickoffs = {"thu": NOW - timedelta(days=1), "sun": NOW + timedelta(days=4), "gone": NOW + timedelta(days=4)}
    existing = {
        "thu": {"stats": {"idp_tkl_solo": 5}, "fetched_at": "earlier"},
        "sun": {"stats": {"idp_tkl_solo": 3}, "fetched_at": "earlier"},
        "gone": {"stats": {"idp_tkl_solo": 2}, "fetched_at": "earlier"},
    }
    fetched = {
        "thu": {"stats": {"idp_tkl_solo": 9}},  # post-kickoff value must not overwrite
        "sun": {"stats": {"idp_tkl_solo": 4}},
        "new": {"stats": {"idp_tkl_solo": 1}},
        "late": {"stats": {"idp_tkl_solo": 7}},  # first seen after his kickoff
    }
    kickoffs["late"] = NOW - timedelta(hours=1)
    merged, counts = storage.merge_rows(existing, fetched, NOW, kickoffs.get)
    assert merged["thu"] == existing["thu"]
    assert merged["sun"]["stats"] == {"idp_tkl_solo": 4} and merged["sun"]["fetched_at"] == NOW.isoformat()
    assert "gone" not in merged and "late" not in merged and "new" in merged
    assert counts == {
        "frozen": 1,
        "updated": 1,
        "added": 1,
        "deleted": 1,
        "skipped_after_kickoff": 1,
        "late": 0,
        "unchanged": 0,
    }
    # a source whose rows don't change after games may keep the late row, flagged, then frozen
    merged, counts = storage.merge_rows(existing, fetched, NOW, kickoffs.get, accept_late=True)
    assert merged["late"]["late"] is True and counts["late"] == 1 and counts["skipped_after_kickoff"] == 0
    assert merged["thu"] == existing["thu"]  # pre-kickoff rows still never touched
    again, _ = storage.merge_rows(merged, {"late": {"stats": {"idp_tkl_solo": 99}}}, NOW, kickoffs.get, True)
    assert again["late"]["stats"] == {"idp_tkl_solo": 7}


CFG = {"tiers": {"nu0": 2, "s0_default": 1.0, "sigma_floor": {"LB": 0.75, "DL": 0.75}}, "weights": {"prior_split": 0.6}}


def test_consensus_single_source_gets_s0_or_floor():
    out = consensus({"a": {"p": 10.0}}, {"a": "projection"}, {"p": "LB"}, CFG)
    assert out["p"]["mu"] == 10.0 and out["p"]["sigma"] == 1.0 and out["p"]["n_sources"] == 1
    out = consensus(
        {"a": {"p": 10.0}}, {"a": "projection"}, {"p": "LB"}, {**CFG, "tiers": {**CFG["tiers"], "s0_default": 0.2}}
    )
    assert out["p"]["sigma"] == 0.75


def test_consensus_group_split_and_spread():
    values = {"p1": {"x": 10.0}, "p2": {"x": 14.0}, "r1": {"x": 8.0}}
    groups = {"p1": "projection", "p2": "projection", "r1": "rank"}
    out = consensus(values, groups, {"x": "LB"}, CFG)
    assert out["x"]["mu"] == pytest.approx(0.6 * 12.0 + 0.4 * 8.0)
    s2 = 0.3 * (10 - 10.4) ** 2 + 0.3 * (14 - 10.4) ** 2 + 0.4 * (8 - 10.4) ** 2
    assert out["x"]["sigma"] == pytest.approx(math.sqrt((2 * s2 + 2 * s2) / 4))  # s0 = median over 3-source players
    assert (out["x"]["min"], out["x"]["max"]) == (8.0, 14.0)
    hidden = consensus(values, groups, {"x": "LB"}, CFG, private=frozenset({"r1"}))
    assert (hidden["x"]["min"], hidden["x"]["max"]) == (10.0, 14.0)


def test_within_player_sd():
    players = {"a": {"position": "LB"}, "b": {"position": "DE"}, "q": {"position": "QB"}}
    weekly = {
        w: {"a": {"gp": 1, "idp_tkl_solo": w % 3}, "b": {"gp": 1, "idp_sack": 1}, "q": {"gp": 1}} for w in range(1, 10)
    }
    weekly[9]["a"] = {"gp": 0, "idp_tkl_solo": 50}  # didn't play: excluded
    out = within_player_sd(weekly, players, {"idp_tkl_solo": 2.0, "idp_sack": 6.0}, min_games=8)
    pts = [2.0 * (w % 3) for w in range(1, 9)]
    assert out["LB"] == round(statistics.stdev(pts), 3) and out["DL"] == 0.0 and out["DB"] is None
    assert out["players"] == {"DL": 1, "LB": 1, "DB": 0}


def test_unchanged_rows_keep_their_first_seen_time_and_files_are_one_row_per_line(tmp_path):
    kick = {"a": NOW + timedelta(days=2), "b": NOW + timedelta(days=2)}.get
    first, _ = storage.merge_rows({}, {"a": {"stats": {"x": 1}}, "b": {"stats": {"x": 2}}}, NOW, kick)
    later = NOW + timedelta(hours=3)
    again, counts = storage.merge_rows(first, {"a": {"stats": {"x": 1}}, "b": {"stats": {"x": 5}}}, later, kick)
    assert again["a"]["fetched_at"] == NOW.isoformat() and again["b"]["fetched_at"] == later.isoformat()
    assert counts["unchanged"] == 1 and counts["updated"] == 1
    path = tmp_path / "s.json"
    storage.write_json(path, {"rows": again, "week": 5})
    lines = path.read_text().splitlines()
    assert sum(1 for line in lines if line.startswith('  "a": {')) == 1 and len(lines) == 7
    assert storage.read_json(path) == {"rows": again, "week": 5}


def test_an_old_format_file_keeps_its_last_fetch_in_fetches(tmp_path):
    from sadlidp.sources.base import AdapterResult

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"last_success": "2026-10-11T09:00:00-04:00", "rows": {}}))  # schema 1
    res = AdapterResult(name="x", kind="projection", status="ok", week=5, fetched_at="2026-10-12T05:00:00-04:00")
    storage.save_source(path, res, {})
    assert json.loads(path.read_text())["fetches"] == ["2026-10-11T09:00:00-04:00", "2026-10-12T05:00:00-04:00"]
