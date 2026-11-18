"""The five kicker adapters beside `sleeper_k`: ESPN, CBS, FantasyPros, FantasyData and RotoBaller (SPEC §5.2;
kickers added 2026-11-17). The fixtures are trimmed real responses for week 11 of 2026 (week 9 for the second
RotoBaller article). All six kicker sources in one sync: test_kicker_sources_sync.py."""

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sadlidp import kicker, scoring
from sadlidp.backfill import source_cfg
from sadlidp.config import load_config
from sadlidp.matching import Matcher
from sadlidp.net import Response
from sadlidp.schedule import ET
from sadlidp.sources import cbs_k, espn_k, espn_proj, fantasydata_k, fp_k, registry, rotoballer, rotoballer_k
from sadlidp.sources.base import OK, STATUSES, SUSPECT, FetchContext

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests" / "fixtures"
CFG = load_config(ROOT / "config" / "sadl.toml")
SC = json.loads((FIX / "sleeper_league.json").read_text())["scoring_settings"]  # fgm_yds 0.1, fgmiss/xpmiss -1, xpm 1
NOW = datetime(2026, 11, 17, 21, 0, tzinfo=ET)


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


ESPN = json.loads(_read("espn_k_week11_trimmed.json"))["players"]
CBS = _read("cbs_k_week11_trimmed.html")
FP = _read("fp_k_week11_trimmed.html")
FD = _read("fantasydata_k_week11_trimmed.html")
RSS = _read("rotoballer_k_rss_trimmed.xml")
RB11 = _read("rotoballer_k_week11_trimmed.html")
RB9 = _read("rotoballer_k_week09_trimmed.html")
ESPN_URL = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/{CFG['espn']['league_id']}"
    "?view=kona_player_info&scoringPeriodId={week}"
)
CBS_URL = "https://www.cbssports.com/fantasy/football/stats/K/2026/{week}/projections/nonppr/"
FP_URL = "https://www.fantasypros.com/nfl/rankings/k.php?week={week}"
FD_URL = "https://fantasydata.com/nfl/rankings/k"
FEED = "https://www.rotoballer.com/?s=kicker+streamers+tiers&feed=rss2&paged={page}"
RB = "https://www.rotoballer.com/week-{week}-kicker-streamers-starters-and-rankings-2026-fantasy-tiers-rankings/{id}"
ARTICLES = {11: RB.format(week=11, id=1995643), 10: RB.format(week=10, id=1988707), 9: RB.format(week=9, id=1981623),
            8: RB.format(week=8, id=1976746)}  # fmt: skip
LOOKALIKES = (  # other RotoBaller kicker pieces of the same weeks: never the ranking article
    "https://www.rotoballer.com/week-10-kicker-streamers-best-fantasy-kicker-pickups-starts-2026/1991820",
    "https://www.rotoballer.com/week-10-kicker-k-rankings-for-fantasy-football-weekend-updates-2026/1993360",
    "https://www.rotoballer.com/fantasy-football-kicker-k-rankings-streamers-for-weeks-10-11-12-13-2026/1991332",
)

# Sleeper id: (name, team, espn_id, fantasy_data_id) as Sleeper lists the kickers the fixtures name; like the
# real players DB, only some carry another site's id
_KICKERS = {
    "3451": ("Ka'imi Fairbairn", "HOU", "2971573", "18215"),
    "11533": ("Brandon Aubrey", "DAL", None, "24902"),
    "11786": ("Cam Little", "JAX", None, None),
    "11792": ("Will Reichard", "MIN", None, None),
    "7042": ("Tyler Bass", "BUF", "3917232", None),
    "1945": ("Chris Boswell", "PIT", "17372", None),
    "2020": ("Cairo Santos", "CHI", None, None),
    "7839": ("Evan McPherson", "CIN", None, None),
    "5119": ("Jason Sanders", "NYJ", None, None),
    "12185": ("Spencer Shrader", "IND", None, None),
    "11539": ("Jake Bates", "DET", None, None),
    "6650": ("Chase McLaughlin", "TB", None, None),
    "4227": ("Harrison Butker", "KC", None, None),
    "8259": ("Cameron Dicker", "LAC", None, None),
    "3678": ("Wil Lutz", "DEN", None, None),
    "6528": ("Joey Slye", "TEN", None, None),
    "12961": ("Ryan Fitzgerald", "CAR", None, None),
    "2747": ("Jason Myers", "SEA", "2473037", None),
    "12711": ("Tyler Loop", "BAL", None, None),
    "650": ("Nick Folk", None, "10621", None),  # a free agent: found only through his ESPN id
    "11653": ("Charlie Smyth", "NO", None, None),
    "6083": ("Matt Gay", "LV", None, None),
    "4195": ("Jake Elliott", "PHI", None, None),
    "5095": ("Daniel Carlson", "MIA", None, None),
    "1264": ("Justin Tucker", "KC", None, None),
    "4233": ("Zane Gonzalez", None, None, None),
    "5189": ("Eddy Pineiro", "SF", None, None),
    "13968": ("Drew Stevens", "WAS", None, None),
    "1433": ("Brandon McManus", "LAR", None, None),
}
PLAYERS = {
    pid: {"full_name": name, "position": "K", "fantasy_positions": ["K"], "team": team, "active": True,
          "espn_id": espn, "fantasy_data_id": fd}
    for pid, (name, team, espn, fd) in _KICKERS.items()
}  # fmt: skip
ID = {p["full_name"]: pid for pid, p in PLAYERS.items()}


