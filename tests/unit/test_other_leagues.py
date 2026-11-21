"""Your other leagues' kickers on the K tab (SPEC D25): the Crooked League read beside SADL, offline.

The world is test_kickers_isolation's (test_sync_site's fixtures plus kickers), with a second league added: you
roster HOU's kicker there, another team rosters NO's, and the roster is full. Checked here: the league's checks
(kicker scoring, found or not), the combined kicker list and its tags, each league's moves against its own free
kickers, the league's own files, the FAAB line, the page, and that no DL/LB/DB part of any file SADL's sync
writes changes, whether the league is read, unreachable, or scores kickers differently.
"""

import copy
import json
from datetime import datetime
from html import unescape
from pathlib import Path

import pytest
from test_kickers_isolation import FRIDAY, K_ROUTE, NOW, TNF, KClient, idp_view, k_routes, tree
from test_sync_site import CFG, ME

from sadlidp import league_tx, other_leagues
from sadlidp.schedule import ET
from sadlidp.site.build import build_site, this_week
from sadlidp.sleeper import V1
from sadlidp.sync import last_week_recap, run_sync

CID = "crooked-1"
MINE_C, THEIRS_C = "3451", "11653"  # my kicker in the Crooked League (HOU); another team's there (NO)
MINE_SADL, DAL_K = "11786", "11533"  # my SADL kicker (free in Crooked); a SADL-rostered DAL kicker (free in Crooked)
BOTH = {"5119", "6083", "4195"}  # free in both leagues
CROOKED_SCORING = {"fgm_0_19": 0.0, "fgm_20_29": 0.0, "fgm_30_39": 0.0, "fgm_40_49": 0.0, "fgm_50p": 0.0,
                   "fgm_yds": 0.1, "fgmiss": -1.0, "xpm": 1.0, "xpmiss": -1.0, "rec": 1.0}  # fmt: skip
CFG_SADL_ONLY = {**CFG, "kicker_leagues": {}}
WEAK = {"gp": 1.0, "fga": 0.6, "fgm": 0.4, "fgm_yds": 14.0, "xpa": 0.6, "xpm": 0.5, "xpmiss": 0.1}


def c_league(**over) -> dict:
    return {"league_id": CID, "name": "Kusch's Crooked League", "season": "2026", "status": "in_season",
            "roster_positions": ["QB", "K"], "settings": {"waiver_type": 2, "waiver_budget": 1000},
            "scoring_settings": CROOKED_SCORING, **over}  # fmt: skip


def world(crooked: dict | None = None) -> dict:
    """k_routes plus the Crooked League (unless `crooked` is {}: then it is unreachable). My Crooked kicker is
    projected for a poor game, so the free kickers there are START candidates; my roster there is full."""
    r = k_routes()
    r[K_ROUTE] = [{**x, "stats": WEAK} if x["player_id"] == MINE_C else x for x in r[K_ROUTE]]
    leagues = r[f"{V1}/user/{ME}/leagues/nfl/2026"]
    if crooked == {}:
        return r
    lg = c_league(**(crooked or {}))
    r[f"{V1}/user/{ME}/leagues/nfl/2026"] = [*leagues, {"league_id": CID, "name": lg["name"]}]
    r[f"{V1}/league/{CID}"] = lg
    r[f"{V1}/league/{CID}/rosters"] = [
        {"roster_id": 1, "owner_id": ME, "players": ["qb1", MINE_C], "reserve": None, "taxi": None},
        {"roster_id": 2, "owner_id": "someone", "players": [THEIRS_C], "reserve": None, "taxi": None},
    ]
    r[f"{V1}/league/{CID}/matchups/5"] = [{"roster_id": 1, "starters": ["qb1", MINE_C]}]
    r[f"{V1}/league/{CID}/transactions/5"] = []
    r[f"{V1}/league/{CID}/transactions/4"] = []
    return r


