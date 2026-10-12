import json
from pathlib import Path

import pytest

from sadlidp.matching import METHODS, Match, Matcher, MatchReport, load_nicknames, load_overrides, normalize_name

FIXTURES = Path(__file__).parent.parent / "fixtures"
CONFIG = Path(__file__).parent.parent.parent / "config"


def _p(name, team, position, fantasy=None, **extra):
    first, _, last = name.partition(" ")
    rec = {"full_name": name, "first_name": first, "last_name": last, "team": team, "position": position}
    return rec | {"fantasy_positions": fantasy or [position], "active": True} | extra


def test_normalize_name_accents_punctuation_suffixes():
    assert normalize_name("Will Anderson Jr.") == normalize_name("Will Anderson") == "will anderson"
    assert normalize_name("Jevón Holland") == "jevon holland"
    assert normalize_name("T.J. Watt") == normalize_name("T. J. Watt") == normalize_name("TJ Watt") == "tj watt"
    assert normalize_name("Ja'Quan McMillian") == normalize_name("Ja’Quan  McMillian") == "jaquan mcmillian"
    assert normalize_name("Joe Tryon-Shoyinka") == normalize_name("Joe Tryon Shoyinka") == "joe tryon shoyinka"
    assert normalize_name("Harold Landry III") == normalize_name("HAROLD LANDRY, II") == "harold landry"
    assert normalize_name("Jay Higgins IV") == normalize_name("Jay Higgins V") == "jay higgins"
    assert normalize_name("Sr Smith") == "sr smith"  # a suffix word is only dropped after the first name
    assert normalize_name("Bjørn Ødegård") == "bjorn odegard"
    assert normalize_name(None) == normalize_name("  ") == ""


def test_precedence_override_then_source_id_then_names():
    players = {
        "1": _p("Will Anderson", "HOU", "DE", espn_id=100),
        "2": _p("Will Anderson", "NYJ", "LB"),
        "3": _p("Bill Anderson", "HOU", "DE"),
    }
    row = {"source_id": "100", "id_field": "espn_id", "name": "Will Anderson Jr.", "team": "NYJ", "slot": "LB"}
    assert Matcher(players, {"espn_proj": {"100": "3"}}).match("espn_proj", **row) == Match("3", "override")
    assert Matcher(players, {"other": {"100": "3"}}).match("espn_proj", **row) == Match("1", "source_id")
    m = Matcher(players)
    assert m.match("espn_proj", **row | {"source_id": "999"}) == Match("2", "name_team_slot")
    assert m.match("espn_proj", **row | {"source_id": None, "slot": "DB"}) == Match("2", "name_team")
    assert m.match("espn_proj", **row | {"source_id": None, "slot": None}) == Match("2", "name_team")
    # two same-name players on other teams: the name-only step refuses
    assert m.match("espn_proj", name="Will Anderson", team="KC", slot="DL") == Match(None, "ambiguous")
    assert m.match("espn_proj", name="Billy Anderson", team="HOU", slot="DL") == Match(None, "unmatched")
    nick = Matcher(players, nicknames={"bill": ["william"], "billy": ["william"]})
    assert nick.match("espn_proj", name="William Anderson", team="HOU", slot="DL") == Match("3", "nickname")
    assert nick.match("espn_proj", name="Will Anderson", team="HOU", slot="DL") == Match("1", "name_team_slot")
    assert [Match(None, h).rank for h in METHODS] == list(range(len(METHODS)))


def test_source_id_must_land_on_an_idp():
    players = {
        "10": _p("Travis Hunter", "JAX", "WR", ["WR"], espn_id=555),
        "11": _p("Travis Hunter", "JAX", "CB", espn_id=556),
        "12": {"full_name": "Duplicate Player", "position": "CB", "team": "JAX", "espn_id": 557},
        "13": _p("Pat Freiermuth", "PIT", "TE", ["TE"], fantasy_data_id=777),
    }
    m = Matcher(players)
    assert m.match("espn_proj", source_id=555, id_field="espn_id", name="Travis Hunter", team="JAX") == Match(
        "11", "name_team"
    )
    assert m.match("espn_proj", source_id="556", id_field="espn_id", name="x", team=None) == Match("11", "source_id")
    assert m.match("espn_proj", source_id="557", id_field="espn_id", name="Nobody", team="JAX").pid is None
    assert m.match("x", source_id="777", id_field="fantasy_data_id", name="Pat Freiermuth", team="PIT") == Match(
        None, "unmatched"
    )
    assert m.match("x", source_id="556", id_field="yahoo_id", name="Travis Hunter", team=None) == Match(None, "no_team")


