"""All six kicker sources in one sync, end to end and offline (SPEC §5.2, §6.4, §6.5; kickers added 2026-11-17).

test_kickers_isolation's world (week 5 of the fixture league, Sleeper's kicker projections) plus the five other
kicker sources, each served from its trimmed real week-11 fixture relabelled as week 5. A kicker's number is
then a blend of three projections and three rank lists, scored with the league's decimal kicking settings, and
no DL/LB/DB number moves."""

import copy
import json
import re
from datetime import UTC, datetime

import pytest
from test_kicker_sources import ARTICLES, CBS, ESPN, ESPN_URL, FD, FEED, FP, FP_URL, ID, PLAYERS, RB9, RB11, RSS, Web
from test_kickers_isolation import K_PROJ, KClient, assert_idp_identical, k_routes, tree
from test_sync_site import CFG, LEAGUE, NOW, SCHED, FakeClient, routes

from sadlidp import backfill, scoring
from sadlidp.matching import Matcher
from sadlidp.net import Response
from sadlidp.sleeper import V1
from sadlidp.sources import cbs_k, espn_k, fantasydata_k, fp_k, registry, rotoballer_k
from sadlidp.sync import run_sync

SC = LEAGUE["scoring_settings"]
K_NAMES = registry.names_for("k")
LABELS = {"sleeper_k": "Sleeper", "espn_k": "ESPN", "cbs_k": "CBS", "fp_k": "FantasyPros",
          "fantasydata_k": "FantasyData", "rotoballer_k": "RotoBaller"}  # fmt: skip
MINE, SANTOS, SANDERS = "11786", "2020", "5119"  # my kicker (Cam Little); two waiver kickers
ESPN_ROUTE = espn_k.URL.format(season=2026, league=CFG["espn"]["league_id"])
ARTICLE = "https://www.rotoballer.com/week-5-kicker-streamers-starters-and-rankings-2026-fantasy-tiers-rankings/1995643"
POSTED = datetime(2026, 10, 6, 11, 9, 41, tzinfo=UTC)  # Tuesday morning of week 5, before every game