def ids(*names: str) -> set[str]:
    return {ID[n] for n in names}


class Web:
    """Serves {url: body | status | Response}; anything else is a 404. Keeps each call's URL and headers."""

    def __init__(self, pages: dict):
        self.pages, self.calls, self.headers = pages, [], []

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        self.headers.append(headers)
        body = self.pages.get(url, 404)
        if isinstance(body, Response):
            return body
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        raw = body if isinstance(body, str) else json.dumps(body)
        return Response(url=url, final_url=url, status=200, body=raw.encode())


class NoImputer:
    """A kicker line is never imputed: any call fails the test."""

    def fill(self, *a, **kw):
        raise AssertionError("a kicker adapter called the imputer")


def ctx(module, client, week=11, players=PLAYERS, cfg=None, xwalk=None, **kw):
    """The context the sync hands a kicker adapter: the kickers, a kicker matcher, its config table."""
    cfg = source_cfg(CFG, module.NAME) if cfg is None else cfg
    matcher = Matcher(players, crosswalk=xwalk, slots=("K",))
    return FetchContext(client, None, 2026, week, NOW, players, cfg, matcher, kw.pop("imputer", None), **kw)


CHALLENGE = Response(url="u", final_url="u", status=200, body=b"<html>Just a moment...</html>", challenged=True)


def test_all_six_kicker_sources_are_registered_with_their_kinds():
    assert list(registry.K_SOURCES) == ["sleeper_k", "espn_k", "cbs_k", "fp_k", "fantasydata_k", "rotoballer_k"]
    kinds = {name: kind for name, (_module, kind) in registry.K_SOURCES.items()}
    assert kinds == {"sleeper_k": "projection", "espn_k": "projection", "cbs_k": "projection", "fp_k": "rank",
                     "fantasydata_k": "rank", "rotoballer_k": "rank"}  # fmt: skip
    assert registry.K_SOURCES["espn_k"][0] is espn_k and registry.K_SOURCES["rotoballer_k"][0] is rotoballer_k
    assert list(registry.SOURCES) == ["sleeper_proj", "espn_proj", "fp_ecr", "rotoballer", "fantasydata"]
    for name, (module, _kind) in registry.K_SOURCES.items():  # the backfill runner's host budget and the config
        assert getattr(module, "HOST", None) or getattr(module, "URL", None), name
        assert CFG["sources"][name]["enabled"] is True
    archives = {n for n in registry.K_SOURCES if CFG["sources"][n]["archive"]}
    assert archives == {"sleeper_k", "espn_k", "fp_k", "rotoballer_k"}  # CBS and FantasyData serve one week only


# ---- ESPN --------------------------------------------------------------------------------------------------


def espn_web(players=None, week=11):
    return Web({ESPN_URL.format(week=week): {"players": ESPN if players is None else players}})