def test_crosswalk_is_step_three():
    players = {
        "1": _p("Will Anderson", "HOU", "DE", espn_id=100),
        "2": _p("Will Anderson", "NYJ", "LB"),
        "3": _p("Bill Anderson", "HOU", "DE"),
        "4": _p("Travis Hunter", "JAX", "WR", ["WR"]),
    }
    xw = {"fantasypros_id": {"9": "3", "8": "4", "7": "404"}, "espn_id": {"100": "3", "200": "1", "300": "2"}}
    m = Matcher(players, {"fp_ecr": {"9": "2"}}, crosswalk=xw)
    row = {"id_field": "fantasypros_id", "name": "Will Anderson", "team": "NYJ", "slot": "LB"}
    assert m.match("fp_ecr", source_id="9", **row) == Match("2", "override")  # step 1 beats the crosswalk
    assert m.match("other", source_id="9", **row) == Match("3", "source_id")  # crosswalk beats names
    assert m.match("other", source_id="8", **row) == Match("2", "name_team_slot")  # lands on a non-IDP
    assert m.match("other", source_id="7", **row) == Match("2", "name_team_slot")  # not in the players DB
    assert m.match("other", source_id="9", **row | {"id_field": "espn_id"}) == Match("2", "name_team_slot")
    # Sleeper's own espn_id (step 2) wins; the crosswalk can't move an id onto a record carrying another one
    assert m.match("other", source_id="100", **row | {"id_field": "espn_id"}) == Match("1", "source_id")
    assert m.match("other", source_id="200", **row | {"id_field": "espn_id"}) == Match("2", "name_team_slot")
    assert m.match("other", source_id="300", **row | {"id_field": "espn_id", "team": None}) == Match("2", "source_id")
    assert Matcher(players).match("other", source_id="9", **row) == Match("2", "name_team_slot")


def test_name_only_uniqueness_and_slot_guards():
    players = {
        "20": _p("Jordan Smith", "DAL", "LB"),
        "21": _p("Keshawn Banks", "MIN", "LB", ["DL", "LB"]),
        "22": _p("Josh Jones", "ARI", "S"),
        "23": _p("Josh Jones", None, "CB", active=False),
        "24": _p("Jordan Smith", "DAL", "WR", ["WR"]),  # not an IDP: never a name candidate
    }
    m = Matcher(players)
    # a DB row must not take an LB-only player of the same name on another team
    assert m.match("s", name="Jordan Smith", team="SEA", slot="DB") == Match(None, "unmatched")
    assert m.match("s", name="Jordan Smith", team="SEA", slot="LB") == Match("20", "name")
    assert m.match("s", name="Jordan Smith", team="SEA", slot=None) == Match("20", "name")
    assert m.match("s", name="Jordan Smith", team=None, slot="LB") == Match(None, "no_team")
    assert m.match("s", name="Keshawn Banks", team="ATL", slot="DL") == Match("21", "name")  # dual-eligible
    assert m.match("s", name="Keshawn Banks", team="ATL", slot="DB") == Match(None, "unmatched")
    assert m.match("s", name="Josh Jones", team="NYG", slot="DB") == Match(None, "ambiguous")
    assert m.match("s", name="Josh Jones", team="ari", slot="DB") == Match("22", "name_team_slot")
    twins = Matcher({"30": _p("Byron Young", "LAR", "LB"), "31": _p("Byron Young", "LAR", "OLB")})
    assert twins.match("s", name="Byron Young", team="LAR", slot="LB") == Match(None, "ambiguous")
    assert twins.match("s", name="Byron Young", team="LAR", slot="DL") == Match(None, "ambiguous")


def test_nicknames_both_ways_and_flagged(tmp_path):
    nick = load_nicknames(CONFIG / "aliases" / "nicknames.json")
    assert "cameron" in nick["cam"] and "cam" in nick["cameron"] and "cam" in nick["camryn"]
    assert "christopher" in nick["chris"] and "christian" not in nick["christopher"]
    assert load_nicknames(tmp_path / "missing.json") == {}
    players = {
        "40": _p("Camryn Bynum", "IND", "S"),
        "41": _p("Cam Gill", "CAR", "LB"),
        "42": _p("Zach Carter", "ARI", "DT"),
    }
    m = Matcher(players, nicknames=nick)
    assert m.match("s", name="Cam Bynum", team="IND", slot="DB") == Match("40", "nickname")
    assert m.match("s", name="Cameron Gill", team="CAR", slot="DL") == Match("41", "nickname")
    assert m.match("s", name="Zachary Carter", team="ARI", slot="DL") == Match("42", "nickname")
    assert Matcher(players).match("s", name="Cam Bynum", team="IND", slot="DB") == Match(None, "unmatched")


