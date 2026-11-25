"""A second league (D26), end to end and offline: the same syncs run with SADL alone and with SADL plus a
K/DEF-only league. SADL's files must come out byte for byte the same; the second league's page must show its own
roster's kickers and defenses from the one shared consensus; and a failing second league must never touch SADL."""

import copy
import json
import re
from pathlib import Path

import pytest
from test_kickers_isolation import MINE, KClient, k_routes
from test_sync_site import CFG, FIXTURES, FRIDAY, ME, NOW, SCHED, TNF

from sadlidp.net import Response
from sadlidp.site.build import build_all, build_site, week_dirs
from sadlidp.sleeper import API, V1
from sadlidp.sync import run_sync

PROJ = json.loads((FIXTURES / "sleeper_def_projections_week13_trimmed.json").read_text())
CROOKED = json.loads((FIXTURES / "sleeper_league_crooked.json").read_text())
CLID = CROOKED["league_id"]
DEF_ROUTE = f"{API}/projections/nfl/2026/5#DEF"
CFG_ONE = {k: v for k, v in CFG.items() if k != "leagues"}
CFG_TWO = {**CFG_ONE, "leagues": {"crooked": {"names": [CROOKED["name"]], "league_id": CLID, "label": "Crooked"}}}
TEAMS = ("JAX", "TB", "DAL", "MIN", "NE", "LAC", "BUF", "DET", "NYJ", "PHI")


def defense(team: str) -> dict:
    return {"position": "DEF", "fantasy_positions": ["DEF"], "team": team, "first_name": team, "last_name": "Defense",
            "active": True, "status": None, "injury_status": None, "player_id": team}  # fmt: skip


