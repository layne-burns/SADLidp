"""End-to-end, offline: run_sync over real (trimmed) Sleeper/nflverse fixtures, then build the site."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sadlidp import cli
from sadlidp.config import load_config
from sadlidp.net import Response
from sadlidp.positions import slot_of
from sadlidp.schedule import NFLVERSE_GAMES, Schedule
from sadlidp.site.build import build_site, latest_week_dir
from sadlidp.sleeper import API, V1
from sadlidp.sync import run_sync

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
CFG = load_config(ROOT / "config" / "sadl.toml")
NOW = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)  # Tue 3 PM PT: inside the week-5 pre-waiver window
LEAGUE = json.loads((FIXTURES / "sleeper_league.json").read_text())
LID = LEAGUE["league_id"]
ROSTERS = json.loads((FIXTURES / "sleeper_rosters.json").read_text())
ME = ROSTERS[0]["owner_id"]
GAMES_CSV = (FIXTURES / "nflverse_games_2026.csv").read_text()
SCHED = Schedule.from_csv(GAMES_CSV, 2026, {"LA": "LAR"})


class FakeClient:
    """Exact-URL routes (query ignored): dict/list -> JSON, str -> raw text, int -> HTTP status; else 404."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        body = self.routes.get(url.split("?")[0], 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        raw = body if isinstance(body, str) else json.dumps(body)
        return Response(url=url, final_url=url, status=200, body=raw.encode())


def _players_and_projections():
    players = json.loads((FIXTURES / "sleeper_players_rostered.json").read_text())
    proj = json.loads((FIXTURES / "sleeper_projections_week05_sample.json").read_text())
    for r in proj:  # the sample's waiver players aren't in the rostered-players fixture
        p = r["player"]
        players.setdefault(
            r["player_id"],
            {
                "full_name": f"{p['first_name']} {p['last_name']}",
                "position": p["position"],
                "fantasy_positions": p["fantasy_positions"],
                "team": r["team"],
                "status": "Active",
                "active": True,
                "injury_status": p["injury_status"],
            },
        )
    players["4118"]["full_name"] = "Alex <i>Anzalone</i>"  # escaping check
    # unrostered LBs with a game: z1 is absent from the source (counts as 0), z2 is a placeholder (missing)
    z1 = {"full_name": "Zero Fill", "position": "LB", "fantasy_positions": ["LB"], "team": "NYJ", "status": "Active"}
    players["z1"] = {**z1, "active": True, "injury_status": None}
    players["z2"] = {**players["z1"], "full_name": "Place Holder"}
    proj.append({"player_id": "z2", "week": 5, "team": "NYJ", "opponent": None, "stats": {}})
    template = next(r for r in proj if r["player_id"] == "11017")
    for pid in ROSTERS[0]["players"]:  # weak projections for my IDPs that the sample lacks
        p = players.get(pid, {})
        opp = SCHED.opponent(p.get("team"), 5)
        if slot_of(p) and opp and not any(r["player_id"] == pid for r in proj):
            row = copy.deepcopy(template)
            row.update(player_id=pid, team=p["team"], opponent=opp.lstrip("@"))
            row["stats"] = {"gp": 1.0, "idp_tkl_solo": 1.0, "idp_tkl_ast": 1.0, "idp_tkl": 2.0}
            proj.append(row)
    return players, proj


def routes(season_type="regular", proj_status=None):
    players, proj = _players_and_projections()
    truth = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    return {
        f"{V1}/state/nfl": {"season": "2026", "season_type": season_type, "week": 5, "display_week": 5},
        NFLVERSE_GAMES: GAMES_CSV,
        f"{V1}/user/lbburnsy": {"user_id": ME},
        f"{V1}/user/{ME}/leagues/nfl/2026": [LEAGUE, {**LEAGUE, "league_id": "other", "name": "Another league"}],
        f"{V1}/league/{LID}": LEAGUE,
        f"{V1}/league/{LID}/rosters": ROSTERS,
        f"{V1}/league/{LID}/matchups/5": [],
        f"{V1}/players/nfl": players,
        f"{API}/projections/nfl/2026/5": proj_status or proj,
        f"{V1}/stats/nfl/regular/2026/4": truth["stats"],
    }