def test_overrides_by_id_and_name_team(tmp_path):
    path = tmp_path / "player_overrides.toml"
    path.write_text('[espn_proj]\n"4034959" = "8297"\n"Jane Doe Jr.|car" = 12345\n"John Roe|" = "777"\n')
    ov = load_overrides(path)
    assert ov == {"espn_proj": {"4034959": "8297", "Jane Doe Jr.|car": "12345", "John Roe|": "777"}}
    assert load_overrides(tmp_path / "missing.toml") == {}
    assert load_overrides(CONFIG / "player_overrides.toml") == {}
    m = Matcher({}, ov)
    assert m.match("espn_proj", source_id=4034959, name="Anyone", team=None) == Match("8297", "override")
    assert m.match("espn_proj", name="Jane Doe", team="CAR", slot="DB") == Match("12345", "override")
    assert m.match("espn_proj", name="John Roe", team=None) == Match("777", "override")
    assert m.match("espn_proj", name="Jane Doe", team="ATL") == Match(None, "unmatched")
    assert m.match("fp_ecr", name="Jane Doe", team="CAR") == Match(None, "unmatched")


def test_report_summary_and_teamed_match_rate():
    rep = MatchReport()
    assert rep.summary()["teamed_match_rate"] is None
    rep.add(Match("1", "source_id"), name="A", team="NE", slot="DL")
    rep.add(Match("2", "source_id"), name="B", team=None, slot="LB")
    rep.add(Match("3", "name"), name="C", team="KC", slot="DB")
    rep.add(Match(None, "no_team"), name="D", team=None, slot="DB")
    rep.add(Match(None, "ambiguous"), name="Zed", team="NYG", slot="DB")
    rep.add(Match(None, "unmatched"), name="Abe", team="SF", slot="DL")
    rep.conflict("1", kept="espn 10 (source_id)", dropped="espn 11 (name)")
    s = rep.summary()
    assert s["by_method"] == {"source_id": 2, "name": 1, "unmatched": 1, "no_team": 1, "ambiguous": 1}
    assert [r["name"] for r in s["unmatched"]] == ["Abe", "Zed"]
    assert s["unmatched"][0] == {"name": "Abe", "team": "SF", "slot": "DL", "how": "unmatched"}
    assert s["conflicts"] == [{"pid": "1", "kept": "espn 10 (source_id)", "dropped": "espn 11 (name)"}]
    assert (s["matched"], s["teamed"]) == (3, 4) and s["teamed_match_rate"] == pytest.approx(0.5)
    for i in range(60):
        rep.add(Match(None, "unmatched"), name=f"P{i:02d}", team="LV", slot="LB")
    assert len(rep.summary()["unmatched"]) == 50 and rep.summary()["by_method"]["unmatched"] == 61


def test_live_espn_sample_matches_reviewed_output():
    """81 live ESPN week-6 rows against every Sleeper record they could reach (2026-10-12 players DB)."""
    fx = json.loads((FIXTURES / "matching_espn_week6_sample.json").read_text())
    m = Matcher(fx["players"], nicknames=load_nicknames(CONFIG / "aliases" / "nicknames.json"))
    rep = MatchReport()
    for r in fx["rows"]:
        team, slot = fx["espn_teams"][str(r["proTeamId"])], fx["espn_positions"].get(str(r["defaultPositionId"]))
        got = m.match("espn_proj", source_id=str(r["id"]), id_field="espn_id", name=r["fullName"], team=team, slot=slot)
        assert got == Match(r["expect"]["pid"], r["expect"]["how"]), r["fullName"]
        rep.add(got, name=r["fullName"], team=team, slot=slot)
    s = rep.summary()
    assert set(s["by_method"]) == {
        "source_id",
        "name_team_slot",
        "name_team",
        "name",
        "nickname",
        "unmatched",
        "no_team",
    }
    assert [r["name"] for r in s["unmatched"]] == ["Jeremiah Moon", "Keshawn Banks", "Victor Dimukeje"]
