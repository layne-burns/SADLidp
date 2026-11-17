"""PR 22, data hygiene (full review 2026-11-17): the archive's game-time team, the read-only audit, the
late-source warning, the gap fill of a live week and the refresh of rebuilt frozen records (SPEC §5.3, §6.5, §7)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from test_backtest import SC, FakeSleeper
from test_multisource import TYKEE, Client, multi_routes
from test_sync_site import CFG, NOW

from sadlidp import audit, backfill, backtest, storage
from sadlidp.positions import slot_of
from sadlidp.replay import replay_week
from sadlidp.sleeper import V1
from sadlidp.sync import SOURCES, build_matcher, run_sync

ROOT = Path(__file__).parents[2]


def _row(pid, team, opp, player_team):
    return {"player_id": pid, "team": team, "opponent": opp, "player": {"position": "LB", "team": player_team},
            "stats": {"gp": 1, "idp_tkl_solo": 3.0}}  # fmt: skip


def test_the_archive_keeps_the_games_team_not_todays():
    doc = backtest.fetch_week(FakeSleeper({1: [_row("a", "NO", "NYG", "NYJ")]}, {1: {}}), 2025, 1, {}, SC)
    assert doc["rows"]["a"]["team"] == "NO" and doc["rows"]["a"]["opponent"] == "NYG"  # he plays for NYJ today


def test_patch_teams_changes_team_and_opponent_only(tmp_path):
    rows = {"a": {"slot": "LB", "team": "NYJ", "proj": {"idp_tkl_solo": 3.0}, "act": {"gp": 1}, "snap": [50, 60]},
            "c": {"slot": "DB", "team": None, "proj": {}, "act": {"gp": 1}}}  # fmt: skip
    storage.write_json(backtest.archive_dir(tmp_path, 2025) / "week01.json", {"season": 2025, "week": 1, "rows": rows})
    logs = []
    changed = backtest.patch_teams(FakeSleeper({1: [_row("a", "NO", "NYG", "NYJ")]}, {}), tmp_path, 2025, logs.append)
    doc = storage.read_json(backtest.archive_dir(tmp_path, 2025) / "week01.json")
    assert changed == {1: 1} and doc["rows"]["a"] == {**rows["a"], "team": "NO", "opponent": "NYG"}
    assert doc["rows"]["c"]["team"] is None and doc["rows"]["c"]["act"] == {"gp": 1}  # stats only: no team
    assert logs == ["backtest: 2025 week 1: 1 of 2 rows had another team"]


def _espn_backfill(data):
    db = multi_routes()[f"{V1}/players/nfl"]
    players, matcher = {pid: p for pid, p in db.items() if slot_of(p)}, build_matcher(CFG, db)
    logs = []

    def run(weeks, **kw):
        return backfill.run(SOURCES, CFG, data, 2026, weeks, NOW, client=Client(multi_routes()), sleeper=None,
                            players=players, matcher=matcher, only="espn_proj", log=logs.append, **kw)  # fmt: skip

    return run, logs


def test_a_gap_fill_adds_archive_rows_only_when_the_archive_matches_the_live_ones(tmp_path):
    data = tmp_path / "d"
    run, logs = _espn_backfill(data)
    assert run([2, 3]) == {"espn_proj": [2, 3]}
    for w, alter in ((2, False), (3, True)):  # a week first read live after most games: five live rows
        path = storage.week_dir(data, 2026, w) / "sources" / "espn_proj.json"
        doc = storage.load_source(path)
        live = dict(list(doc["rows"].items())[:5])
        live = {pid: {k: v for k, v in r.items() if k != "backfill"} for pid, r in live.items()}
        if alter:  # the archive no longer says what was read live for one of them
            pid = next(iter(live))
            live[pid] = {**live[pid], "stats": {**live[pid]["stats"], "idp_tkl_solo": 99.0}}
        storage.write_json(path, {**doc, "rows": live})
    assert run([2, 3]) == {}  # live weeks are left alone without the flag
    done = run([2, 3], fill_gaps=True)
    assert done == {"espn_proj": [2]}
    week2 = storage.load_source(storage.week_dir(data, 2026, 2) / "sources" / "espn_proj.json")["rows"]
    live = {pid: r for pid, r in week2.items() if not r.get("backfill")}
    assert len(live) == 5 and len(week2) == 44 and all(r["fetched_at"] == NOW.isoformat() for r in week2.values()
                                                       if r.get("backfill"))  # fmt: skip
    week3 = storage.load_source(storage.week_dir(data, 2026, 3) / "sources" / "espn_proj.json")["rows"]
    assert len(week3) == 5 and "the archive matches 4 of 5 live rows; gaps left unfilled" in logs[-1]


def test_replay_refreshes_rebuilt_records_and_never_a_live_one(tmp_path):
    data = tmp_path / "d"
    r = multi_routes()
    run_sync(CFG, data, tmp_path / "c", NOW, client=Client(r), log=lambda m: None)  # backfills week 4
    truth = json.loads((ROOT / "tests" / "fixtures" / "sleeper_week04_ground_truth.json").read_text())
    path = data / "2026" / "week04" / "frozen.json"
    live_pid = "11017"
    old = {TYKEE: {"mu": 99.0, "rebuilt": True}, live_pid: {"mu": 1.5, "kickoff": "x"}}
    for refresh, expect in ((False, 99.0), (True, None)):
        storage.write_json(path, {"schema_version": 1, "players": old})
        replay_week(CFG, data, 2026, 4, truth["matchups"], r[f"{V1}/players/nfl"], log=lambda m: None,
                    fill_frozen=True, refresh_rebuilt=refresh)  # fmt: skip
        recs = storage.read_json(path)["players"]
        assert recs[live_pid] == old[live_pid] and recs[TYKEE]["rebuilt"]
        assert (recs[TYKEE]["mu"] == 99.0) if expect else (recs[TYKEE]["mu"] != 99.0)


def test_a_source_first_read_after_most_games_gets_a_banner(tmp_path):
    monday = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)  # Mon 6 PM ET of week 5: only Monday night is left
    out = run_sync(CFG, tmp_path / "d", tmp_path / "c", monday, force=True, client=Client(multi_routes()),
                   log=lambda m: None)  # fmt: skip
    assert any(w.startswith("ESPN: ") and "already started and can't be used" in w for w in out.warnings)


def _audit_season(tmp_path):
    data = tmp_path / "data"
    season = data / "2026"
    season.mkdir(parents=True)
    (season / "schedule.json").write_text((ROOT / "data" / "2026" / "schedule.json").read_text())
    (season / "league.json").write_text(json.dumps({"scoring_settings": {"idp_tkl_solo": 1.0}}))
    for w, n in ((1, 100), (2, 100), (3, 100), (4, 20)):
        wd = season / f"week{w:02d}"
        rows = {f"p{i}": {"stats": {"idp_tkl_solo": 3.0}} for i in range(n)}
        storage.write_json(wd / "sources" / "sleeper_proj.json", {"kind": "projection", "rows": rows})
        storage.write_json(wd / "actuals.json", {"stats": {}})
        storage.write_json(wd / "consensus.json", {"swing": {"DL": {}}})
        storage.write_json(wd / "tiers_log.json", {"prewaiver": {"players": {"p0": {}}}})
    merge = {"skipped_after_kickoff": 80, "frozen": 0, "unchanged": 20}
    storage.write_json(season / "week04" / "run.json", {"sources": {"sleeper_proj": {"merge": merge}}})
    storage.write_json(season / "week03" / "consensus.json", {})  # a page without swing, and no pre-waiver log
    (season / "week03" / "tiers_log.json").unlink()
    kick = "2026-09-11T00:20:00+00:00"  # week 1's opener (Thu Sep 10, 8:20 PM ET)
    off = "2026-09-12T03:00:00+00:00"  # no game then
    storage.write_json(season / "week01" / "frozen.json", {"players": {"a": {"kickoff": kick}, "b": {"kickoff": off}}})
    storage.write_json(season / "week04" / "weights.json", {"method": "accuracy", "alpha": 0.6,
                                                             "sources": {"sleeper_proj": {"w": 5.0}}})  # fmt: skip
    return data


def test_the_audit_flags_each_problem_and_writes_nothing(tmp_path):
    data = _audit_season(tmp_path)
    before = sorted(p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file())
    rows = audit.run(data, 2026, CFG)
    warn = {(r["check"], r["week"]) for r in rows if r["status"] == "warn"}
    assert warn == {("rows", 4), ("late source", 4), ("page", 3), ("kickoff", 1), ("weights", 4)}
    assert sorted(p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()) == before  # read-only
    known = audit.run(data, 2026, {**CFG, "audit": {"acknowledged": ["2026-w04"]}})
    assert {(r["check"], r["week"]) for r in known if r["status"] == "known"} == {("rows", 4), ("late source", 4),
                                                                                  ("weights", 4)}  # fmt: skip
    assert "5 to look at" in audit.report(rows)