@pytest.fixture
def synced(tmp_path):
    data, cache = tmp_path / "data", tmp_path / "cache"
    client = FakeClient(routes())
    out = run_sync(CFG, data_dir=data, cache_dir=cache, now=NOW, client=client, log=lambda m: None)
    return out, data, cache, client


def test_gated_sync_writes_the_week(synced):
    out, data, _, _ = synced
    assert out.synced and (out.season, out.week, out.window) == (2026, 5, "2026-w05-prewaiver")
    week = data / "2026" / "week05"
    for name in ("consensus.json", "run.json", "rosters.json", "players_idp.json", "sources/sleeper_proj.json"):
        assert (week / name).exists(), name
    assert json.loads((data / "2026" / "gate.json").read_text()) == {"windows_done": ["2026-w05-prewaiver"]}
    league = json.loads((data / "2026" / "league.json").read_text())
    assert league["user_id"] == ME and league["settings"]["waiver_day_of_week"] == 2
    run = json.loads((week / "run.json").read_text())
    assert run["backfilled"] == [] and run["window"] == "2026-w05-prewaiver"  # offline: no past projections
    actuals = json.loads((data / "2026" / "week04" / "actuals.json").read_text())
    assert actuals["week"] == 4 and len(actuals["stats"]) > 50
    assert any("σ_out" in w for w in out.warnings)  # no 2025 stats offline: config defaults


def test_site_data_tiers_and_my_idps(synced):
    _, data, _, _ = synced
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    assert d["week"] == 5 and d["league"] == {
        "name": "Slow Ass Draft League",
        "waiver_type": "FAAB",
        "limits": {"DL": 2, "LB": 2, "DB": 2},
    }
    rostered = {pid for r in ROSTERS for k in ("players", "reserve", "taxi") for pid in r.get(k) or []}
    for slot, pos in d["positions"].items():
        cards = [p for t in pos["tiers"] for p in t["players"]]
        assert cards, slot
        assert not {p["id"] for p in cards} & rostered
        assert [p["mu"] for p in cards] == sorted((p["mu"] for p in cards), reverse=True)
        assert all(p["slot"] == slot and p["n"] == 1 and p["sigma"] == 1.0 for p in cards)
        assert [t["tier"] for t in pos["tiers"]] == list(range(1, len(pos["tiers"]) + 1))
    lb_cards = [p for t in d["positions"]["LB"]["tiers"] for p in t["players"]]
    assert (lb_cards[-1]["id"], lb_cards[-1]["mu"]) == ("z1", 0.0)  # §6.4: a complete source's silence = 0
    assert "z2" not in {p["id"] for p in lb_cards} and d["positions"]["LB"]["no_projection"] == 1
    assert d["positions"]["LB"]["tier_count"] == lb_cards[-1]["tier"]
    lb = d["my_idps"]["LB"]
    assert lb["count"] == 2 and {p["name"] for p in lb["players"]} == {"Cedric Gray", "Carson Schwesinger"}
    assert lb["suggestions"][0]["badge"] == "START" and lb["suggestions"][0]["action"] == "add_drop"
    assert lb["suggestions"][0]["drop"] in lb["names"]
    db = d["my_idps"]["DB"]  # Tykee Smith (12.0) starts; weak Lassiter is the upgrade target
    assert {s["badge"] for s in db["suggestions"]} == {"UPGRADE"}
    assert {db["names"][s["drop"]] for s in db["suggestions"]} == {"Kamari Lassiter"}
    text = (data / "2026" / "week05" / "consensus.json").read_text()
    assert "idp_tkl" not in text  # per-source values never reach the site data