def test_espn_k_asks_for_the_kicker_slot_and_maps_the_scored_line():
    web = espn_web()
    res = espn_k.fetch(ctx(espn_k, web, imputer=NoImputer()))
    assert res.status == OK and (res.kind, res.week, res.published_at) == ("projection", 11, None)
    assert web.calls == [ESPN_URL.format(week=11)]  # 16 players: the first page is already short
    assert json.loads(web.headers[0]["X-Fantasy-Filter"]) == {
        "players": {
            "filterSlotIds": {"value": [17]},
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "limit": 400,
            "offset": 0,
        }
    }
    assert espn_k.STAT_MAP == {
        "214": "fgm_yds",
        "85": "fgmiss",
        "86": "xpm",
        "88": "xpmiss",
        "83": "fgm",
        "84": "fga",
        "87": "xpa",
        "80": "fgm_0_39",
        "77": "fgm_40_49",
        "74": "fgm_50p",
    }
    assert set(res.rows) == ids("Brandon Aubrey", "Will Reichard", "Ka'imi Fairbairn", "Tyler Bass", "Cam Little",
                                "Chris Boswell", "Matt Gay", "Cairo Santos", "Jason Sanders", "Jake Elliott",
                                "Charlie Smyth")  # fmt: skip
    aubrey = res.rows[ID["Brandon Aubrey"]]  # his week-11 line, not the season projection the entry also holds
    assert aubrey["stats"] == {
        "fgm_yds": 86.127,
        "fgmiss": 0.312,
        "xpm": 3.378,
        "xpmiss": 0.069,
        "fgm": 2.235,
        "fga": 2.548,
        "xpa": 3.447,
        "fgm_0_39": 1.226,
        "fgm_40_49": 0.574,
        "fgm_50p": 0.435,
    }
    assert aubrey["imputed"] == [] and aubrey["raw"]["214"] == 86.127 and aubrey["raw"]["215"] == 15.25
    assert set(aubrey["raw"]) == set(espn_k.STAT_MAP) | set(espn_k.RAW_ONLY)
    assert (aubrey["map_version"], aubrey["espn_id"], aubrey["team"], aubrey["opponent"]) == (1, "3953687", "DAL", None)
    # the league's decimal scoring on ESPN's own line: 0.1 a yard, -1 a miss, +1 an extra point, -1 a missed one
    assert scoring.projected_points(aubrey["stats"], SC) == pytest.approx(8.6127 - 0.312 + 3.378 - 0.069)
    assert res.universe == {"K": {"complete": False, "depth": None}}  # ESPN never zero-fills a kicker
    assert res.covered_keys == set(espn_k.STAT_MAP.values())
    rep = res.report
    assert (rep["pages"], rep["players"], rep["kickers"], rep["with_projection"]) == (1, 16, 16, 11)
    assert rep["duplicates"] == 0 and rep["unresolved_teams"] == {} and rep["archive"] is False
    match = rep["match"]  # by ESPN id where Sleeper has one, else name + team + slot; a free agent has no team
    assert match["by_method"] == {"source_id": 5, "name_team_slot": 10, "no_team": 1}
    assert match["unmatched"] == [] and match["teamed_match_rate"] == 1.0


def test_espn_k_empty_lines_are_placeholders_and_nothing_is_imputed():
    res = espn_k.fetch(ctx(espn_k, espn_web(), imputer=NoImputer()))
    # a bye (Myers), a backup (Loop), a free agent matched by his ESPN id (Folk): week entries with `stats: {}`;
    # Tucker has no week-11 entry at all. None of them is a 0
    assert res.no_projection_ids == ids("Jason Myers", "Tyler Loop", "Nick Folk", "Justin Tucker")
    assert not res.no_projection_ids & set(res.rows) and ID["Daniel Carlson"] not in res.rows  # FA, no id: unmatched
    assert all(r["imputed"] == [] for r in res.rows.values())
    empty = copy.deepcopy(ESPN)
    for e in empty:
        for s in e["player"]["stats"]:
            s["stats"] = {}
    res = espn_k.fetch(ctx(espn_k, espn_web(empty), imputer=NoImputer()))  # posted, but every line empty: mid-update
    assert res.status == SUSPECT and res.error == "no week-11 line has kicking stats" and not res.rows


def test_espn_k_another_week_a_response_without_kickers_and_failures():
    res = espn_k.fetch(ctx(espn_k, espn_web(week=12), week=12))  # the newest projected line is week 11's
    assert res.status == "not_yet_published" and res.week == 11 and not res.rows
    idp = json.loads(_read("espn_week06_trimmed.json"))["players"]  # what a client that ignores the filter serves
    res = espn_k.fetch(ctx(espn_k, espn_web(idp)))
    assert res.status == SUSPECT and res.error == "no kicker rows in the response" and res.report["kickers"] == 0
    url = ESPN_URL.format(week=11)
    assert espn_k.fetch(ctx(espn_k, Web({url: CHALLENGE}))).status == "blocked"
    res = espn_k.fetch(ctx(espn_k, Web({url: 500})))
    assert res.status == "failed" and "HTTP 500" in res.error
    assert espn_k.fetch(ctx(espn_k, Web({url: {"teams": []}}))).status == "parse_error"
    res = espn_k.fetch(ctx(espn_k, espn_web(), cfg={"_root": CFG["_root"]}))
    assert res.status == "skipped" and "league_id" in res.error


def test_espn_k_leaves_the_idp_request_as_it_was():
    assert json.loads(espn_proj.filter_header(400))["players"]["filterSlotIds"] == {"value": [10, 11, 14, 15]}
    assert espn_proj.filter_header(0) == espn_proj.filter_header(0, espn_proj.SLOT_IDS)
    assert espn_k.URL == espn_proj.URL and espn_k.SLOT_IDS == [17] and espn_proj.SLOT_IDS == [10, 11, 14, 15]


# ---- CBS ---------------------------------------------------------------------------------------------------


def cbs_web(html=CBS, week=11):
    return Web({CBS_URL.format(week=week): html})


