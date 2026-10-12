import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sadlidp.matching import Match, Matcher
from sadlidp.positions import slot_of
from sadlidp.sources import espn_proj
from sadlidp.sources.base import OK, SUSPECT, FetchContext

ROOT = Path(__file__).parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
CFG = {"espn_league_id": "942104146", "_root": str(ROOT / "config")}
ESPN = json.loads((FIXTURES / "espn_week06_trimmed.json").read_text())["players"]
SLEEPER = json.loads((FIXTURES / "espn_sleeper_players.json").read_text())["players"]
NICKNAMES = json.loads((ROOT / "config" / "aliases" / "nicknames.json").read_text())
IMPUTABLE = ("idp_tkl_loss", "idp_qb_hit", "idp_def_td", "idp_safe", "idp_blk_kick")
FRED, ROQUAN, STONE = "5041", "4960", "6923"  # Sleeper ids (Fred Warner and Roquan Smith match by espn_id)


class FakeResponse:
    def __init__(self, status=200, payload=None, error=None, challenged=False, body=None):
        self.status, self.error, self.challenged = status, error, challenged
        self.body = body if body is not None else json.dumps(payload).encode()

    @property
    def ok(self):
        return self.status is not None and 200 <= self.status < 300

    def json(self):
        return json.loads(self.body)


class FakeClient:
    """Serves the fixture's players as ESPN pages, honouring the X-Fantasy-Filter's offset and limit."""

    def __init__(self, players=None, errors=None):
        self.players = ESPN if players is None else players
        self.errors = errors or {}  # offset -> FakeResponse
        self.calls = []

    def get(self, url, headers=None):
        flt = json.loads(headers["X-Fantasy-Filter"])
        self.calls.append((url, flt))
        offset, limit = flt["players"]["offset"], flt["players"]["limit"]
        if offset in self.errors:
            return self.errors[offset]
        return FakeResponse(payload={"players": self.players[offset : offset + limit], "positionAgainstOpponent": {}})

    @property
    def offsets(self):
        return [flt["players"]["offset"] for _, flt in self.calls]


class FakeImputer:
    """Fills every IMPUTABLE key the source doesn't cover with 0.1 (the real one is tested in its own file)."""

    def __init__(self):
        self.covered = []

    def fill(self, pid, line, covered):
        self.covered.append(set(covered))
        missing = [k for k in IMPUTABLE if k not in covered]
        return {**line, **dict.fromkeys(missing, 0.1)}, sorted(missing)


class FixedMatcher:
    """The real matcher, except for the ESPN ids pinned in `fixed`."""

    def __init__(self, fixed):
        self.real, self.fixed = Matcher(SLEEPER, {}, NICKNAMES), fixed

    def match(self, source, **kw):
        return self.fixed.get(kw["source_id"]) or self.real.match(source, **kw)


def ctx(client=None, players=None, matcher=None, week=6, **kw):
    players = SLEEPER if players is None else players
    kw = {"cfg": CFG, "matcher": matcher or Matcher(players, {}, NICKNAMES), "imputer": FakeImputer(), **kw}
    return FetchContext(client or FakeClient(), None, 2026, week, NOW, players, **kw)


def espn_id(name):
    return next(str(e["player"]["id"]) for e in ESPN if e["player"]["fullName"] == name)