def _run(data: Path, cache: Path, routes: dict, cfg: dict = CFG) -> None:
    run_sync(cfg, data, cache, NOW, client=KClient(routes), log=lambda m: None)  # Tuesday: the pre-waiver sync
    run_sync(cfg, data, cache, TNF, force=True, client=KClient(routes), log=lambda m: None)  # DAL's game started
    run_sync(cfg, data, cache, FRIDAY, force=True, client=KClient(routes), log=lambda m: None)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("crooked")
    _run(tmp / "base", tmp / "cb", world({}), CFG_SADL_ONLY)  # SADL alone, as on main
    _run(tmp / "both", tmp / "cc", world())
    _run(tmp / "gone", tmp / "cg", world({}))  # configured, but no league of yours has that name
    _run(tmp / "diff", tmp / "cd", world({"scoring_settings": {**CROOKED_SCORING, "fgm_50p": 1.0}}))
    kickers = {pid for pid, p in world()[f"{V1}/players/nfl"].items() if p.get("position") == "K"}
    return tmp, kickers


def _week(data: Path, name: str):
    return json.loads((data / "2026" / "week05" / name).read_text(encoding="utf-8"))


def test_kicker_scoring_check():
    sadl = {"fgm_0_19": 0.0, "fgm_50_59": 0.0, "fgm_60p": 0.0, "fgm_yds": 0.1, "fgmiss": -1.0, "xpm": 1.0,
            "xpmiss": -1.0, "idp_sack": 6.0}  # fmt: skip
    assert other_leagues.same_kicker_scoring(CROOKED_SCORING, sadl)  # zero buckets listed differently: alike
    assert not other_leagues.same_kicker_scoring({**CROOKED_SCORING, "fgm_50p": 1.0}, sadl)  # a bucket bonus
    assert not other_leagues.same_kicker_scoring({**CROOKED_SCORING, "fgmiss": -2.0}, sadl)
    assert not other_leagues.same_kicker_scoring({**CROOKED_SCORING, "fgm_yds": 0.04}, sadl)


def test_the_league_is_read_and_its_kickers_join_the_list(runs):
    tmp, _ = runs
    d = _week(tmp / "both", "consensus.json")
    o = d["k_leagues"]["crooked"]
    assert (o["status"], o["label"], o["name"], o["note"], o["waiver_type"]) == (
        "ok", "Crooked", "Kusch's Crooked League", None, "FAAB")  # fmt: skip
    mine = o["my"]
    assert [p["id"] for p in mine["players"]] == [MINE_C] and mine["players"][0]["role"] == "starter"
    assert mine["count"] == 1 and mine["open_spots"] == 0 and mine["limit"] is None
    # the combined list: every kicker free in either league, tagged with where he is free
    k = d["positions"]["K"]
    cards = {p["id"]: p for t in k["tiers"] for p in t["players"]}
    tags = {pid: [lg["label"] for lg in p["leagues"]] for pid, p in cards.items()}
    assert tags[THEIRS_C] == ["SADL"] and tags[MINE_SADL] == ["Crooked"]
    assert all(tags[pid] == ["SADL", "Crooked"] for pid in BOTH if pid in tags) and BOTH & tags.keys()
    assert MINE_C not in cards  # yours in Crooked, another team's in SADL: free nowhere
    # the SADL list is the same kickers it was, in the same order
    base = _week(tmp / "base", "consensus.json")["positions"]["K"]
    base_ids = [p["id"] for t in base["tiers"] for p in t["players"]]
    assert [pid for t in k["tiers"] for pid in (p["id"] for p in t["players"]) if "SADL" in tags[pid]] == base_ids