def test_gate_runs_a_window_once_and_force_overrides(synced):
    _, data, cache, _ = synced
    client = FakeClient(routes())
    again = run_sync(
        CFG, data_dir=data, cache_dir=cache, now=NOW + timedelta(hours=1), client=client, log=lambda m: None
    )
    assert not again.synced and again.reason == "no gate window due"
    assert not any("/league/" in url or "/user/" in url for url in client.calls)  # gate read league.json
    forced = run_sync(CFG, data, cache, NOW + timedelta(hours=1), force=True, client=client, log=lambda m: None)
    assert forced.synced and forced.window is None
    assert json.loads((data / "2026" / "gate.json").read_text()) == {"windows_done": ["2026-w05-prewaiver"]}


def test_failed_source_keeps_earlier_rows(synced):
    _, data, cache, _ = synced
    out = run_sync(CFG, data, cache, NOW + timedelta(hours=1), force=True, client=FakeClient(routes(proj_status=503)))
    assert any(w.startswith("sleeper_proj: failed (HTTP 503)") for w in out.warnings)
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    assert d["positions"]["LB"]["tiers"] and d["sources"]["sleeper_proj"]["rows_used"] > 0


def test_no_usable_source_keeps_the_window_open(tmp_path):
    out = run_sync(
        CFG, tmp_path / "d", tmp_path / "c", NOW, client=FakeClient(routes(proj_status=404)), log=lambda m: None
    )
    assert out.synced and out.window == "2026-w05-prewaiver"
    assert any(w.startswith("no source has usable rows") for w in out.warnings)
    assert not (tmp_path / "d" / "2026" / "gate.json").exists()  # the next slot in the window retries


def test_backfill_writes_past_weeks_once(tmp_path):
    r = routes()
    week4 = [{**row, "week": 4} for row in r[f"{API}/projections/nfl/2026/5"][:5]]
    client = FakeClient({**r, f"{API}/projections/nfl/2026/4": week4})
    run_sync(CFG, tmp_path / "d", tmp_path / "c", NOW, force=True, client=client, log=lambda m: None)
    run = json.loads((tmp_path / "d" / "2026" / "week05" / "run.json").read_text())
    assert run["backfilled"] == [4]
    src = json.loads((tmp_path / "d" / "2026" / "week04" / "sources" / "sleeper_proj.json").read_text())
    assert src["week"] == 4 and len(src["rows"]) == 5 and all(row["backfill"] for row in src["rows"].values())
    run_sync(CFG, tmp_path / "d", tmp_path / "c", NOW, force=True, client=client, log=lambda m: None)
    assert json.loads((tmp_path / "d" / "2026" / "week05" / "run.json").read_text())["backfilled"] == []


def test_off_season_stops_before_league_calls(tmp_path):
    client = FakeClient(routes(season_type="off"))
    out = run_sync(CFG, tmp_path / "data", tmp_path / "cache", NOW, client=client, log=lambda m: None)
    assert not out.synced and out.reason.startswith("off-season")
    assert not any("/league/" in url or "/user/" in url for url in client.calls)


def test_build_site_renders_the_latest_week(synced, tmp_path):
    _, data, _, _ = synced
    assert latest_week_dir(data) == data / "2026" / "week05"
    index = build_site(data, tmp_path / "site")
    html = index.read_text()
    assert "Week 5" in html and "My IDPs" in html and "START" in html and "UPGRADE" in html
    assert "Alex &lt;i&gt;Anzalone&lt;/i&gt;" in html and "<i>Anzalone" not in html
    assert "drop Kamari Lassiter" in html and "1 available LB has no projection" in html
    site = index.parent
    assert json.loads((site / "data.json").read_text()) == json.loads((data / "2026/week05/consensus.json").read_text())
    assert (site / ".nojekyll").exists()


def test_cli_sync_build_and_leakcheck(tmp_path, monkeypatch, capsys):
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr("sadlidp.sync.Client", lambda: FakeClient(routes()))
    data, site = str(tmp_path / "data"), str(tmp_path / "site")
    cfg = str(ROOT / "config" / "sadl.toml")
    args = ["--config", cfg, "--data-dir", data, "--cache-dir", str(tmp_path / "c"), "--now", NOW.isoformat()]
    assert cli.main(["sync", *args]) == 0
    assert out_file.read_text() == "synced=true\nseason=2026\nweek=5\nwindow=2026-w05-prewaiver\n"
    assert cli.main(["build", "--data-dir", data, "--site-dir", site]) == 0
    assert cli.main(["leakcheck", data, site]) == 0
    assert "0 hits" in capsys.readouterr().out