def test_cbs_k_reads_the_table_and_derives_what_the_league_scores():
    web = cbs_web()
    res = cbs_k.fetch(ctx(cbs_k, web, imputer=NoImputer()))
    assert res.status == OK and (res.kind, res.week) == ("projection", 11)
    assert web.calls == [CBS_URL.format(week=11)] and "sortcol" not in web.calls[0]  # never a sort link
    assert set(res.rows) == ids("Ka'imi Fairbairn", "Brandon Aubrey", "Cairo Santos", "Tyler Bass", "Harrison Butker",
                                "Cameron Dicker", "Wil Lutz", "Jake Bates", "Cam Little", "Evan McPherson",
                                "Joey Slye", "Ryan Fitzgerald")  # fmt: skip
    fairbairn = res.rows[ID["Ka'imi Fairbairn"]]
    assert fairbairn["stats"] == {
        "gp": 1.0, "fgm": 2.6, "fga": 2.9, "xpm": 3.0, "xpa": 3.1,  # as published
        "fgm_0_19": 0.0, "fgm_20_29": 0.6, "fgm_30_39": 0.7, "fgm_40_49": 0.7, "fgm_50p": 0.5,
        "fgmiss": 0.3,  # fga − fgm
        "xpmiss": 0.1,  # xpa − xpm
        "fgm_yds": 97.35,  # 0.6·25.4 + 0.7·34.4 + 0.7·44.4 + 0.5·53.9
    }  # fmt: skip
    assert fairbairn["imputed"] == ["fgm_yds", "fgmiss", "xpmiss"]  # the page shows these three with a mark
    assert (fairbairn["cbs_id"], fairbairn["team"], fairbairn["opponent"]) == ("1996562", "HOU", None)
    assert scoring.projected_points(fairbairn["stats"], SC) == pytest.approx(9.735 - 0.3 + 3.0 - 0.1)
    little = res.rows[ID["Cam Little"]]  # the long name, not "C. Little"; CBS writes JAC
    assert little["team"] == "JAX" and little["stats"]["fgm_yds"] == round(
        0.4 * 25.4 + 0.5 * 34.4 + 0.7 * 44.4 + 0.3 * 53.9, 2
    )
    assert all(r["imputed"] == ["fgm_yds", "fgmiss", "xpmiss"] for r in res.rows.values())
    assert res.universe == {"K": {"complete": False, "depth": None}} and not res.no_projection_ids
    assert res.covered_keys == {
        "fga",
        "fgm",
        "xpa",
        "xpm",
        "fgm_0_19",
        "fgm_20_29",
        "fgm_30_39",
        "fgm_40_49",
        "fgm_50p",
    }
    rep = res.report
    assert (rep["players"], rep["unread_rows"], rep["unresolved_teams"]) == (12, 0, {})
    assert rep["match"]["by_method"] == {"name_team_slot": 12} and rep["imputed"] == ["fgm_yds", "fgmiss", "xpmiss"]


def test_cbs_k_yards_come_from_the_config_constants():
    assert kicker.yards_per_make(CFG) == {"0_19": 19.0, "20_29": 25.4, "30_39": 34.4, "40_49": 44.4, "50p": 53.9}
    flat = {**source_cfg(CFG, "cbs_k"), "kicker": {"yards_per_make": {b: 40.0 for b in kicker.BUCKETS}}}
    res = cbs_k.fetch(ctx(cbs_k, cbs_web(), cfg=flat))
    assert res.rows[ID["Ka'imi Fairbairn"]]["stats"]["fgm_yds"] == 100.0  # 2.5 makes across the bands, 40 yards each
    bare = {"_root": CFG["_root"]}  # no [kicker] table at all: the module's own constants, the same numbers
    assert cbs_k.fetch(ctx(cbs_k, cbs_web(), cfg=bare)).rows[ID["Ka'imi Fairbairn"]]["stats"]["fgm_yds"] == 97.35
    line = {"gp": 1.0, "fgm": 2.0, "fga": 2.0, "xpm": 1.0, "xpa": 3.0, **{f"fgm_{b}": 0.0 for b in kicker.BUCKETS}}
    out = cbs_k.derive({**line, "fgm_0_19": 1.0, "fgm_50p": 1.0}, kicker.yards_per_make(CFG))
    assert (out["fgm_yds"], out["fgmiss"], out["xpmiss"]) == (72.9, 0.0, 2.0)


def test_cbs_k_serves_the_current_week_whatever_the_url_asks():
    res = cbs_k.fetch(ctx(cbs_k, cbs_web(week=5), week=5))  # asked for week 5; the title says week 11
    assert res.status == "not_yet_published" and res.week == 11 and not res.rows and res.error == "page shows week 11"
    res = cbs_k.fetch(ctx(cbs_k, cbs_web(week=12), week=12))  # next week's page before CBS posts it
    assert res.status == "not_yet_published" and res.week == 11
    untitled = cbs_k.fetch(ctx(cbs_k, cbs_web(CBS.replace("Week 11 Proj", "Fantasy"))))
    assert untitled.status == "parse_error" and "title" in untitled.error


