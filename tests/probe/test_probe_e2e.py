"""End-to-end probe run against a fake network: every section renders and nothing private leaks."""

import json
from pathlib import Path

import pytest

from probe import leakcheck, probe, sources_probe
from probe.phttp import Client, Response

SCORING = {"idp_tkl_solo": 1.0, "idp_tkl_ast": 0.5, "idp_sack": 3.0, "bonus_tkl_10p": 2.0}
PLAYERS = {
    "1": {"player_id": "1", "full_name": "Edge Hybrid", "position": "LB", "fantasy_positions": ["DL", "LB"],
          "team": "BUF", "status": "Active", "active": True, "injury_status": None},
    "2": {"player_id": "2", "full_name": "Pure DL", "position": "DE", "fantasy_positions": ["DL"],
          "team": "BUF", "status": "Active", "active": True, "injury_status": "Questionable"},
    "3": {"player_id": "3", "full_name": "Pure LB", "position": "LB", "fantasy_positions": ["LB"],
          "team": "KC", "status": "Active", "active": True, "injury_status": None},
    "9": {"player_id": "9", "full_name": "Quarterback", "position": "QB", "fantasy_positions": ["QB"], "team": "KC"},
}  # fmt: skip
SCHED_CSV = (
    "season,game_type,week,gameday,gametime,away_team,home_team\n"
    "2026,REG,4,2026-10-05,20:15,LA,KC\n"
    "2026,REG,5,2026-10-08,20:15,SF,LA\n"
    "2026,REG,5,2026-10-12,20:15,BUF,KC\n"
    "2026,REG,6,2026-10-15,20:15,SF,BUF\n"
)
CROSSWALK_CSV = "position,sleeper_id,fantasypros_id,espn_id,gsis_id\nLB,1,11,21,00-1\nDE,2,NA,,00-2\n"
PROJ_ROWS = [
    {"player_id": "1", "stats": {"gp": 1, "idp_tkl_solo": 4.4, "idp_tkl_ast": 2.1}, "opponent": "KC",
     "company": "rotowire", "player": {"position": "LB"}},
    {"player_id": "2", "stats": {"gp": 1, "idp_sack": 0.5}, "opponent": "KC", "company": "rotowire",
     "player": {"position": "DE"}},
]  # fmt: skip
STATS_W4 = {"1": {"idp_tkl_solo": 6, "idp_tkl_ast": 4, "idp_tkl": 10}, "2": {"idp_sack": 1}}
PUBLIC_HTML = (
    "<html><p>Week 6</p><table><tr><th>Rk</th><th>Player</th></tr><tr><td>1</td><td>Pub</td></tr></table></html>"
)
INDEX_HTML = '<html><a href="/idp/week-6-idp-rankings-projections">Week 6 IDP rankings</a></html>'
FP_SECRET_NAME = "Secret Premium Player"
FP_HTML = (
    "<html><a href='/accounts/logout/'>Log Out</a><p>Week 6</p>"
    "<table><thead><tr><th>RK</th><th>Player</th><th>Tackles</th></tr></thead>"
    f"<tbody><tr><td>1</td><td>{FP_SECRET_NAME}</td><td>7.5</td></tr></tbody></table>"
    f'<script>var ecrData = {{"players":[{{"player_name":"{FP_SECRET_NAME}","player_position_id":"LB","rank_ecr":1}}],'
    '"week":6,"year":2026};</script></html>'
)


