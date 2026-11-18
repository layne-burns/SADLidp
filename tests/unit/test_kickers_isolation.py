"""IDP isolation, end to end and offline: the same syncs run twice over the existing fixtures, once as they
are (no kicker data anywhere) and once with kicker players, kicker projections and kicker results added. Every
DL/LB/DB part of every file the sync writes must come out identical; the second run must also carry the K data
the page and the Stats page build on."""

import copy
import json
from pathlib import Path

import pytest
from test_sync_site import CFG, FIXTURES, FRIDAY, LID, NOW, ROSTERS, SCHED, TNF, FakeClient, routes

from sadlidp import accuracy
from sadlidp.net import Response
from sadlidp.replay import replay_week
from sadlidp.sleeper import API, V1
from sadlidp.sources import registry
from sadlidp.sync import run_sync

IDP = ("DL", "LB", "DB")
K_NAMES = registry.names_for("k")
K_PROJ = json.loads((FIXTURES / "sleeper_k_projections_week11_trimmed.json").read_text(encoding="utf-8"))
K_ACTUAL = json.loads((FIXTURES / "sleeper_k_stats_week10_trimmed.json").read_text(encoding="utf-8"))["players"]
K_ROUTE = f"{API}/projections/nfl/2026/5#K"
MINE, DAL_K, HOU_K = "11786", "11533", "3451"  # my kicker (JAX); a rostered DAL kicker (Thursday game); another team's