def test_cbs_k_failures_and_rows_it_cannot_read():
    url = CBS_URL.format(week=11)
    assert cbs_k.fetch(ctx(cbs_k, Web({url: 403}))).status == "blocked"
    assert cbs_k.fetch(ctx(cbs_k, Web({url: CHALLENGE}))).status == "blocked"
    assert cbs_k.fetch(ctx(cbs_k, Web({}))).status == "failed"
    no_xpa = cbs_k.fetch(ctx(cbs_k, cbs_web(CBS.replace(">xpa<", ">pat<"))))  # a column the line needs is gone
    assert no_xpa.status == "parse_error" and "xpa" in no_xpa.error
    short = CBS[: CBS.index("<tbody>")] + "<tbody>" + "</tr>".join(CBS.split("<tbody>")[1].split("</tr>")[:4])
    short = cbs_k.fetch(ctx(cbs_k, cbs_web(short + "</tr></tbody></table></body></html>")))
    assert short.status == "parse_error" and short.error == "4 rows"
    dash = CBS.replace("> 2.6 <", "> — <", 1)  # Fairbairn's FGM unreadable: his row is left out, the rest stand
    res = cbs_k.fetch(ctx(cbs_k, cbs_web(dash)))
    assert res.ok and ID["Ka'imi Fairbairn"] not in res.rows and res.report["unread_rows"] == 1 and len(res.rows) == 11
    idle = cbs_k.fetch(ctx(cbs_k, cbs_web(CBS.replace("> 1 <", "> 0 <", 1))))  # Fairbairn listed with no game
    assert idle.ok and idle.no_projection_ids == {ID["Ka'imi Fairbairn"]} and len(idle.rows) == 11  # missing, not 0
    nobody = cbs_k.fetch(ctx(cbs_k, cbs_web(), players={}))  # a players DB without kickers: never an empty week
    assert nobody.status == SUSPECT and not nobody.rows


# ---- FantasyPros -------------------------------------------------------------------------------------------


def fp_web(html=FP, week=11):
    return Web({FP_URL.format(week=week): html})


def test_fp_k_reads_one_list_named_k():
    web = fp_web()
    res = fp_k.fetch(ctx(fp_k, web))
    assert res.status == OK and (res.kind, res.week) == ("rank", 11)
    assert web.calls == [FP_URL.format(week=11)]  # the K page only: none of the IDP pages
    assert set(res.lists) == {"K"} and res.universe == {"K": {"complete": False, "depth": 13}}
    top = [
        "Ka'imi Fairbairn",
        "Brandon Aubrey",
        "Will Reichard",
        "Tyler Bass",
        "Cam Little",
        "Chris Boswell",
        "Cairo Santos",
        "Evan McPherson",
        "Jason Sanders",
        "Spencer Shrader",
        "Jake Bates",
        "Chase McLaughlin",
    ]
    assert res.lists["K"] == {ID[name]: rank for rank, name in enumerate(top, 1)}
    assert res.rows[ID["Ka'imi Fairbairn"]] == {"ranks": {"K": 1}, "ave": {"K": 1.86}, "std": {"K": 0.99},
                                               "fp_id": "15756", "team": "HOU"}  # fmt: skip
    assert res.rows[ID["Cam Little"]]["team"] == "JAX" and res.rows[ID["Cam Little"]]["ranks"] == {"K": 5}  # JAC
    assert res.published_at == datetime.fromtimestamp(1794963931, UTC).isoformat()  # ecrData.last_updated_ts
    rep = res.report
    assert rep["lists"]["K"] == {"status": "ok", "error": None, "players": 13, "week": 11, "experts": 9, "depth": 13}
    assert rep["unresolved_teams"] == {} and rep["archive"] is False
    assert rep["match"]["by_method"] == {"name_team_slot": 12, "no_team": 1} and rep["match"]["unmatched"] == []


def test_fp_k_a_free_agent_row_matches_only_through_the_crosswalk():
    res = fp_k.fetch(ctx(fp_k, fp_web()))
    assert ID["Zane Gonzalez"] not in res.rows and ID["Zane Gonzalez"] not in res.lists["K"]  # "FA": no team
    res = fp_k.fetch(ctx(fp_k, fp_web(), xwalk={"fantasypros": {"16548": ID["Zane Gonzalez"], "15756": "3451"}}))
    assert res.rows[ID["Zane Gonzalez"]] == {"ranks": {"K": 26}, "ave": {"K": 24.5}, "std": {"K": 0.5},
                                             "fp_id": "16548", "team": None}  # fmt: skip
    assert res.report["match"]["by_method"] == {"source_id": 2, "name_team_slot": 11}
    assert res.lists["K"][ID["Zane Gonzalez"]] == 26 and len(res.rows) == 13