def route(url: str):
    v1, api = "https://api.sleeper.app/v1", "https://api.sleeper.com"
    table = [
        (sources_probe.SCHEDULE_URLS[0], SCHED_CSV, "text/csv"),
        (sources_probe.CROSSWALK_URL, CROSSWALK_CSV, "text/csv"),
        (f"{v1}/state/nfl", {"season": "2026", "league_season": "2026", "week": 5, "display_week": 5}, None),
        (f"{v1}/players/nfl", PLAYERS, None),
        (f"{v1}/user/lbburnsy", {"user_id": "u1"}, None),
        (f"{v1}/user/u1/leagues/nfl/2026", [{"name": "SADL", "league_id": "L1"}, {"name": "Other", "league_id": "L2"}], None),
        (f"{v1}/league/L1/rosters", [{"roster_id": 1, "owner_id": "u1", "players": ["1", "2", "3"], "reserve": []}], None),
        (f"{v1}/league/L1/matchups/4", [{"roster_id": 1, "players_points": {"1": 10.0, "2": 3.0, "3": 0.0}}], None),
        (f"{v1}/league/L1/transactions/", [], None),
        (f"{v1}/league/L1", {"league_id": "L1", "name": "SADL", "status": "in_season", "season": "2026",
                             "settings": {"position_limit_dl": 2, "position_limit_lb": 2, "position_limit_db": 2,
                                          "waiver_type": 2, "waiver_day_of_week": 2},
                             "scoring_settings": SCORING, "roster_positions": ["DL", "LB", "DB", "BN"]}, None),
        (f"{v1}/stats/nfl/regular/2026/4", STATS_W4, None),
        (f"{v1}/stats/nfl/regular/2026/1", {"1": {"idp_tkl_solo": 5}}, None),
        (f"{api}/stats/nfl/2026/4", [{"player_id": k, "stats": v} for k, v in STATS_W4.items()], None),
        (f"{api}/projections/nfl/2026/", PROJ_ROWS, None),
        ("https://lm-api-reads.fantasy.espn.com/", {"players": []}, None),
        ("https://www.fantasypros.com/", FP_HTML, "text/html"),
        ("https://idpguru.com/", INDEX_HTML, "text/html"),
        ("https://www.dynastynerds.com/idp/", INDEX_HTML, "text/html"),
    ]  # fmt: skip
    for prefix, body, ctype in table:
        if url.startswith(prefix):
            return body, ctype
    if "week-6-idp" in url or url in sources_probe.PUBLIC_PAGES.values():
        return PUBLIC_HTML, "text/html"
    return None, None


@pytest.fixture
def fake_network(monkeypatch):
    seen = []

    def fake_get(self, url, *, headers=None, redact=()):
        self.request_count += 1
        seen.append((url, dict(headers or {})))
        body, ctype = route(url)
        if body is None:
            return Response(url=url, final_url=url, status=404, headers={"content-type": "text/html"}, body=b"nope")
        if not isinstance(body, str):
            body, ctype = json.dumps(body), "application/json"
        return Response(url=url, final_url=url, status=200, headers={"content-type": ctype}, body=body.encode())

    monkeypatch.setattr(Client, "get", fake_get)
    monkeypatch.setenv("FP_COOKIE", "sessionid=fakecookievalue123; csrftoken=othertokenvalue456")
    monkeypatch.delenv("FP_DATA_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    return seen


def test_probe_end_to_end(tmp_path, fake_network):
    out = tmp_path / "out"
    assert probe.main(["--out", str(out), "--now", "2026-10-12T18:00:00+00:00"]) == 0
    report = json.loads((out / "report.json").read_text())
    secs = report["sections"]
    assert not [k for k, v in secs.items() if isinstance(v, dict) and v.get("error")], secs.keys()

    sl = secs["sleeper"]
    assert sl["errors"] == []
    assert sl["league"]["league_id"] == "L1" and sl["my_roster_id"] == 1
    assert sl["ground_truth"]["week"] == 4
    assert sl["ground_truth"]["v1"]["match_with_derived_bonuses"] == 3
    assert sl["d14"]["rules"]["position"]["all"]["violations"] == []
    assert sl["projections"]["week"] == 5 and sl["projections"]["slot_positions"]["rows"] == 2
    assert sl["past_projections_week1"]["players_with_projection_and_actual"] == 1
    assert secs["schedule"]["target_week"] == 5
    assert secs["index_pages"]["idpguru"]["followed"]["status"] == 200
    assert secs["odds_api"]["status"] == "skipped"

    fp = secs["fantasypros"]
    assert fp["cookie_configured"] and set(fp["pages"]["rankings_idp"]) == {"public", "authenticated"}
    auth = fp["pages"]["rankings_idp"]["authenticated"]
    assert auth["ecrData"]["players"] == 1 and auth["markers"]["logout_link"]
    assert auth["tables"][0]["headers"] == ["RK", "Player", "Tackles"]

    # cookie only ever sent to fantasypros
    assert all("Cookie" not in h for u, h in fake_network if "fantasypros.com" not in u)
    # nothing private or secret anywhere in the output
    files = [p for p in Path(out).rglob("*") if p.is_file()]
    blob = b"".join(p.read_bytes() for p in files)
    assert FP_SECRET_NAME.encode() not in blob
    assert b"fakecookievalue123" not in blob and b"othertokenvalue456" not in blob
    assert leakcheck.main([str(out)]) == 0
    md = (out / "report.md").read_text()
    assert "D14" in md and "Probe report" in md
    assert (out / "samples" / "sleeper_ground_truth_week04.json").exists()