def test_rows_covered_keys_imputation_and_report():
    client, imputer = FakeClient(), FakeImputer()
    res = espn_proj.fetch(ctx(client, imputer=imputer))
    assert res.status == OK and res.week == 6 and res.fetched_at == NOW.isoformat() and res.published_at is None
    url, flt = client.calls[0]
    assert url == (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/942104146"
        "?view=kona_player_info&scoringPeriodId=6"
    )
    assert flt == {
        "players": {
            "filterSlotIds": {"value": [10, 11, 14, 15]},
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "limit": 400,
            "offset": 0,
        }
    }
    assert len(client.calls) == 1  # 57 players: the first page is already short
    assert res.covered_keys == set(espn_proj.STAT_MAP.values()) and "idp_blk_kick" in res.covered_keys
    assert all(c == res.covered_keys for c in imputer.covered)
    assert len(res.rows) == 44 and len(res.no_projection_ids) == 11 and not set(res.rows) & res.no_projection_ids

    fred = res.rows[FRED]  # week-6 projection, not his week-5 actuals line (10 tackles)
    assert fred["stats"]["idp_tkl_solo"] == pytest.approx(5.028540718)
    assert fred["stats"]["idp_tkl_ast"] == pytest.approx(3.987932272)
    assert fred["stats"]["idp_sack"] == pytest.approx(0.111941852)
    assert fred["imputed"] == ["idp_def_td", "idp_qb_hit", "idp_safe", "idp_tkl_loss"]
    assert fred["stats"]["idp_tkl_loss"] == 0.1 and "idp_blk_kick" not in fred["imputed"]
    assert set(fred["raw"]) == set(espn_proj.STAT_MAP) | {"94", "98", "103", "105"}  # no 93, 100, 110, 210
    assert fred["raw"]["108"] == fred["stats"]["idp_tkl_solo"]
    assert (fred["map_version"], fred["espn_id"], fred["team"], fred["opponent"]) == (1, "3138826", "SF", None)

    assert {pid for pid, p in SLEEPER.items() if p["full_name"] == "Travis Hunter"} & set(res.rows)  # no ESPN slot
    rep = res.report
    assert (rep["pages"], rep["players"], rep["with_projection"], rep["complete"]) == (1, 57, 44, True)
    assert rep["why_incomplete"] is None and rep["duplicates"] == 0 and rep["unresolved_teams"] == {}
    assert rep["imputed"] == {"idp_def_td": 44, "idp_qb_hit": 44, "idp_safe": 44, "idp_tkl_loss": 44}
    match = rep["match"]
    assert match["by_method"] == {
        "source_id": 26,
        "name_team_slot": 25,
        "name_team": 2,
        "name": 1,
        "nickname": 1,
        "unmatched": 1,
        "no_team": 1,
    }
    assert match["unmatched"] == [{"name": "Jeremiah Moon", "team": "CAR", "slot": "DL", "how": "unmatched"}]
    assert (match["matched"], match["teamed"], match["conflicts"]) == (55, 55, [])
    assert match["teamed_match_rate"] == pytest.approx(54 / 55)

    for slot in ("DL", "LB", "DB"):
        want = sorted(pid for pid, p in SLEEPER.items() if slot_of(p) == slot and p.get("espn_id"))
        assert res.universe[slot] == {"complete": True, "depth": None, "zero_fill_ids": want} and want


def test_without_imputer_rows_hold_only_mapped_keys():
    res = espn_proj.fetch(ctx(imputer=None))
    assert res.ok and res.rows[FRED]["imputed"] == [] and set(res.rows[FRED]["stats"]) <= res.covered_keys
    assert res.report["imputed"] == {}


def test_no_matcher_falls_back_to_one_built_from_the_players():
    res = espn_proj.fetch(FetchContext(FakeClient(), None, 2026, 6, NOW, SLEEPER, CFG))
    assert res.ok and FRED in res.rows and res.report["match"]["by_method"]["source_id"] == 26


@pytest.mark.parametrize(("size", "offsets"), [(20, [0, 20, 40]), (19, [0, 19, 38, 57])])
def test_paging_stops_on_the_first_short_page(monkeypatch, size, offsets):
    monkeypatch.setattr(espn_proj, "PAGE_SIZE", size)
    client = FakeClient()
    res = espn_proj.fetch(ctx(client))
    assert client.offsets == offsets and all(flt["players"]["limit"] == size for _, flt in client.calls)
    assert res.ok and res.report["pages"] == len(offsets) and res.report["complete"] and len(res.rows) == 44