def test_fp_k_another_week_blocks_and_pages_without_rankings():
    res = fp_k.fetch(ctx(fp_k, fp_web(week=12), week=12))  # the page still shows week 11
    assert res.status == "not_yet_published" and res.week == 11 and not res.rows
    assert res.report["lists"]["K"]["status"] == "not_yet_published"
    web = Web({FP_URL.format(week=11): 403})
    res = fp_k.fetch(ctx(fp_k, web))
    assert res.status == "blocked" and "HTTP 403" in res.error and len(web.calls) == 1
    assert fp_k.fetch(ctx(fp_k, fp_web("<html>maintenance</html>"))).status == "parse_error"
    assert fp_k.fetch(ctx(fp_k, Web({}))).status == "failed"
    nobody = fp_k.fetch(ctx(fp_k, fp_web(), players={}))
    assert nobody.status == SUSPECT and nobody.error == "none of the 13 ranked rows matched a kicker"


# ---- FantasyData -------------------------------------------------------------------------------------------


def fd_web(html=FD):
    return Web({FD_URL: html})


def test_fantasydata_k_drops_the_unprojected_backups():
    web = fd_web()
    res = fantasydata_k.fetch(ctx(fantasydata_k, web))
    assert res.status == OK and (res.kind, res.week) == ("rank", 11) and web.calls == [FD_URL]
    order = [
        "Ka'imi Fairbairn",
        "Brandon Aubrey",
        "Tyler Bass",
        "Cam Little",
        "Will Reichard",
        "Cairo Santos",
        "Chris Boswell",
        "Cameron Dicker",
        "Wil Lutz",
        "Harrison Butker",
        "Jake Bates",
        "Jason Sanders",
    ]
    assert res.lists == {"K": {ID[name]: rank for rank, name in enumerate(order, 1)}}
    assert res.universe == {"K": {"complete": False, "depth": 12}}
    # the page's last four rows are projected 0.0: Loop (BAL has Patterson), Carlson, Tucker (KC has Butker),
    # Matsuzawa. None of them is ranked
    assert not ids("Tyler Loop", "Daniel Carlson", "Justin Tucker") & set(res.rows)
    info = res.report["lists"]["K"]
    assert (info["players"], info["dropped_unprojected"], info["depth"], info["week"]) == (12, 4, 12, 11)
    assert res.rows[ID["Ka'imi Fairbairn"]] == {"ranks": {"K": 1}, "fd_id": "18215", "team": "HOU"}
    assert res.report["match"]["by_method"] == {"source_id": 2, "name_team_slot": 10}  # Sleeper's fantasy_data_id first
    assert res.report["unresolved_teams"] == {} and res.published_at is None


def test_fantasydata_k_reranks_in_page_order_after_a_drop():
    row = next(r for r in FD.splitlines() if "Tyler Bass" in r)
    assert ">K3<" in row and row.endswith(">10.1</td></tr>")
    html = FD.replace(row, row.replace(">10.1</td></tr>", ">0.0</td></tr>"))  # the page's third row loses his game
    res = fantasydata_k.fetch(ctx(fantasydata_k, fd_web(html)))
    assert res.ok and ID["Tyler Bass"] not in res.rows and res.report["lists"]["K"]["dropped_unprojected"] == 5
    assert res.rows[ID["Cam Little"]]["ranks"] == {"K": 3} and res.rows[ID["Will Reichard"]]["ranks"] == {"K": 4}
    assert sorted(res.lists["K"].values()) == list(range(1, 12)) and res.universe["K"]["depth"] == 11


def test_fantasydata_k_title_week_and_failures():
    assert (
        fantasydata_k.TITLE.search("<title>Week 11 K Fantasy Football Rankings | FantasyData</title>").group(1) == "11"
    )
    assert not fantasydata_k.TITLE.search("<title>Week 11 DL Fantasy Football Rankings | FantasyData</title>")
    assert not fantasydata_k.TITLE.search("<title>Week 11 Kicker Fantasy Football Rankings</title>")
    res = fantasydata_k.fetch(ctx(fantasydata_k, fd_web(), week=12))  # no archive, no week in the URL
    assert res.status == "not_yet_published" and res.week == 11 and not res.rows
    idp_page = FD.replace("Week 11 K ", "Week 11 DL ")
    res = fantasydata_k.fetch(ctx(fantasydata_k, fd_web(idp_page)))
    assert res.status == "parse_error" and "title" in res.error
    assert fantasydata_k.fetch(ctx(fantasydata_k, Web({FD_URL: 503}))).status == "blocked"
    assert fantasydata_k.fetch(ctx(fantasydata_k, Web({}))).status == "failed"
    few = "\n".join(line for line in FD.splitlines() if not re.search(r">K(\d\d|[4-9])<", line))  # 3 kickers left
    res = fantasydata_k.fetch(ctx(fantasydata_k, fd_web(few)))
    assert res.status == "parse_error" and res.error == "K: 3 rows"
    no_col = fantasydata_k.fetch(ctx(fantasydata_k, fd_web(FD.replace(">PROJECTED<", ">FPTS<"))))
    assert no_col.status == "parse_error" and "PROJECTED" in no_col.error  # a backup can't be told from a starter