def test_each_league_moves_only_on_its_own_free_kickers(runs):
    tmp, _ = runs
    d = _week(tmp / "both", "consensus.json")
    cards = {p["id"]: p for t in d["positions"]["K"]["tiers"] for p in t["players"]}
    crooked = d["k_leagues"]["crooked"]["my"]["suggestions"]
    assert crooked, "a weak Crooked kicker: the free kickers there should be START candidates"
    for s in crooked:
        assert "Crooked" in [lg["label"] for lg in cards[s["player"]["id"]]["leagues"]]
        assert (s["badge"], s["over"], s["action"], s["drop"]) == ("START", MINE_C, "swap", MINE_C)  # full roster
    for s in d["my_idps"]["K"]["suggestions"]:  # SADL's, as before: SADL's free kickers only
        assert "SADL" in [lg["label"] for lg in cards[s["player"]["id"]]["leagues"]]
    assert d["my_idps"]["K"] == _week(tmp / "base", "consensus.json")["my_idps"]["K"] | {
        "players": d["my_idps"]["K"]["players"]
    }  # SADL's moves untouched; only the tier placement may differ
    # a card carries each league's badge for him: SADL's is the card's own `badge`, as it always was
    for p in cards.values():
        sadl = next((lg for lg in p["leagues"] if lg["label"] == "SADL"), None)
        assert (sadl or {}).get("badge") == (p["badge"] if sadl else None)


def test_the_league_keeps_its_own_files(runs):
    tmp, _ = runs
    season = tmp / "both" / "2026"
    lg = json.loads((season / "crooked" / "league.json").read_text())
    assert lg["league_id"] == CID and lg["scoring_settings"] == CROOKED_SCORING
    rosters = json.loads((season / "week05" / "crooked" / "rosters.json").read_text())
    assert rosters[0] == {"roster_id": 1, "owner_id": ME, "co_owners": None, "players": ["qb1", MINE_C],
                          "reserve": None, "taxi": None}  # fmt: skip
    badges = json.loads((season / "week05" / "crooked" / "badges.json").read_text())["badges"]
    assert badges and all(b["slot"] == "K" and b["over"] == MINE_C for b in badges.values())
    assert (season / "week05" / "crooked" / "transactions.json").exists()
    run = _week(tmp / "both", "run.json")
    assert run["kicker_leagues"]["crooked"]["status"] == "ok"
    tlog = _week(tmp / "both", "tiers_log.json")["prewaiver"]["players"]
    assert all("free_in" in r for r in tlog.values() if r["slot"] == "K")  # the combined list, as shown
    assert not any("free_in" in r for r in tlog.values() if r["slot"] != "K")


def _only_new_files(base: dict, new: dict) -> None:
    extra = set(new) - set(base)
    assert set(base) <= set(new) and all("/crooked/" in p or p.startswith("2026/crooked/") for p in extra), extra


@pytest.mark.parametrize("case", ["both", "gone", "diff"])
def test_every_dl_lb_db_part_is_identical_to_sadl_alone(runs, case):
    tmp, kickers = runs
    b, n = tree(tmp / "base"), tree(tmp / case)
    _only_new_files(b, n)
    for rel, doc in b.items():
        name = rel.split("/", 2)[2] if rel.count("/") >= 2 else rel.split("/", 1)[1]
        view_b, view_n = idp_view(name, doc, kickers), idp_view(name, n[rel], kickers)
        if name in ("consensus.json", "run.json"):
            view_n = {k: x for k, x in view_n.items() if k not in ("k_leagues", "kicker_leagues")}
        assert view_b == view_n, rel
    if case != "both":  # not shown: every file is SADL's alone, kickers included, apart from the league's note
        for rel, doc in b.items():
            new = n[rel]
            if rel.endswith(("consensus.json", "run.json")):
                new = {k: x for k, x in new.items() if k not in ("k_leagues", "kicker_leagues")}
            assert doc == new, rel