def test_paging_cap_leaves_the_result_incomplete(monkeypatch):
    monkeypatch.setattr(espn_proj, "PAGE_SIZE", 5)
    client = FakeClient()
    res = espn_proj.fetch(ctx(client))
    assert client.offsets == [0, 5, 10, 15, 20, 25] and res.report["pages"] == 6
    assert res.ok and res.rows and not res.report["complete"] and "6-page cap" in res.report["why_incomplete"]
    assert all(u == {"complete": False, "depth": None, "zero_fill_ids": []} for u in res.universe.values())


@pytest.mark.parametrize(
    ("response", "status", "error"),
    [
        (FakeResponse(500, {}), "failed", "page 2 (offset 20): HTTP 500"),
        (FakeResponse(None, error="timeout", body=b""), "failed", "page 2 (offset 20): timeout"),
        (FakeResponse(403, challenged=True, body=b"Just a moment"), "blocked", "page 2 (offset 20): bot challenge"),
        (FakeResponse(200, body=b"<html>"), "parse_error", "page 2 (offset 20): no players list"),
        (FakeResponse(200, {"error": "x"}), "parse_error", "page 2 (offset 20): no players list"),
    ],
)
def test_any_page_error_fails_the_whole_fetch(monkeypatch, response, status, error):
    monkeypatch.setattr(espn_proj, "PAGE_SIZE", 20)
    client = FakeClient(errors={20: response})
    res = espn_proj.fetch(ctx(client))
    assert (res.status, res.error) == (status, error)
    assert client.offsets == [0, 20] and not res.rows and not res.no_projection_ids and not res.universe
    assert res.report["pages"] == 2


def test_week_without_projection_lines_is_not_yet_published():
    res = espn_proj.fetch(ctx(week=7))
    assert res.status == "not_yet_published" and res.week == 6 and not res.rows  # ESPN only has week 6 here
    empty = [{**e, "player": {**e["player"], "stats": []}} for e in ESPN]
    res = espn_proj.fetch(ctx(FakeClient(empty)))
    assert res.status == "not_yet_published" and res.week is None


def test_posted_week_with_no_idp_stats_is_suspect():
    stripped = copy.deepcopy(ESPN)
    for e in stripped:
        for line in e["player"]["stats"]:
            line["stats"] = {k: v for k, v in line["stats"].items() if k not in espn_proj.STAT_MAP}
    res = espn_proj.fetch(ctx(FakeClient(stripped)))
    assert res.status == SUSPECT and not res.rows and len(res.no_projection_ids) == 55


def test_repeated_espn_ids_make_the_result_incomplete():
    res = espn_proj.fetch(ctx(FakeClient([*ESPN, ESPN[3]])))
    assert res.ok and len(res.rows) == 44 and res.report["duplicates"] == 1 and res.report["players"] == 58
    assert not res.report["complete"] and res.report["why_incomplete"] == "1 repeated ESPN ids"
    assert all(not u["complete"] and u["zero_fill_ids"] == [] for u in res.universe.values())


def test_low_match_rate_makes_the_result_incomplete():
    dean = next(pid for pid, p in SLEEPER.items() if p["full_name"] == "Nakobe Dean")  # matched by name + team
    players = {pid: p for pid, p in SLEEPER.items() if pid != dean}
    res = espn_proj.fetch(ctx(players=players))
    assert res.ok and dean not in res.rows and len(res.rows) == 43
    assert res.report["match"]["teamed_match_rate"] == pytest.approx(53 / 55)
    assert res.report["why_incomplete"] == "matched 96.4% of players with an NFL team (< 98%)"
    assert {"name": "Nakobe Dean", "team": "LV", "slot": "LB", "how": "unmatched"} in res.report["match"]["unmatched"]
    assert all(u["zero_fill_ids"] == [] for u in res.universe.values())