# ---- RotoBaller --------------------------------------------------------------------------------------------


def rb_web(week=11, article=RB11, feed=RSS):
    return Web({FEED.format(page=1): feed, ARTICLES[week]: article})


def test_rotoballer_k_link_pattern_takes_the_ranking_article_only():
    links = re.findall(r"<link>\s*([^<\s]+)\s*</link>", RSS)
    found = {link: m for link in links if (m := rotoballer_k.LINK.search(link))}
    assert set(found) == set(ARTICLES.values())  # the four real slugs in the feed: weeks 11, 10, 9 and 8
    assert {(int(m.group(1)), int(m.group(2))) for m in found.values()} == {
        (11, 2026),
        (10, 2026),
        (9, 2026),
        (8, 2026),
    }
    for link in LOOKALIKES:
        assert not rotoballer_k.LINK.search(link), link
    idp = "https://www.rotoballer.com/week-5-idp-rankings-streamers-start-sit-fantasy-football-2026/1959614"
    assert not rotoballer_k.LINK.search(idp) and rotoballer.LINK.search(idp)  # each adapter finds its own article
    assert not any(rotoballer.LINK.search(link) for link in ARTICLES.values())
    assert rotoballer_k.RSS != rotoballer.RSS and "kicker+streamers+tiers" in rotoballer_k.RSS


def test_rotoballer_k_finds_each_weeks_article_past_the_lookalikes():
    item = "<item>\n<title>Best Week 10 Kicker Streamers</title>\n<link>{}</link>\n</item>\n\t"
    noisy = RSS.replace("<item>", "".join(item.format(link) for link in LOOKALIKES) + "<item>", 1)
    for week, url in ARTICLES.items():
        web = Web({FEED.format(page=1): noisy})
        assert rotoballer.find_article(ctx(rotoballer_k, web, week=week), rotoballer_k.RSS, rotoballer_k.LINK) == (
            url, OK, None,
        )  # fmt: skip
        assert web.calls == [FEED.format(page=1)]
    web = Web({FEED.format(page=1): noisy})  # week 12: not in the feed; page 2 answers 404, past the last page
    res = rotoballer_k.fetch(ctx(rotoballer_k, web, week=12))
    assert res.status == "not_yet_published" and "week-12" in res.error and len(web.calls) == 2
    assert rotoballer_k.fetch(ctx(rotoballer_k, Web({FEED.format(page=1): 503}))).status == "blocked"
    res = rotoballer_k.fetch(ctx(rotoballer_k, Web({FEED.format(page=1): RSS})))  # the article itself is gone
    assert res.status == "failed" and res.error == "article: HTTP 404" and res.report["article"] == ARTICLES[11]
    idp_feed = _read("rotoballer_rss_trimmed.xml")  # what a client that ignores the search words would serve
    web = Web({FEED.format(page=page): idp_feed for page in (1, 2, 3)})
    res = rotoballer_k.fetch(ctx(rotoballer_k, web, week=5))  # it holds the week-5 IDP article: not this one
    assert res.status == "not_yet_published" and not res.rows and len(web.calls) == 3


def test_rotoballer_k_reads_tier_rank_name_team():
    web = rb_web()
    res = rotoballer_k.fetch(ctx(rotoballer_k, web))
    assert res.status == OK and (res.kind, res.week) == ("rank", 11)
    assert web.calls == [FEED.format(page=1), ARTICLES[11]]
    assert res.published_at == "2026-11-17T07:09:41-05:00"  # JSON-LD datePublished, not dateModified
    order = [
        "Cam Little",
        "Brandon Aubrey",
        "Ka'imi Fairbairn",
        "Evan McPherson",
        "Tyler Bass",
        "Cairo Santos",
        "Jake Bates",
        "Chris Boswell",
        "Chase McLaughlin",
        "Spencer Shrader",
        "Will Reichard",
        "Joey Slye",
    ]
    assert res.lists == {"K": {ID[name]: rank for rank, name in enumerate(order, 1)}}  # column 2, not the tier
    assert res.rows[ID["Cam Little"]] == {"ranks": {"K": 1}, "flags": {}, "team": "JAX"}
    assert res.universe == {"K": {"complete": False, "depth": 12}}
    rep = res.report
    assert rep["lists"]["K"] == {"status": "ok", "error": None, "players": 12, "depth": 12}
    assert rep["match"]["by_method"] == {"name_team_slot": 12} and rep["match"]["teamed_match_rate"] == 1.0
    assert rep["unresolved_teams"] == {} and rep["dropped_after_kickoff"] == 0 and rep["article"] == ARTICLES[11]