TNF = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)  # Thu Oct 8, 9 PM ET: TB @ DAL has kicked off


def _with_waiver_db(r, solo):
    """A strong unrostered Sunday DB (NYJ) to compare against Tykee Smith (TB, plays Thursday)."""
    players, proj = r[f"{V1}/players/nfl"], r[f"{API}/projections/nfl/2026/5"]
    players["zdb"] = {"full_name": "Sunday Safety", "position": "S", "fantasy_positions": ["DB"], "team": "NYJ",
                      "status": "Active", "active": True, "injury_status": None}  # fmt: skip
    row = copy.deepcopy(next(x for x in proj if x["player_id"] == "11017"))
    row.update(player_id="zdb", team="NYJ", opponent="CLE")
    row["stats"] = {"gp": 1.0, "idp_tkl_solo": solo, "idp_tkl_ast": 2.0, "idp_tkl": solo + 2.0}
    proj.append(row)
    return r


def test_frozen_record_and_locked_suggestions_after_kickoff(tmp_path):
    data, cache = tmp_path / "d", tmp_path / "c"
    run_sync(CFG, data, cache, NOW, force=True, client=FakeClient(_with_waiver_db(routes(), 9.0)), log=lambda m: None)
    frozen = json.loads((data / "2026" / "week05" / "frozen.json").read_text())["players"]
    tykee_before = frozen["11705"]
    # Thursday night: Sleeper now projects everyone differently; TB/DAL players are locked
    later = _with_waiver_db(routes(), 9.5)
    for row in later[f"{API}/projections/nfl/2026/5"]:
        row["stats"] = {k: (v * 2 if k.startswith("idp_") else v) for k, v in row["stats"].items()}
    run_sync(CFG, data, cache, TNF, force=True, client=FakeClient(later), log=lambda m: None)
    frozen = json.loads((data / "2026" / "week05" / "frozen.json").read_text())["players"]
    assert frozen["11705"] == tykee_before  # frozen at his kickoff
    assert frozen["zdb"]["mu"] != tykee_before["mu"] and frozen["zdb"]["fetched_at"] == TNF.isoformat()
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    db = d["my_idps"]["DB"]
    tykee = next(p for p in db["players"] if p["id"] == "11705")
    assert tykee["mu"] == round(tykee_before["mu"], 2) and "locked" in tykee["unavailable"]
    assert db["starter_locked"]  # no lineup known, and S (Tykee) is locked
    start = next(s for s in db["suggestions"] if s["badge"] == "START")
    assert start["player"]["id"] == "zdb" and start["over"] == "11705" and start["reference"]
    html = build_site(data, tmp_path / "site").read_text()
    assert "START · ref" in html and "For reference: your DB starter&#39;s game has started" in html


def test_decision_logs(tmp_path):
    data = tmp_path / "d"
    run_sync(CFG, data, tmp_path / "c", NOW, client=FakeClient(routes()), log=lambda m: None)  # pre-waiver window
    week = data / "2026" / "week05"
    badges = json.loads((week / "badges.json").read_text())["badges"]
    assert [k for k in badges if k.startswith("4118|")] == ["4118|START|11742"]  # vs Cedric Gray
    anz = badges["4118|START|11742"]
    assert anz["active"] and anz["first_p"] == anz["p"] and anz["reference"] is False
    assert anz["lock_at"] == "2026-10-08T20:15:00-04:00"  # TB @ DAL kicks off before his LB comparison
    tiers = json.loads((week / "tiers_log.json").read_text())
    assert tiers["prewaiver"]["players"]["4118"]["tier"] == 1
    assert tiers["last_pre_kickoff"]["4118"]["slot"] == "LB"
    # after TNF kickoff the badge and the TB player's tier row are frozen
    run_sync(CFG, data, tmp_path / "c", TNF, force=True, client=FakeClient(routes()), log=lambda m: None)
    assert json.loads((week / "badges.json").read_text())["badges"]["4118|START|11742"] == anz
    assert (
        json.loads((week / "tiers_log.json").read_text())["last_pre_kickoff"]["4118"]
        == tiers["last_pre_kickoff"]["4118"]
    )
    assert tiers["last_pre_kickoff"]["4118"]["kickoff"] == "2026-10-08T20:15:00-04:00"  # stored with the record