class SixClient(KClient):
    """KClient that also tells the two requests apart that differ from an IDP source's only outside the path:
    ESPN's kicker page (the X-Fantasy-Filter header) and RotoBaller's kicker feed (the search words). The IDP
    requests to those hosts get what they always got here: a 404."""

    def get(self, url, headers=None, **kw):
        if url.startswith(ESPN_ROUTE):
            slots = json.loads((headers or {}).get("X-Fantasy-Filter", "{}")).get("players", {}).get("filterSlotIds")
            return self._serve(url, "#K" if slots == {"value": [17]} else "#IDP")
        if url.startswith("https://www.rotoballer.com/?"):
            return self._serve(url, "#K" if "s=kicker+streamers+tiers" in url else "#IDP")
        return super().get(url, **kw)

    def _serve(self, url, tag):
        self.calls.append(url)
        body = self.routes.get(url.split("?")[0] + tag, 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        raw = body if isinstance(body, str) else json.dumps(body)
        return Response(url=url, final_url=url, status=200, body=raw.encode())


def six_routes() -> dict:
    """k_routes plus the five other kicker sources' week-5 pages, and one more waiver kicker (Cairo Santos, whom
    every fixture lists) with a Sleeper projection of his own."""
    r = k_routes()
    players = r[f"{V1}/players/nfl"]
    players[SANTOS] = {"full_name": "Cairo Santos", "position": "K", "fantasy_positions": ["K"], "team": "CHI",
                       "status": "Active", "active": True, "injury_status": None}  # fmt: skip
    k_route = next(key for key in r if key.endswith("/projections/nfl/2026/5#K"))
    row = copy.deepcopy(next(x for x in K_PROJ if x["player_id"] == SANDERS))  # a real line, relabelled
    row.update(player_id=SANTOS, team="CHI", week=5, opponent=SCHED.opponent("CHI", 5).lstrip("@"))
    listed = {x["player_id"] for x in r[k_route]} | {SANTOS}
    # Sleeper lists every other kicker it knows as a placeholder ("no projection"), as its real payload does for
    # the kickers it doesn't project; without a row at all they would count as projected 0 (the absence rule)
    blank = [{"player_id": pid, "week": 5, "team": p.get("team"), "opponent": None, "stats": {"adp_dd_ppr": 999.0}}
             for pid, p in players.items() if p.get("position") == "K" and pid not in listed]  # fmt: skip
    r[k_route] = [*r[k_route], row, *blank]
    espn = copy.deepcopy(ESPN)
    for e in espn:
        for s in e["player"]["stats"]:
            s["scoringPeriodId"] = 5 if s["scoringPeriodId"] == 11 else s["scoringPeriodId"]
    r[ESPN_ROUTE + "#K"] = {"players": espn}
    r[cbs_k.URL.format(season=2026, week=5)] = CBS.replace("Week 11 Proj", "Week 5 Proj")
    fp = FP.replace('"week": "11"', '"week": "5"')
    r[fp_k.URL.split("?")[0]] = re.sub(r'"last_updated_ts": \d+', f'"last_updated_ts": {int(POSTED.timestamp())}', fp)
    r[fantasydata_k.URL] = FD.replace("Week 11 K ", "Week 5 K ")
    r[rotoballer_k.URL.split("?")[0] + "#K"] = RSS.replace("/week-11-kicker-streamers", "/week-5-kicker-streamers")
    r[ARTICLE] = RB11.replace("2026-11-17T07:09:41-05:00", "2026-10-06T07:09:41-04:00")
    return r


@pytest.fixture(scope="module")
def six(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("six")
    base = run_sync(CFG, tmp / "base", tmp / "cb", NOW, client=FakeClient(routes()), log=lambda m: None)
    client = SixClient(six_routes())
    out = run_sync(CFG, tmp / "six", tmp / "cs", NOW, client=client, log=lambda m: None)
    assert base.synced and out.synced
    return tmp / "base", tmp / "six", out, client


def _json(path):
    return json.loads(path.read_text())


def test_all_six_kicker_sources_are_read_saved_and_reported(six):
    _, data, out, client = six
    week = data / "2026" / "week05"
    run = _json(week / "run.json")
    for name in K_NAMES:
        src = run["sources"][name]
        assert (src["status"], src["group"], src["label"], src["week"]) == ("ok", "k", LABELS[name], 5), name
        assert (week / "sources" / f"{name}.json").exists(), name
    assert {n: run["sources"][n]["rows"] for n in K_NAMES} == {
        "sleeper_k": 8, "espn_k": 11, "cbs_k": 9, "fp_k": 12, "fantasydata_k": 11, "rotoballer_k": 11,
    }  # fmt: skip
    assert not [w for w in out.warnings if w.startswith(K_NAMES) or "kicker" in w.lower()]
    assert run["sources"]["rotoballer_k"]["published_at"] == "2026-10-06T07:09:41-04:00"
    assert run["sources"]["fp_k"]["published_at"] == POSTED.isoformat()
    # one request a source and week (RotoBaller: the feed, then the article); never a CBS sort link, and the two
    # sources without an archive are never asked for a past week
    assert [c for c in client.calls if "cbssports.com" in c] == [cbs_k.URL.format(season=2026, week=5)]
    assert [c for c in client.calls if c.startswith(fantasydata_k.URL)] == [fantasydata_k.URL]
    assert client.calls.count(ARTICLE) == 1 and not [c for c in client.calls if "sortcol" in c or "sortdir" in c]
    assert run["backfilled"] == {}  # no page here shows a past week: nothing saved for weeks 1 to 4
    cbs, espn = _json(week / "sources" / "cbs_k.json"), _json(week / "sources" / "espn_k.json")
    assert cbs["universe"] == espn["universe"] == {"K": {"complete": False, "depth": None}}
    assert cbs["rows"][SANTOS]["imputed"] == ["fgm_yds", "fgmiss", "xpmiss"] and espn["rows"][SANTOS]["imputed"] == []
    assert {"2747", "12711", "650"} <= set(espn["no_projection_ids"])  # a bye, a backup, a free agent: never 0
    assert run["sources"]["espn_k"]["zero_filled"] == run["sources"]["cbs_k"]["zero_filled"] == 0
    ranks = {n: _json(week / "sources" / f"{n}.json") for n in ("fp_k", "fantasydata_k", "rotoballer_k")}
    assert {n: (f["lists"]["K"][SANTOS], f["universe"]["K"]["depth"]) for n, f in ranks.items()} == {
        "fp_k": (7, 13), "fantasydata_k": (6, 12), "rotoballer_k": (6, 12),
    }  # fmt: skip
    footer = _json(week / "consensus.json")["sources"]
    assert footer["cbs_k"]["match"]["names"] == ["Harrison Butker KC K", "Joey Slye TEN K", "Ryan Fitzgerald CAR K"]
    assert footer["fp_k"]["match"] == {"rate": 1.0, "unmatched": 0, "names": []}


def test_a_kicker_is_a_blend_of_three_projections_and_three_rank_lists(six):
    _, data, _, _ = six
    d = _json(data / "2026" / "week05" / "consensus.json")
    cards = {p["id"]: p for t in d["positions"]["K"]["tiers"] for p in t["players"]}
    assert set(cards) == {SANTOS, SANDERS, "6083", "11653", "4195"}  # the unrostered kickers with a number
    santos = cards[SANTOS]
    parts = santos["breakdown"]["parts"]
    assert [(p["source"], p["kind"]) for p in parts] == [
        ("Sleeper", "projection"), ("ESPN", "projection"), ("CBS", "projection"),
        ("FantasyPros", "rank"), ("FantasyData", "rank"), ("RotoBaller", "rank"),
    ]  # fmt: skip
    # the prior split: 0.6 to the three projections equally, 0.4 to the rank lists at 2 : 1 : 1
    assert [p["share"] for p in parts] == [0.2, 0.2, 0.2, 0.2, 0.1, 0.1]
    by = {p["source"]: p for p in parts}
    # each projection is the league's decimal scoring of that source's own line: 0.1 a yard of made field
    # goals, -1 a missed one, +1 an extra point, -1 a missed one
    line = next(e for e in ESPN if e["player"]["fullName"] == "Cairo Santos")["player"]["stats"][-1]["stats"]
    assert by["ESPN"]["pts"] == round(0.1 * line["214"] - line["85"] + line["86"] - line["88"], 2) == 10.06
    assert all(mark is False for _label, _value, mark in by["ESPN"]["stats"])  # ESPN projects the whole line
    assert by["CBS"]["pts"] == 11.3  # 0.1·84.98 − 0.4 + 3.3 − 0.1
    assert by["CBS"]["stats"] == [["FG yards", 84.98, True], ["XP", 3.3, False], ["missed FG", 0.4, True],
                                  ["missed XP", 0.1, True]]  # the three derived keys carry the mark  # fmt: skip
    sleeper = next(x for x in K_PROJ if x["player_id"] == SANDERS)["stats"]  # the line Santos was given
    assert by["Sleeper"]["pts"] == round(scoring.projected_points({**sleeper, "fgmiss": 0.32}, SC), 2) == 7.49
    assert [
        (by[s]["rank"], by[s]["list"], by[s]["censored"]) for s in ("FantasyPros", "FantasyData", "RotoBaller")
    ] == [
        (7, "K", False),
        (6, "K", False),
        (6, "K", False),
    ]
    # a rank becomes the points of the kicker projected that high on the list: a kicker's week, not an IDP's
    assert all(7.0 < by[s]["pts"] < 12.0 for s in ("FantasyPros", "FantasyData", "RotoBaller"))
    assert santos["mu"] == pytest.approx(sum(p["share"] * p["pts"] for p in parts), abs=0.02)
    assert (santos["n"], santos["min"], santos["max"], santos["slot"]) == (6, 7.49, 11.3, "K")
    assert 9.0 < santos["mu"] < 10.5 and "form" not in santos["breakdown"]
    # Sanders: CBS doesn't list him (said so, not counted as 0) and RotoBaller's list ends above him (censored)
    sanders = {p["source"]: p for p in cards[SANDERS]["breakdown"]["parts"]}
    assert sanders["CBS"] == {"source": "CBS", "missing": "no projection for him"}
    assert [sanders[s]["share"] for s in ("Sleeper", "ESPN", "FantasyPros", "FantasyData", "RotoBaller")] == [
        0.3, 0.3, 0.2, 0.1, 0.1,
    ]  # fmt: skip
    assert (sanders["RotoBaller"]["censored"], sanders["RotoBaller"]["rank"]) == (True, 13)
    assert (sanders["FantasyPros"]["rank"], sanders["FantasyData"]["rank"]) == (9, 12)
    assert [p["mu"] for p in cards.values()] == sorted((p["mu"] for p in cards.values()), reverse=True)
    assert all(5.0 < p["mu"] < 11.0 for p in cards.values())
    # my own kicker: all six sources, and the same six parts in the record kept for the results
    mine = d["my_idps"]["K"]["players"][0]
    assert mine["id"] == MINE and [p["source"] for p in mine["breakdown"]["parts"]] == list(LABELS.values())
    assert mine["n"] == 6 and mine["min"] == 4.0 and mine["breakdown"]["parts"][-1]["rank"] == 1  # RotoBaller's K1
    frozen = _json(data / "2026" / "week05" / "frozen.json")["players"]
    assert set(frozen[MINE]["parts"]) == set(frozen[SANTOS]["parts"]) == set(K_NAMES)
    run = _json(data / "2026" / "week05" / "run.json")
    assert run["weights"]["kickers"]["method"] == "prior" and run["weights"]["kickers"]["alpha"] == 0.6
    assert all(run["sources"][n]["mapped"] > 0 for n in ("fp_k", "fantasydata_k", "rotoballer_k"))


def test_the_kicker_sources_move_no_dl_lb_db_number(six):
    base, data, _, _ = six
    kickers = {pid for pid, p in six_routes()[f"{V1}/players/nfl"].items() if p.get("position") == "K"}
    b, k = tree(base), tree(data)
    assert_idp_identical(b, k, kickers)
    week = "2026/week05/"
    assert b[week + "sources/sleeper_proj.json"] == k[week + "sources/sleeper_proj.json"]
    assert {p.rsplit("/", 1)[1] for p in set(k) - set(b)} == {f"{n}.json" for n in K_NAMES}
    assert (
        k[week + "consensus.json"]["positions"]["K"]["tiers"]
        and not b[week + "consensus.json"]["positions"]["K"]["tiers"]
    )


def test_the_backfill_runner_fills_the_three_archives_and_skips_the_other_two(tmp_path):
    """Week 9 from the archives: ESPN (scoringPeriodId), FantasyPros (?week=) and RotoBaller (the feed) are read
    and saved as backfilled rows; CBS and FantasyData serve the current week only and are never asked."""
    espn = copy.deepcopy(ESPN)
    for e in espn:
        for s in e["player"]["stats"]:
            s["scoringPeriodId"] = 9 if s["scoringPeriodId"] == 11 else s["scoringPeriodId"]
    web = Web(
        {
            ESPN_URL.format(week=9): {"players": espn},
            FP_URL.format(week=9): FP.replace('"week": "11"', '"week": "9"'),
            FEED.format(page=1): RSS,
            ARTICLES[9]: RB9,
        }
    )
    five = {n: registry.K_SOURCES[n] for n in ("espn_k", "cbs_k", "fp_k", "fantasydata_k", "rotoballer_k")}
    done = backfill.run(five, CFG, tmp_path, 2026, [9], NOW, client=web, sleeper=None, players=PLAYERS,
                        matchers={"k": Matcher(PLAYERS, slots=("K",))}, log=lambda m: None)  # fmt: skip
    assert done == {"espn_k": [9], "fp_k": [9], "rotoballer_k": [9]}
    assert web.calls == [ESPN_URL.format(week=9), FP_URL.format(week=9), FEED.format(page=1), ARTICLES[9]]
    src = tmp_path / "2026" / "week09" / "sources"
    assert sorted(p.name for p in src.iterdir()) == ["espn_k.json", "fp_k.json", "rotoballer_k.json"]
    rb = _json(src / "rotoballer_k.json")
    assert rb["published_at"] == "2026-11-07T03:10:32-05:00" and rb["lists"]["K"][ID["Charlie Smyth"]] == 16
    for name in done:
        saved = _json(src / f"{name}.json")
        assert saved["rows"] and all(r["backfill"] is True for r in saved["rows"].values()), name
    assert _json(src / "espn_k.json")["rows"][ID["Brandon Aubrey"]]["stats"]["fgm_yds"] == 86.127