class DefClient(KClient):
    """KClient that also tells the team-defense request apart: it is served from the route ending "#DEF"."""

    def get(self, url, **kw):
        if "position[]=DEF" not in url:
            return super().get(url, **kw)
        self.calls.append(url)
        body = self.routes.get(url.split("?")[0] + "#DEF", 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        return Response(url=url, final_url=url, status=200, body=json.dumps(body).encode())


def def_routes(crooked: bool = True, **over) -> dict:
    """The kicker world plus team defenses (status None, as Sleeper lists them; KC and CAR on bye), their week-5
    projections (the trimmed real week-13 rows, relabelled) and last week's lines, and the Crooked league."""
    r = k_routes()
    players = r[f"{V1}/players/nfl"]
    rows = []
    playing = [t for t in TEAMS if SCHED.opponent(t, 5)]
    for i, team in enumerate(playing):
        players[team] = defense(team)
        src = copy.deepcopy(PROJ[i % len(PROJ)])
        src.update(player_id=team, team=team, week=5, opponent=SCHED.opponent(team, 5).lstrip("@"))
        src["player"] = {"position": "DEF", "fantasy_positions": ["DEF"], "first_name": team, "last_name": "D"}
        rows.append(src)
    players["KC"] = defense("KC")  # on bye in week 5
    r[DEF_ROUTE] = rows
    r[f"{V1}/stats/nfl/regular/2026/4"] = {
        **r[f"{V1}/stats/nfl/regular/2026/4"],
        "JAX": {"gp": 1.0, "sack": 3.0, "int": 1.0, "pts_allow": 17.0, "pts_allow_14_20": 1.0},
        "TEAM_JAX": {"gp": 1.0, "sack": 3.0},
    }
    if crooked:
        mine = {"roster_id": 1, "owner_id": ME, "players": [MINE, "KC", "DET"], "reserve": [], "taxi": [],
                "starters": [MINE, "DET"]}  # fmt: skip
        theirs = {"roster_id": 2, "owner_id": "x", "players": ["TB", "MIN"], "reserve": [], "taxi": []}
        r[f"{V1}/league/{CLID}"] = CROOKED
        r[f"{V1}/league/{CLID}/rosters"] = [mine, theirs]
        r[f"{V1}/league/{CLID}/matchups/5"] = []
    r.update(over)
    return r


def _run(cfg, data: Path, cache: Path, routes_of) -> list:
    out = []
    for now, force in ((NOW, False), (TNF, True), (FRIDAY, True)):  # pre-waiver; TB @ DAL kicked off; final
        out.append(run_sync(cfg, data, cache, now, force=force, client=DefClient(routes_of()), log=lambda m: None))
    return out


def files(data: Path, skip_leagues: bool = True) -> dict[str, bytes]:
    return {p.relative_to(data).as_posix(): p.read_bytes() for p in sorted(data.rglob("*.json"))
            if not (skip_leagues and p.relative_to(data).parts[0] == "leagues")}  # fmt: skip


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("leagues")
    one = _run(CFG_ONE, tmp / "one", tmp / "c1", def_routes)
    two = _run(CFG_TWO, tmp / "two", tmp / "c2", def_routes)
    bad = _run(CFG_TWO, tmp / "bad", tmp / "c3", lambda: def_routes(**{f"{V1}/league/{CLID}/rosters": 503}))
    return tmp, one, two, bad


def test_sadl_files_are_byte_identical_with_a_second_league(worlds):
    tmp, one, two, _bad = worlds
    a, b = files(tmp / "one"), files(tmp / "two")
    assert a == b  # every SADL and shared file, run.json and frozen.json included
    assert [o.warnings for o in one] == [o.warnings for o in two]
    assert all(o.leagues["crooked"]["status"] == "rendered" for o in two) and not any(o.leagues for o in one)
    assert [w[:2] for w in week_dirs(tmp / "two")] == [(2026, 5)]  # the league folder is never read as a SADL week


def test_the_second_league_keeps_only_its_own_files(worlds):
    tmp = worlds[0]
    base = tmp / "two" / "leagues" / "crooked" / "2026"
    got = sorted(p.relative_to(base).as_posix() for p in base.rglob("*.json"))
    assert got == ["league.json", "week05/badges.json", "week05/consensus.json", "week05/rosters.json",
                   "week05/tiers_log.json"]  # fmt: skip
    league = json.loads((base / "league.json").read_text())
    assert league["league_id"] == CLID and league["scoring_settings"] == CROOKED["scoring_settings"]


def test_the_second_league_page_shows_its_kickers_and_defenses_from_the_shared_consensus(worlds):
    tmp = worlds[0]
    page = json.loads((tmp / "two" / "leagues" / "crooked" / "2026" / "week05" / "consensus.json").read_text())
    sadl = json.loads((tmp / "two" / "2026" / "week05" / "consensus.json").read_text())
    frozen = json.loads((tmp / "two" / "2026" / "week05" / "frozen.json").read_text())["players"]
    assert sorted(page["positions"]) == ["DEF", "K"] and sorted(page["my_idps"]) == ["DEF", "K"]
    assert sorted(sadl["positions"]) == ["DB", "DL", "K", "LB"]  # SADL shows no DEF
    assert page["league"]["limits"] == {} and not any("position limit" in w for w in page["warnings"])
    assert page["sigma_out"] == {"K": 5.0, "DEF": 5.7}
    assert {g for g in (s.get("group") for s in page["sources"].values())} == {"k", "def"}  # no IDP source, no signal
    assert not any("sleeper_def" in n or "fp_def" in n for n in sadl["sources"])
    mine = page["my_idps"]["DEF"]
    assert {p["id"] for p in mine["players"]} == {"DET", "KC"}
    assert mine["limit"] is None and "open_spots" in mine
    kc = next(p for p in mine["players"] if p["id"] == "KC")
    assert kc["unavailable"] == ["bye"]  # a defense with no status is listed; its bye still counts
    listed = [p for t in page["positions"]["DEF"]["tiers"] for p in t["players"]]
    assert listed and {p["id"] for p in listed}.isdisjoint({"TB", "MIN", "DET", "KC"})  # rostered elsewhere / mine
    for p in listed:
        assert p["mu"] == round(frozen[p["id"]]["mu"], 2), p["id"]  # the one shared consensus
    k_sadl = {p["id"]: p["mu"] for t in sadl["positions"]["K"]["tiers"] for p in t["players"]}
    k_crook = {p["id"]: p["mu"] for t in page["positions"]["K"]["tiers"] for p in t["players"]}
    assert k_sadl and all(k_crook[pid] == mu for pid, mu in k_sadl.items() if pid in k_crook)


def test_a_failing_second_league_never_touches_sadl(worlds):
    tmp, _one, _two, bad = worlds
    assert files(tmp / "bad") == files(tmp / "one")
    assert all(o.synced and o.leagues["crooked"]["status"] == "failed" for o in bad)
    assert "rosters" in bad[0].leagues["crooked"]["reason"]
    assert not (tmp / "bad" / "leagues" / "crooked" / "2026" / "week05").exists()


def test_the_site_gets_a_folder_and_a_switcher_per_league(worlds, tmp_path):
    tmp = worlds[0]
    one, two = tmp_path / "one", tmp_path / "two"
    build_site(tmp / "one", one, CFG_ONE)
    build_all(tmp / "two", two, CFG_TWO)
    nav = re.compile(r'\n  <nav class="leagues".*?</nav>', re.S)
    css = re.compile(r"\n\.leagues[^\n]*")
    for path in sorted(one.rglob("*")):
        if path.is_file():
            new = (two / path.relative_to(one)).read_bytes()
            if path.suffix == ".html":
                assert css.sub("", nav.sub("", new.decode())) == css.sub("", path.read_text()), path.name
            else:
                assert new == path.read_bytes(), path.name
    root, crook = (two / "index.html").read_text(), (two / "crooked" / "index.html").read_text()
    assert '<a href="crooked/index.html">Crooked</a>' in root and 'aria-current="page">SADL</a>' in root
    assert '<a href="../index.html">SADL</a>' in crook and 'aria-current="page">Crooked</a>' in crook
    assert "<h1>Crooked streamer</h1>" in crook and "<title>Crooked streamer</title>" in crook
    assert re.findall(r'id="tab-(\w+)"', crook) == ["K", "DEF"]
    assert "IDP" not in crook and "stats.html" not in crook and "SADL scoring" not in crook
    assert "Team defenses: no form" in crook and "Team-defense sources" in crook
    assert not (two / "crooked" / "stats.html").exists()
    # with no page for the second league, the SADL site has no switcher at all
    alone = tmp_path / "alone"
    build_all(tmp / "one", alone, CFG_TWO)
    assert (alone / "index.html").read_bytes() == (one / "index.html").read_bytes()
    assert not (alone / "crooked").exists()


def test_a_results_only_key_never_stops_the_second_league_and_is_scored_its_way(tmp_path):
    """The scoring check reads the week's projections only, so a kicker's forced fumble on a return (`idp_ff`:
    3 in SADL, 0 in Crooked) can't switch the league off mid-week; each league scores the result its own way."""
    from test_kickers_isolation import DAL_K

    crooked = {**CROOKED, "scoring_settings": {**CROOKED["scoring_settings"]}}
    sadl_ff = json.loads((FIXTURES / "sleeper_league.json").read_text())["scoring_settings"]["idp_ff"]
    assert sadl_ff and not crooked["scoring_settings"].get("idp_ff")
    line = {"gp": 1.0, "fgm_yds": 40.0, "xpm": 2.0, "idp_ff": 1.0}
    stats5 = {f"{V1}/stats/nfl/regular/2026/5": {DAL_K: line}}
    data, cache = tmp_path / "data", tmp_path / "cache"
    outs = [run_sync(CFG_TWO, data, cache, now, force=force, client=DefClient(def_routes(**stats5)), log=lambda m: None)
            for now, force in ((NOW, False), (TNF, True))]  # fmt: skip
    assert [o.leagues["crooked"]["status"] for o in outs] == ["rendered", "rendered"]
    page = json.loads((data / "leagues" / "crooked" / "2026" / "week05" / "consensus.json").read_text())
    sadl = json.loads((data / "2026" / "week05" / "consensus.json").read_text())

    def played(d):
        return {p["id"]: (p.get("result") or {}).get("pts") for p in d["positions"]["K"]["played"]}

    assert played(page)[DAL_K] == 6.0  # 0.1 · 40 yards + 2 extra points, no forced fumble in Crooked
    sadl_pts = played(sadl).get(DAL_K)  # (rostered in SADL: if listed, it is in SADL's points)
    assert sadl_pts in (None, 6.0 + sadl_ff)


def test_the_second_league_finds_its_user_through_the_first(tmp_path):
    data, cache = tmp_path / "data", tmp_path / "cache"
    run_sync(CFG_ONE, data, cache, NOW, client=DefClient(def_routes()), log=lambda m: None)  # SADL knows the user
    down = def_routes(**{f"{V1}/user/lbburnsy": 503})
    out = run_sync(CFG_TWO, data, cache, TNF, force=True, client=DefClient(down), log=lambda m: None)
    assert out.synced and out.leagues["crooked"]["status"] == "rendered"


def test_a_manual_backfill_reads_each_dst_page_once(tmp_path):
    from datetime import UTC, datetime

    from test_sync_site import FakeClient

    from sadlidp import backfill
    from sadlidp.sources import fp_def, fp_dst

    page = (FIXTURES / "fp_dst_week12_trimmed.html").read_text().replace('"week": "12"', '"week": "3"')
    client = FakeClient({"https://www.fantasypros.com/nfl/rankings/dst.php": page})
    players = {t: defense(t) for t in ("MIN", "JAX", "DEN", "TB")}
    cfg = {**CFG, "backfill": {"budget_s": 60, "max_requests": 40}}
    done = backfill.run({"fp_dst": (fp_dst, "signal"), "fp_def": (fp_def, "rank")}, cfg, tmp_path, 2026, [3],
                        datetime(2026, 11, 25, tzinfo=UTC), client=client, sleeper=None, players=players,
                        sched=SCHED)  # fmt: skip
    assert done == {"fp_dst": [3], "fp_def": [3]} and len(client.calls) == 1