def test_rotoballer_k_header_test_and_team_codes():
    assert rotoballer_k.ranking_table(RB11)[0] == OK and len(rotoballer_k.ranking_table(RB9)[1]) == 16
    updates = RB11.replace("<td><strong>Team</strong></td>", "<td><strong>Pos</strong></td>")  # "weekend updates"
    assert rotoballer_k.ranking_table(updates) == ("parse_error", [], "K: no kicker ranking table")
    idp = _read("rotoballer_week05_trimmed.html")  # Rank, Player, Team: the IDP article's tables
    assert rotoballer_k.ranking_table(idp)[0] == "parse_error"
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(article=updates)))
    assert res.status == "parse_error" and res.report["lists"]["K"]["status"] == "parse_error" and not res.rows
    short = RB11[: RB11.index("<td>2</td>\n<td>5</td>")] + "</tr>\n</tbody>\n</table></body></html>"
    assert rotoballer_k.ranking_table(short.replace("<tr>\n</tr>", "")) == ("parse_error", [], "K: 4 rows")
    aliases = json.loads((ROOT / "config" / "aliases" / "rotoballer_teams.json").read_text())
    assert (aliases["wsh"], aliases["jac"], aliases["la"], aliases["was"], aliases["jax"]) == (
        "WAS", "JAX", "LAR", "WAS", "JAX",
    )  # fmt: skip
    other = RB11.replace("<td>JAX</td>", "<td>JAC</td>").replace("<td>DAL</td>", "<td>XYZ</td>")
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(article=other)))
    assert res.rows[ID["Cam Little"]]["team"] == "JAX" and res.report["unresolved_teams"] == {"XYZ": 1}
    assert ID["Brandon Aubrey"] not in res.rows  # no team, no match: counted, never guessed


def test_rotoballer_k_strips_the_note_star_and_reads_unlinked_names():
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(9, RB9), week=9))
    assert res.ok and res.published_at == "2026-11-07T03:10:32-05:00"
    assert "Charlie Smyth</a>*" in RB9 and res.rows[ID["Charlie Smyth"]]["ranks"] == {"K": 16}  # "Charlie Smyth*"
    assert res.rows[ID["Ka'imi Fairbairn"]]["ranks"] == {"K": 2}  # plain text in the cell, no player link
    assert res.rows[ID["Brandon McManus"]] == {"ranks": {"K": 14}, "flags": {}, "team": "LAR"}
    assert res.report["match"]["by_method"] == {"name_team_slot": 16} and len(res.lists["K"]) == 16
    flagged = RB9.replace("Charlie Smyth</a>*", "Charlie Smyth</a> (Q)*")
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(9, flagged), week=9))
    assert res.rows[ID["Charlie Smyth"]] == {"ranks": {"K": 16}, "flags": {"K": "Q"}, "team": "NO"}


def test_rotoballer_k_kickers_who_played_before_the_article_are_dropped_but_stay_in_the_list():
    # the week-9 article is dated Saturday 03:10 ET (RotoBaller re-dates an article it updates): a kicker of
    # that week's Thursday game played before it, so his rank was never a pre-game rank
    thursday = datetime(2026, 11, 5, 20, 15, tzinfo=ET)
    sunday = datetime(2026, 11, 8, 13, 0, tzinfo=ET)
    early = ID["Matt Gay"]
    kick = lambda pid: thursday if pid == early else sunday  # noqa: E731
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(9, RB9), week=9, kickoff_of=kick))
    assert res.ok and early not in res.rows and res.lists["K"][early] == 15 and len(res.rows) == 15
    assert res.report["dropped_after_kickoff"] == 1 and res.universe["K"]["depth"] == 16
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(9, RB9), week=9, kickoff_of=lambda pid: thursday))
    assert res.ok and not res.rows and len(res.lists["K"]) == 16  # everyone had played: the list alone is kept
    undated = RB9.replace("datePublished", "published")
    res = rotoballer_k.fetch(ctx(rotoballer_k, rb_web(9, undated), week=9, kickoff_of=lambda pid: thursday))
    assert res.published_at is None and len(res.rows) == 16  # no date to compare against: nothing dropped


# ---- every adapter -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", [espn_k, cbs_k, fp_k, fantasydata_k, rotoballer_k], ids=lambda m: m.NAME)
def test_every_outcome_is_a_known_status_and_a_dead_host_is_never_ok(module):
    res = module.fetch(ctx(module, Web({})))  # every request answers 404
    assert res.status in STATUSES and not res.ok and not res.rows and res.name == module.NAME
    assert res.fetched_at == NOW.isoformat() and res.error
    res = module.fetch(ctx(module, Web({}), archive=True))
    assert res.report.get("archive") is True  # the backfill runner's flag reaches the report