def test_a_league_not_shown_says_why(runs):
    tmp, _ = runs
    gone = _week(tmp / "gone", "consensus.json")["k_leagues"]["crooked"]
    assert gone["status"] == "not_found" and "my" not in gone and "Kusch's Crooked League" in gone["note"]
    diff = _week(tmp / "diff", "consensus.json")["k_leagues"]["crooked"]
    assert diff["status"] == "scoring_differs" and "my" not in diff
    assert not (tmp / "diff" / "2026" / "week05" / "crooked" / "rosters.json").exists()  # never read further
    assert all("leagues" not in p for t in _week(tmp / "gone", "consensus.json")["positions"]["K"]["tiers"]
               for p in t["players"])  # fmt: skip


def test_read_never_raises():
    class Boom:
        def user(self, *a):
            return {"user_id": ME}

        def leagues(self, *a):
            raise RuntimeError("network down")

    o = other_leagues.read(Boom(), "crooked", {"names": ["x"]}, 2026, 5, ME, CROOKED_SCORING, "nowhere",
                           datetime(2026, 10, 6, tzinfo=ET), log=lambda m: None)  # fmt: skip
    assert (o.status, o.note) == ("failed", "couldn't be read this sync (RuntimeError)") and not o.ok


def test_kicker_faab_line_counts_kicker_claims_in_its_own_folder(tmp_path):
    week = tmp_path / "2026" / "week05"
    txs = [{"type": "waiver", "status": "complete", "leg": 5, "adds": {f"k{i}": 1}, "bid": bid, "created": 1}
           for i, bid in enumerate((3, 59, 43, 0, 21))]  # fmt: skip
    txs += [{"type": "waiver", "status": "failed", "leg": 5, "adds": {"k1": 1}, "bid": 5},
            {"type": "waiver", "status": "complete", "leg": 5, "adds": {"idp1": 1}, "bid": 1}]  # fmt: skip
    (week / "crooked").mkdir(parents=True)
    (week / "crooked" / "transactions.json").write_text(json.dumps({"transactions": txs}))
    slots = {**{f"k{i}": "K" for i in range(5)}, "idp1": "LB"}
    settings = {"waiver_type": 2, "waiver_budget": 1000}
    f = league_tx.faab_summary(tmp_path, 2026, slots, settings, count=("K",), sub="crooked")
    assert (f["claims"], f["won_token"], f["contested"], f["contested_label"], f["token_label"]) == (
        5,
        2,
        1,
        "5.9",
        "1",
    )  # fmt: skip (a token bid: $10 or less of $1,000)
    assert league_tx.faab_summary(tmp_path, 2026, slots, settings) is None  # SADL's folder: nothing there


def test_last_week_recap_reads_the_leagues_own_log(tmp_path):
    from test_sync_site import SCHED

    wd = tmp_path / "2026" / "week04"
    (wd / "crooked").mkdir(parents=True)
    badge = {"slot": "K", "w": "k1", "badge": "START", "over": "k2", "p": 0.7, "reference": False, "why": [],
             "mu_w": 9.0, "mu_over": 7.0, "active": True}  # fmt: skip
    (wd / "crooked" / "badges.json").write_text(json.dumps({"badges": {"k1|START|k2": badge}}))
    (wd / "actuals.json").write_text(json.dumps({"captured_at": "2026-10-03T00:00:00-04:00", "stats": {
        "k1": {"gp": 1, "fgm_yds": 90.0, "xpm": 2}, "k2": {"gp": 1, "fgm_yds": 40.0, "xpm": 1}}}))  # fmt: skip
    sc = {"fgm_yds": 0.1, "xpm": 1.0}
    players = {"k1": {"full_name": "One", "position": "K"}, "k2": {"full_name": "Two", "position": "K"}}
    rec = last_week_recap(tmp_path, 2026, 5, SCHED, sc, players, "crooked")
    assert rec["summary_k"] == {"hits": 1, "decided": 1} and rec["summary"] == {"hits": 0, "decided": 0}
    assert [x["outcome"] for x in rec["slots"]["K"]] == ["hit"]
    assert last_week_recap(tmp_path, 2026, 5, SCHED, sc, players) is None  # SADL's folder has no log