def test_free_agents():
    res = espn_proj.fetch(ctx())
    assert STONE in res.no_projection_ids  # ESPN free agent with an espn_id in Sleeper and an empty line
    match = res.report["match"]
    assert match["by_method"]["no_team"] == 1 and match["teamed"] == 55  # free agents don't count toward the rate
    assert "Claudin Cherelus" not in {u["name"] for u in match["unmatched"]}  # no team: counted, not listed

    signed = copy.deepcopy(ESPN)  # a free agent with a real line is still a row, without a team
    stone = next(e for e in signed if e["player"]["fullName"] == "Geno Stone")
    stone["player"]["stats"] = copy.deepcopy(ESPN[0]["player"]["stats"])
    res = espn_proj.fetch(ctx(FakeClient(signed)))
    assert res.rows[STONE]["team"] is None and STONE not in res.no_projection_ids


def test_two_espn_rows_on_one_player_keep_the_higher_precedence():
    # Roquan Smith's row also claims Fred Warner by name + team: Fred's own espn_id match wins.
    res = espn_proj.fetch(ctx(matcher=FixedMatcher({espn_id("Roquan Smith"): Match(FRED, "name_team")})))
    assert res.ok and res.rows[FRED]["espn_id"] == "3138826" and ROQUAN not in res.rows
    assert res.report["match"]["conflicts"] == [
        {
            "pid": FRED,
            "kept": "Fred Warner (ESPN 3138826, source_id)",
            "dropped": "Roquan Smith (ESPN 3915189, name_team)",
        }
    ]
    # The weaker row comes first this time (Fred Warner is the most owned): the later, better one replaces it.
    res = espn_proj.fetch(ctx(matcher=FixedMatcher({"3138826": Match(ROQUAN, "name")})))
    assert res.rows[ROQUAN]["espn_id"] == "3915189" and FRED not in res.rows
    assert res.report["match"]["conflicts"][0]["kept"] == "Roquan Smith (ESPN 3915189, source_id)"


def test_unresolved_team_codes_are_reported():
    moved = copy.deepcopy(ESPN)
    moved[0]["player"]["proTeamId"] = 99
    res = espn_proj.fetch(ctx(FakeClient(moved)))
    assert res.report["unresolved_teams"] == {"99": 1} and res.rows[FRED]["team"] is None  # still matched by id


def test_archive_flag_is_allowed_and_changes_nothing_else():
    live, archive = espn_proj.fetch(ctx()), espn_proj.fetch(ctx(archive=True))
    assert archive.ok and archive.rows == live.rows and archive.universe == live.universe
    assert archive.report["archive"] is True and live.report["archive"] is False


def test_missing_league_id_or_team_table():
    res = espn_proj.fetch(ctx(cfg={}))
    assert res.status == "skipped" and res.error == "no espn.league_id configured"
    res = espn_proj.fetch(ctx(cfg={"espn_league_id": "1", "_root": str(FIXTURES)}))
    assert res.status == "failed" and "espn_teams.json" in res.error


def test_team_table_is_in_sleeper_vocabulary():
    table = json.loads((ROOT / "config" / "aliases" / "espn_teams.json").read_text())
    sleeper_codes = set(
        "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LAC LAR LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF "
        "TB TEN WAS".split()
    )
    assert table.pop("0") is None and set(table.values()) == sleeper_codes and len(table) == 32
    by_espn = {str(p["espn_id"]): p for p in SLEEPER.values() if p.get("espn_id") and p.get("team")}
    teamed = [e["player"] for e in ESPN if e["player"]["proTeamId"] and str(e["player"]["id"]) in by_espn]
    assert len(teamed) > 20  # live (1,511 players, 2026-10-12): all 256 pairs where both sides have a team agreed
    assert all(table[str(p["proTeamId"])] == by_espn[str(p["id"])]["team"] for p in teamed)