def test_a_raising_adapter_never_fails_the_sync(tmp_path, monkeypatch):
    from sadlidp.sources import sleeper_proj

    def boom(*a, **k):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(sleeper_proj, "fetch", boom)
    out = run_sync(
        CFG, tmp_path / "d", tmp_path / "c", NOW, force=True, client=FakeClient(routes()), log=lambda m: None
    )
    assert out.synced and any(w.startswith("sleeper_proj: failed (RuntimeError in fetch)") for w in out.warnings)


FRIDAY = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)  # Fri 1:30 AM ET: TB @ DAL is final


def test_late_capture_live_results_and_played_suggestions(tmp_path):
    data, cache = tmp_path / "d", tmp_path / "c"
    run_sync(CFG, data, cache, NOW, client=FakeClient(routes()), log=lambda m: None)  # Tue pre-waiver: badges issued
    r = routes()
    r[f"{V1}/stats/nfl/regular/2026/5"] = {
        "4118": {"gp": 1, "idp_tkl_solo": 6, "idp_tkl_ast": 2, "idp_sack": 1},  # Anzalone: 12+2+6 = 20 pts
        "11705": {"gp": 1, "idp_tkl_solo": 3, "idp_pass_def": 1},  # Tykee Smith: 6+3 = 9 pts
    }
    out = run_sync(CFG, data, cache, FRIDAY, force=True, client=FakeClient(r), log=lambda m: None)
    assert out.synced
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    tykee = next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == "11705")
    assert tykee["result"] == {"pts": 9.0, "state": "final"}
    played = {p["id"]: p for p in d["positions"]["LB"]["played"]}
    assert played["4118"]["result"] == {"pts": 20.0, "state": "final"} and played["4118"]["tier"] == 1
    assert played["11017"]["result"] == {"pts": None, "state": "dnp"}  # TB/DAL final, no stat line
    sugg = {s["player"]["id"]: s for s in d["my_idps"]["LB"]["played_suggestions"]}
    anz = sugg["4118"]
    assert anz["badge"] == "START" and anz["player"]["result"]["pts"] == 20.0
    assert anz["over_result"] is None and anz["outcome"] is None  # his comparison plays Sunday
    html = build_site(data, tmp_path / "site").read_text()
    assert "This week's suggestions that have played" in html and "scored 20.0" in html
    assert "Already played this week" in html and "did not play" in html


def test_projection_first_seen_after_kickoff_is_kept_as_late(tmp_path):
    # the first-ever sync runs Thursday night: TB/DAL rows are kept (flagged late), not dropped
    run_sync(CFG, tmp_path / "d", tmp_path / "c", TNF, force=True, client=FakeClient(routes()), log=lambda m: None)
    week = tmp_path / "d" / "2026" / "week05"
    src = json.loads((week / "sources" / "sleeper_proj.json").read_text())
    assert src["rows"]["11705"]["late"] is True
    d = json.loads((week / "consensus.json").read_text())
    tykee = next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == "11705")
    assert tykee["mu"] is not None and tykee["late"] is True
    off = {**CFG, "sources": {**CFG["sources"], "sleeper_proj": {"enabled": True, "late_rows": False}}}
    run_sync(off, tmp_path / "d2", tmp_path / "c", TNF, force=True, client=FakeClient(routes()), log=lambda m: None)
    rows = json.loads((tmp_path / "d2" / "2026" / "week05" / "sources" / "sleeper_proj.json").read_text())["rows"]
    assert "11705" not in rows