def test_this_week_card_names_the_league():
    def block(sugg):
        return {"players": [{"name": "Santos", "role": "starter", "unavailable": [], "flags": []}],
                "suggestions": sugg, "byes": []}  # fmt: skip

    s = {"badge": "START", "over": "x", "reference": False}
    data = {"my_idps": {"DL": block([]), "K": block([])},
            "k_leagues": {"crooked": {"label": "Crooked", "my": block([s])}}}  # fmt: skip
    now = this_week(data, ("DL", "K"))
    assert [(x["slot"], x.get("lg")) for x in now["moves"]] == [("K", "Crooked")]
    assert now["quiet"] == ["DL", "K (SADL)"]
    assert [x["slot"] for x in now["starters"]] == ["DL", "K", "K (Crooked)"]
    data["k_leagues"]["crooked"]["my"] = block([])
    assert this_week(data, ("DL", "K"))["quiet"] == ["DL", "K"]  # no league has a kicker move
    data["my_idps"]["K"] = block([s])
    assert this_week(data, ("DL", "K"))["quiet"] == ["DL", "K (Crooked)"]
    data["k_leagues"]["crooked"] = {"label": "Crooked", "name": "Kusch's Crooked League", "note": "league not found"}
    heads = this_week(data, ("DL", "K"))["heads"]
    assert "Kusch's Crooked League: league not found; the kicker list is SADL's alone." in heads


def test_the_page_tags_the_kickers_and_shows_the_league(runs, tmp_path):
    tmp, _ = runs
    data = tmp_path / "data"
    import shutil

    shutil.copytree(tmp / "both", data)
    txs = [{"type": "waiver", "status": "complete", "leg": 5, "adds": {pid: 1}, "bid": bid, "created": 1}
           for pid, bid in zip((*BOTH, MINE_SADL, DAL_K), (3, 59, 43, 0, 21), strict=True)]  # fmt: skip
    (data / "2026" / "week05" / "crooked" / "transactions.json").write_text(json.dumps({"transactions": txs}))
    html = unescape(build_site(data, tmp_path / "site", CFG).read_text(encoding="utf-8"))  # Kusch&#39;s
    panel = html[html.index('id="panel-K"') :]
    assert '<span class="tag lg">SADL</span>' in panel and '<span class="tag lg">Crooked</span>' in panel
    assert "START · Crooked" in panel and "K · Crooked" in html  # the panel's chip; the This-week card's tag
    assert "Tags: the leagues he's free in." in panel
    assert ("Kusch's Crooked League, kicker waivers this season: 2 of 5 claims were won with a token bid (at most "
            "1% of the starting budget). <small>(Starting budget $1,000.)</small>") in html  # fmt: skip
    assert "Other leagues: the K tab also covers Kusch's Crooked League" in html
    for slot in ("DL", "LB", "DB"):  # no tag on an IDP panel
        start = html.index(f'id="panel-{slot}"')
        assert "tag lg" not in html[start : html.index('class="tabpanel"', start + 10)]


def test_a_replay_never_reads_another_league(runs, tmp_path):
    from test_sync_site import FIXTURES, routes

    from sadlidp.replay import replay_week

    tmp, _ = runs
    data = tmp_path / "data"
    import shutil

    shutil.copytree(tmp / "both", data)
    week4 = data / "2026" / "week04" / "sources"
    week4.mkdir(parents=True, exist_ok=True)
    shutil.copy(data / "2026" / "week05" / "sources" / "sleeper_proj.json", week4 / "sleeper_proj.json")
    truth = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    out = replay_week(CFG, data, 2026, 4, truth["matchups"], copy.deepcopy(routes()[f"{V1}/players/nfl"]),
                      log=lambda m: None)  # fmt: skip
    assert "k_leagues" not in json.loads(out.read_text())