class KClient(FakeClient):
    """FakeClient that tells the kicker request apart (the real one differs only in its query string): it is
    served from the route ending "#K", so the IDP request gets exactly what it always got."""

    def get(self, url, **kw):
        if "position[]=K" not in url:
            return super().get(url, **kw)
        self.calls.append(url)
        body = self.routes.get(url.split("?")[0] + "#K", 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        return Response(url=url, final_url=url, status=200, body=json.dumps(body).encode())


def k_routes() -> dict:
    """test_sync_site's world plus kickers: four unrostered ones and a punter in the players DB, week-5 kicker
    projections (the trimmed real rows, relabelled) and kicker lines in last week's results."""
    r = routes()
    players = r[f"{V1}/players/nfl"]
    rows = []
    for row in K_PROJ:
        pid, p = row["player_id"], row["player"]
        players.setdefault(pid, {"full_name": f"{p['first_name']} {p['last_name']}", "position": p["position"],
                                 "fantasy_positions": p["fantasy_positions"], "team": row["team"],
                                 "status": "Active", "active": True, "injury_status": None})  # fmt: skip
        opp = SCHED.opponent(players[pid].get("team"), 5)
        rows.append({**row, "week": 5, "opponent": opp.lstrip("@") if opp and row["opponent"] else None})
    # my kicker is projected for a poor game (4.0 points), so the waiver kickers are likely upgrades on him
    weak = {"gp": 1.0, "fga": 1.0, "fgm": 0.8, "fgm_yds": 28.0, "xpa": 1.6, "xpm": 1.5, "xpmiss": 0.1}
    rows = [{**x, "stats": weak} if x["player_id"] == MINE else x for x in rows]
    thursday = copy.deepcopy(next(x for x in rows if x["player_id"] == HOU_K))  # a kicker in the Thursday game
    thursday.update(player_id=DAL_K, team="DAL", opponent="TB")
    r[K_ROUTE] = [*rows, thursday]
    r[f"{V1}/stats/nfl/regular/2026/4"] = {
        **r[f"{V1}/stats/nfl/regular/2026/4"],
        **{pid: p["stats"] for pid, p in K_ACTUAL.items()},
        "TEAM_JAX": {"fgm_yds": 170.0, "xpm": 4.0, "gp": 1.0},
    }
    return r


def _warnings(ws: list[str]) -> list[str]:
    return [w for w in ws if not w.startswith(tuple(f"{n}:" for n in K_NAMES)) and "kicker" not in w.lower()]


def idp_view(name: str, doc, kickers: set[str]):
    """The DL/LB/DB part of one file a sync writes (`name` relative to the week or season folder): the file
    itself with every kicker player, K slot, kicker source and kicker-only key taken out. Two runs agree on
    DL/LB/DB exactly when their views are equal; anything else that differs is a failure."""
    if name.startswith("sources/"):
        return doc
    if not isinstance(doc, dict):
        return doc
    v = dict(doc)

    def slots(d):
        return {s: x for s, x in (d or {}).items() if s != "K"}

    def no_k(d):
        return {n: x for n, x in (d or {}).items() if n not in K_NAMES}

    if name == "consensus.json":
        v.pop("kicker", None)
        v.update(positions=slots(v["positions"]), my_idps=slots(v["my_idps"]), sigma_out=slots(v["sigma_out"]),
                 sources=no_k(v["sources"]), warnings=_warnings(v["warnings"]))  # fmt: skip
        if v.get("swing"):
            v["swing"] = {**v["swing"], "a": slots(v["swing"]["a"]), "b": slots(v["swing"]["b"])}
        if v.get("last_week"):
            lw = {k: x for k, x in v["last_week"].items() if k != "summary_k"}
            v["last_week"] = {**lw, "slots": slots(lw["slots"])}
    elif name == "run.json":
        v.pop("kicker_actuals_added", None)
        v.update(sources=no_k(v["sources"]), warnings=_warnings(v["warnings"]), backfilled=no_k(v["backfilled"]),
                 weights={k: x for k, x in v["weights"].items() if k != accuracy.K_KEY})  # fmt: skip
        if v.get("swing"):
            sw = {k: x for k, x in v["swing"].items() if k != "kickers"}
            v["swing"] = {**sw, "a": slots(sw["a"]), "b": slots(sw["b"]), "mean": slots(sw["mean"])}
    elif name in ("frozen.json", "players.json"):
        v["players"] = {pid: x for pid, x in v["players"].items() if pid not in kickers}
    elif name == "actuals.json":
        v = {k: x for k, x in v.items() if k not in ("k_added_at", "k_added")}
        v["stats"] = {pid: x for pid, x in v["stats"].items() if pid not in kickers}
    elif name == "tiers_log.json":
        v["last_pre_kickoff"] = {pid: x for pid, x in v["last_pre_kickoff"].items() if x.get("slot") != "K"}
        if v.get("prewaiver"):
            pw = v["prewaiver"]
            v["prewaiver"] = {**pw, "players": {pid: x for pid, x in pw["players"].items() if x.get("slot") != "K"}}
    elif name == "badges.json":
        v["badges"] = {key: x for key, x in v["badges"].items() if x.get("slot") != "K"}
    elif name == "weights.json":
        v.pop(accuracy.K_KEY, None)
    return v


def tree(data: Path) -> dict[str, object]:
    """Every JSON file under a data dir, keyed by its path below the season folder."""
    out = {}
    for path in sorted(data.rglob("*.json")):
        rel = path.relative_to(data).as_posix()
        out[rel] = json.loads(path.read_text())
    return out


def assert_idp_identical(base: dict, new: dict, kickers: set[str]) -> None:
    extra = set(new) - set(base)
    assert set(base) <= set(new) and all(Path(p).stem in K_NAMES and "/sources/" in p for p in extra), extra
    for rel, doc in base.items():
        name = rel.split("/", 2)[2] if rel.count("/") >= 2 else rel.split("/", 1)[1]
        assert idp_view(name, doc, kickers) == idp_view(name, new[rel], kickers), rel


def _run(data: Path, cache: Path, client_of) -> None:
    run_sync(CFG, data, cache, NOW, client=client_of(), log=lambda m: None)  # Tuesday, gated: the pre-waiver sync
    run_sync(CFG, data, cache, TNF, force=True, client=client_of(), log=lambda m: None)  # TB @ DAL has kicked off
    run_sync(CFG, data, cache, FRIDAY, force=True, client=client_of(), log=lambda m: None)  # and is final


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("isolation")
    _run(tmp / "base", tmp / "cb", lambda: FakeClient(routes()))
    _run(tmp / "kick", tmp / "ck", lambda: KClient(k_routes()))
    kickers = {pid for pid, p in k_routes()[f"{V1}/players/nfl"].items() if p.get("position") == "K"}
    return tmp / "base", tmp / "kick", kickers


def test_every_dl_lb_db_part_is_identical_with_and_without_kickers(runs):
    base, kick, kickers = runs
    b, k = tree(base), tree(kick)
    assert_idp_identical(b, k, kickers)
    week = "2026/week05/"
    for name in ("consensus.json", "frozen.json", "tiers_log.json", "badges.json", "weights.json", "run.json",
                 "players.json", "sources/sleeper_proj.json"):  # fmt: skip
        assert week + name in b, name
    assert b[week + "sources/sleeper_proj.json"] == k[week + "sources/sleeper_proj.json"]  # the IDP file, whole
    for name in ("espn_proj", "fp_ecr", "rotoballer", "fantasydata"):  # offline here: never written in either run
        assert (week + f"sources/{name}.json" in b) == (week + f"sources/{name}.json" in k)
    w_base, w_kick = b[week + "weights.json"], k[week + "weights.json"]
    idp_keys = {"alpha", "alpha_hat", "alpha_n", "computed_at", "from_weeks", "k", "method", "prior_split", "priors",
                "reason", "schema_version", "sources", "week"}  # fmt: skip
    assert set(w_base) == set(w_kick) == idp_keys | {"kickers"}  # one new key, beside the IDP record
    assert {key: w_kick[key] for key in idp_keys} == {key: w_base[key] for key in idp_keys}  # every IDP key, untouched
    assert w_kick["k"] == 4.0 and w_kick["priors"] == {"fp_ecr": 2.0, "rotoballer": 1.0, "fantasydata": 1.0}
    # the view is not vacuous: the kicker run really does hold kicker data in the files compared above
    assert (
        k[week + "consensus.json"]["positions"]["K"]["tiers"]
        and b[week + "consensus.json"] != k[week + "consensus.json"]
    )
    assert (
        kickers & k[week + "frozen.json"]["players"].keys()
        and not b[week + "consensus.json"]["positions"]["K"]["tiers"]
    )


def test_without_kicker_data_the_page_has_an_empty_k_block_and_says_why(runs):
    base, _, _ = runs
    week = base / "2026" / "week05"
    d = json.loads((week / "consensus.json").read_text())
    assert list(d["positions"]) == ["DB", "DL", "K", "LB"] and d["positions"]["K"]["tier_count"] == 0
    assert d["my_idps"]["K"]["count"] == 1 and d["my_idps"]["K"]["suggestions"] == []
    run = json.loads((week / "run.json").read_text())
    k = run["sources"]["sleeper_k"]  # the fixture's projection route serves the IDP payload to any query
    assert (k["status"], k["error"], k["group"], k["label"]) == (
        "suspect",
        "no kicker rows in the response",
        "k",
        "Sleeper",
    )
    assert "group" not in run["sources"]["sleeper_proj"]  # the IDP entries carry no new key
    assert any(w.startswith("no kicker source has usable rows") for w in d["warnings"])
    assert not (week / "sources" / "sleeper_k.json").exists()


def test_the_kicker_run_carries_the_k_data_the_page_builds_on(runs):
    _, kick, kickers = runs
    week = kick / "2026" / "week05"
    d = json.loads((week / "consensus.json").read_text())
    assert d["league"]["limits"] == {"DL": 2, "LB": 2, "DB": 2}  # no K limit, and no warning about it
    assert not [w for w in d["warnings"] if "position limit" in w]
    assert d["sigma_out"]["K"] == 5.0 and d["kicker"] == {"sigma_out": 5.0, "one_point_edge": 0.556}
    src = d["sources"]["sleeper_k"]
    assert (src["label"], src["group"], src["kind"]) == ("Sleeper", "k", "projection") and src["rows_used"] >= 6
    pos = d["positions"]["K"]
    cards = [p for t in pos["tiers"] for p in t["players"]]
    assert {p["id"] for p in cards} == {"11653", "5119", "6083", "4195"}  # the unrostered kickers with a projection
    assert all(p["slot"] == "K" and p["also"] == [] and p["id"] in kickers for p in cards)
    assert [p["mu"] for p in cards] == sorted((p["mu"] for p in cards), reverse=True)
    assert set(pos) == set(d["positions"]["LB"]) and pos["tier_count"] >= 1
    smyth = next(p for p in cards if p["id"] == "11653")
    line = next(r for r in K_PROJ if r["player_id"] == "11653")["stats"]
    assert smyth["mu"] == round(0.1 * line["fgm_yds"] - (line["fga"] - line["fgm"]) + line["xpm"] - line["xpmiss"], 2)
    part = smyth["breakdown"]["parts"][0]
    assert (part["source"], part["kind"]) == ("Sleeper", "projection") and "form" not in smyth["breakdown"]
    assert [s[0] for s in part["stats"]] == ["FG yards", "XP", "missed FG", "missed XP"][: len(part["stats"])]
    assert ["missed FG", round(line["fga"] - line["fgm"], 2), True] in part["stats"]  # derived: starred on the page
    assert len(smyth["breakdown"]["parts"]) == len([n for n in registry.K_SOURCES if n in d["sources"]])
    mine = d["my_idps"]["K"]
    assert set(mine) == set(d["my_idps"]["LB"]) | {"open_spots"}
    assert (mine["count"], mine["limit"], mine["open_spots"]) == (1, None, 2) and "open_spots" not in d["my_idps"]["LB"]
    assert [(p["id"], p["name"], p["role"]) for p in mine["players"]] == [(MINE, "Cam Little", "starter")]
    assert mine["players"][0]["mu"] == 4.0 and mine["suggestions"]  # 0.1·28 − 0.2 + 1.5 − 0.1
    for s in mine["suggestions"]:  # two free roster spots: an add never needs a drop
        assert (s["badge"], s["action"], s["drop"], s["over"]) == ("START", "add", None, MINE)
        assert (
            s["player"]["slot"] == "K" and s["reference"] is False and 0.6 <= s["p"] < 0.8
        )  # σ_out,K = 5: modest odds
    issued = [e for e in json.loads((week / "badges.json").read_text())["badges"].values() if e["slot"] == "K"]
    assert issued and all(e["sigma_out"] == 5.0 and e["action"] == "add" and e["mu_over"] == 4.0 for e in issued)
    frozen = json.loads((week / "frozen.json").read_text())["players"]
    assert DAL_K in frozen and frozen[DAL_K]["parts"].keys() == {"sleeper_k"} and frozen[DAL_K]["kickoff"]
    log = json.loads((week / "tiers_log.json").read_text())
    assert {r["slot"] for r in log["prewaiver"]["players"].values()} == {"DL", "LB", "DB", "K"}
    run = json.loads((week / "run.json").read_text())
    krec = run["weights"]["kickers"]
    assert krec["method"] == "prior" and krec["alpha"] == 0.6 and krec["priors"] == {
        "fp_k": 2.0, "rotoballer_k": 1.0, "fantasydata_k": 1.0,
    }  # fmt: skip
    assert json.loads((week / "weights.json").read_text())["kickers"] == krec
    assert run["swing"]["mean"].keys() == {"DL", "LB", "DB", "K"} and set(run["swing"]["kickers"]) == {
        "players",
        "no_var",
    }
    assert set(run["form"]["miss"]) == {"DL", "LB", "DB"}
    players = json.loads((week / "players.json").read_text())["players"]
    assert {MINE, "11653", DAL_K} <= players.keys() and "2650" not in players  # kickers saved; the punter isn't tracked
    actuals = json.loads((kick / "2026" / "week04" / "actuals.json").read_text())["stats"]
    assert {MINE, DAL_K} <= actuals.keys() and "TEAM_JAX" not in actuals  # by position, never by stat key
    assert "4233" not in actuals  # a kicker line for someone the players DB doesn't know stays out, like any other
    saved = json.loads((week / "sources" / "sleeper_k.json").read_text())
    assert saved["universe"] == {"K": {"complete": True, "depth": None}} and "2650" not in saved["no_projection_ids"]
    assert saved["rows"]["11653"]["imputed"] == ["fgmiss"] and "5838" in saved["no_projection_ids"]


def test_a_full_roster_turns_the_kicker_add_into_a_swap(tmp_path):
    """No free roster spot and one kicker: the move is a straight swap. The IDP suggestions don't change."""
    r = k_routes()
    full = {**ROSTERS[0], "reserve": [], "taxi": []}  # everyone active: 32 players for 27 spots
    r[f"{V1}/league/{LID}/rosters"] = [full, *ROSTERS[1:]]
    run_sync(CFG, tmp_path / "full", tmp_path / "c1", NOW, client=KClient(r), log=lambda m: None)
    run_sync(CFG, tmp_path / "room", tmp_path / "c2", NOW, client=KClient(k_routes()), log=lambda m: None)
    d = json.loads((tmp_path / "full" / "2026" / "week05" / "consensus.json").read_text())
    room = json.loads((tmp_path / "room" / "2026" / "week05" / "consensus.json").read_text())
    k = d["my_idps"]["K"]
    assert (k["open_spots"], k["limit"], k["count"]) == (0, None, 1) and k["suggestions"]
    assert all((s["action"], s["drop"], s["over"]) == ("swap", MINE, MINE) for s in k["suggestions"])
    assert k["names"][MINE] == "Cam Little"
    moves = [(s["player"]["id"], s["badge"], s["p"]) for s in k["suggestions"]]
    assert moves == [(s["player"]["id"], s["badge"], s["p"]) for s in room["my_idps"]["K"]["suggestions"]]
    for slot in IDP:  # roster room is a kicker matter: no IDP action reads it
        assert [(s["player"]["id"], s["action"], s["drop"]) for s in d["my_idps"][slot]["suggestions"]] == [
            (s["player"]["id"], s["action"], s["drop"]) for s in room["my_idps"][slot]["suggestions"]
        ]


def test_a_replay_of_a_week_saved_before_kickers_has_no_k(runs, tmp_path):
    """Old weeks' pages never gain a K block: with no kicker source file for the week, the group is off."""
    import shutil

    base, _, _ = runs
    data = tmp_path / "data"
    shutil.copytree(base, data)
    src = data / "2026" / "week05" / "sources"
    week4 = data / "2026" / "week04" / "sources"
    week4.mkdir(parents=True)
    shutil.copy(src / "sleeper_proj.json", week4 / "sleeper_proj.json")
    truth = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    out = replay_week(CFG, data, 2026, 4, truth["matchups"], routes()[f"{V1}/players/nfl"], log=lambda m: None)
    d = json.loads(out.read_text())
    assert list(d["positions"]) == ["DB", "DL", "LB"] and list(d["my_idps"]) == ["DB", "DL", "LB"]
    assert "kicker" not in d and not [n for n in d["sources"] if n in K_NAMES]
    assert not [w for w in d["warnings"] if "kicker" in w]


def test_a_replay_never_builds_kickers_from_backfilled_rows_alone(runs, tmp_path):
    """A past week gains kicker source files when the archives are backfilled (for the Stats page). Its page
    never had kickers, so its replay has none and no kicker record is written; a week that read its kicker
    rows live is replayed with them. The DL/LB/DB part of the replay is the same either way."""
    import shutil

    _, kick, kickers = runs
    truth = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    players = k_routes()[f"{V1}/players/nfl"]
    live = json.loads((kick / "2026" / "week05" / "sources" / "sleeper_k.json").read_text())
    archived = {**live, "rows": {pid: {**row, "backfill": True} for pid, row in live["rows"].items()}}
    pages = {}
    for label, doc in (("none", None), ("archived", archived), ("live", live)):
        data = tmp_path / label
        shutil.copytree(kick, data)
        week4 = data / "2026" / "week04" / "sources"
        week4.mkdir(parents=True)
        shutil.copy(data / "2026" / "week05" / "sources" / "sleeper_proj.json", week4 / "sleeper_proj.json")
        if doc is not None:
            (week4 / "sleeper_k.json").write_text(json.dumps(doc), encoding="utf-8")
        out = replay_week(CFG, data, 2026, 4, truth["matchups"], players, log=lambda m: None)
        pages[label] = json.loads(out.read_text())
        frozen = json.loads((data / "2026" / "week04" / "frozen.json").read_text())["players"]
        assert bool(kickers & frozen.keys()) == (label == "live")  # no kicker record from archive rows
    assert pages["archived"] == pages["none"] and list(pages["none"]["positions"]) == ["DB", "DL", "LB"]
    assert pages["live"]["positions"]["K"]["tiers"] and "sleeper_k" in pages["live"]["sources"]
    assert idp_view("consensus.json", pages["live"], kickers) == idp_view("consensus.json", pages["none"], kickers)