WEEK6_TUE = datetime(2026, 10, 13, 22, 0, tzinfo=UTC)  # Tue 3 PM PT: week-6 pre-waiver window


def test_last_week_recap_shows_final_scores_of_issued_suggestions(tmp_path):
    data, cache = tmp_path / "d", tmp_path / "c"
    run_sync(CFG, data, cache, NOW, client=FakeClient(routes()), log=lambda m: None)  # week 5: suggestions issued
    r = routes()
    r[f"{V1}/state/nfl"] = {"season": "2026", "season_type": "regular", "week": 6, "display_week": 6}
    r[f"{V1}/league/{LID}/matchups/6"] = []
    r[f"{V1}/stats/nfl/regular/2026/5"] = {
        "4118": {"gp": 1, "idp_tkl_solo": 6, "idp_tkl_ast": 2, "idp_sack": 1},  # Anzalone 20 pts
        "11742": {"gp": 1, "idp_tkl_solo": 4},  # Cedric Gray 8 pts
        "11017": {"gp": 1, "idp_tkl_solo": 1},  # Dee Winters 2 pts
    }
    run_sync(CFG, data, cache, WEEK6_TUE, force=True, client=FakeClient(r), log=lambda m: None)
    d = json.loads((data / "2026" / "week06" / "consensus.json").read_text())
    lw = d["last_week"]
    assert lw["week"] == 5 and lw["provisional"]  # captured Tuesday, corrections until Friday night
    lb = {x["name"]: x for x in lw["slots"]["LB"]}
    anz = lb["Alex <i>Anzalone</i>"]
    assert anz["result"] == {"pts": 20.0, "state": "final"} and anz["over_result"]["pts"] == 8.0
    assert anz["outcome"] == "hit" and lb["Dee Winters"]["outcome"] == "miss"
    assert lb["Shemar James"]["outcome"] == "void"  # no stat line: didn't play
    assert lw["summary"]["hits"] >= 1 and lw["summary"]["decided"] >= 2
    html = build_site(data, tmp_path / "site").read_text()
    assert "Last week&#39;s suggestions (week 5)" in html or "Last week's suggestions (week 5)" in html
    assert "✓ paid off" in html and "suggestions paid off ↓" in html


def test_every_synced_week_gets_a_page_with_final_scores(tmp_path):
    data, cache = tmp_path / "d", tmp_path / "c"
    run_sync(CFG, data, cache, NOW, client=FakeClient(routes()), log=lambda m: None)  # week 5
    r = routes()
    r[f"{V1}/state/nfl"] = {"season": "2026", "season_type": "regular", "week": 6, "display_week": 6}
    r[f"{V1}/stats/nfl/regular/2026/5"] = {"4118": {"gp": 1, "idp_tkl_solo": 6, "idp_tkl_ast": 2, "idp_sack": 1}}
    run_sync(CFG, data, cache, WEEK6_TUE, force=True, client=FakeClient(r), log=lambda m: None)  # week 6
    site = tmp_path / "site"
    build_site(data, site)
    assert {p.name for p in site.iterdir()} >= {
        "index.html", "data.json", "week-2026-05.html", "week-2026-06.html", "data-2026-05.json", "data-2026-06.json",
    }  # fmt: skip
    assert (site / "index.html").read_text() == (site / "week-2026-06.html").read_text()
    past = (site / "week-2026-05.html").read_text()
    assert 'href="week-2026-06.html"' in past and "Week 6 · now" in past and 'aria-current="page"' in past
    assert "looking at a past week" in past and "provisional" in past
    w5 = json.loads((site / "data-2026-05.json").read_text())
    anz = next(p for t in w5["positions"]["LB"]["tiers"] for p in t["players"] if p["id"] == "4118")
    assert anz["result"] == {"pts": 20.0, "state": "final"}  # re-scored from week 5's actuals
    sugg = next(s for s in w5["my_idps"]["LB"]["suggestions"] if s["player"]["id"] == "4118")
    assert sugg["outcome"] == "void"  # Cedric Gray has no stat line in this fixture
    assert "looking at a past week" not in (site / "index.html").read_text()
