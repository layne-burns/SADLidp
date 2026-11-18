"""Kickers (K), unit level: the slot, the source registry, the Sleeper kicker adapter, scoring, the roster
rule, the add-only results merge, the model terms and the kicker group's weights. The end-to-end proof that
none of it moves a DL/LB/DB number is in test_kickers_isolation.py."""

import json
import math
import random
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sadlidp import accuracy, audit, backfill, form, kicker, positions, roster, scoring, storage, swing, sync, weeklog
from sadlidp.config import load_config
from sadlidp.matching import Matcher
from sadlidp.pool import PlayerStatus
from sadlidp.schedule import ET, Schedule
from sadlidp.sleeper import IDP_POSITIONS_QUERY, Sleeper, open_spots, position_limits
from sadlidp.sources import registry, sleeper_k
from sadlidp.sources.base import AdapterResult, FetchContext

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
CFG = load_config(ROOT / "config" / "sadl.toml")
LEAGUE = json.loads((FIXTURES / "sleeper_league.json").read_text())
SC = LEAGUE["scoring_settings"]  # the league's real settings: fgm_yds 0.1, fgmiss -1, xpm 1, xpmiss -1
PROJ = json.loads((FIXTURES / "sleeper_k_projections_week11_trimmed.json").read_text(encoding="utf-8"))
ACTUAL = json.loads((FIXTURES / "sleeper_k_stats_week10_trimmed.json").read_text(encoding="utf-8"))["players"]
SCHED = Schedule.from_csv((FIXTURES / "nflverse_games_2026.csv").read_text(), 2026, {"LA": "LAR"})
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
REAL = {
    "3451",
    "11786",
    "11653",
    "5119",
    "6083",
    "4195",
}  # the fixture's six real rows; 5838 a placeholder, 2650 a punter
KICKER = {"position": "K", "fantasy_positions": ["K"]}


# ---- the slot ----------------------------------------------------------------------------------------------


def test_a_kicker_is_position_k_and_nothing_else():
    assert positions.slot_of(KICKER) == "K"
    assert positions.slot_of({"position": "P", "fantasy_positions": ["K", "P"]}) is None  # a punter never becomes K
    assert positions.slot_of({"position": "WR", "fantasy_positions": ["WR", "K"]}) is None  # no fallback into K
    # Sleeper id 5251: listed "K" with a DL eligibility, but a real DL Sleeper projects every week. Whoever
    # had an IDP slot before kickers keeps it, so no DL number can move because of him
    oddity = {"position": "K", "fantasy_positions": ["DL"], "team": None}
    assert positions.slot_of(oddity) == "DL" and positions.is_idp(oddity) and not positions.is_kicker(oddity)
    assert positions.also_eligible(KICKER) == [] and positions.also_eligible(oddity) == []
    assert positions.is_kicker(KICKER) and not positions.is_idp(KICKER) and positions.idp_slot_of(KICKER) is None
    assert positions.is_idp({"position": "DE"}) and positions.idp_slot_of({"position": "DE"}) == "DL"
    assert not positions.is_idp({"position": "QB"}) and not positions.is_kicker({"position": "QB"})


def test_two_tuples_and_two_groups():
    assert positions.IDP_SLOTS == ("DL", "LB", "DB") and positions.SLOTS == ("DL", "LB", "DB", "K")
    assert positions.GROUPS == {"idp": ("DL", "LB", "DB"), "k": ("K",)}
    assert [positions.group_of(s) for s in ("DL", "LB", "DB", "K", None)] == ["idp", "idp", "idp", "k", "idp"]
    players = {"a": {"position": "LB"}, "k": KICKER, "q": {"position": "QB"}}
    assert set(positions.in_group(players, "k")) == {"k"} and set(positions.in_group(players, "idp")) == {"a", "q"}
    assert positions.EVAL["idp"] == {"pool": 50, "top": 12, "min_cover": 15}
    assert positions.EVAL["k"] == {"pool": 20, "top": 5, "min_cover": 10}


# ---- the registry ------------------------------------------------------------------------------------------


def test_registry_keeps_the_idp_sources_and_declares_the_kicker_ones():
    idp = ["sleeper_proj", "espn_proj", "fp_ecr", "rotoballer", "fantasydata"]
    assert list(sync.SOURCES) == idp and sync.SOURCES is registry.SOURCES and list(sync.SIGNALS) == ["fp_dst"]
    assert registry.names_for("idp") == tuple(idp)
    assert registry.names_for("k") == ("sleeper_k", "espn_k", "cbs_k", "fp_k", "fantasydata_k", "rotoballer_k")
    assert registry.K_SOURCES["sleeper_k"] == (sleeper_k, "projection") and sync.K_SOURCES is registry.K_SOURCES
    assert set(registry.K_SOURCES) <= set(registry.names_for("k"))  # registered = declared and its module exists
    assert registry.sources_for("k") is registry.K_SOURCES and registry.sources_for("idp") is registry.SOURCES
    assert [registry.group_of_source(n) for n in ("fp_k", "cbs_k", "fp_ecr", "fp_dst", "new")] == [
        "k", "k", "idp", "idp", "idp",
    ]  # fmt: skip
    assert registry.of_group({"fp_ecr": 2.0, "fp_k": 2.0, "x": 1}, "k") == {"fp_k": 2.0}
    assert registry.projection_count("idp") == 2 and registry.projection_count("k") == 3
    labels = sync.SOURCE_LABELS  # the IDP part and its order are what other modules sort by
    assert list(labels)[:6] == [*idp, "fp_dst"] and labels["fp_dst"] == "FantasyPros D/ST"
    assert [labels[n] for n in registry.names_for("k")] == [
        "Sleeper", "ESPN", "CBS", "FantasyPros", "FantasyData", "RotoBaller",
    ]  # fmt: skip
    assert sync.warn_label("espn_k") == "ESPN kickers" and sync.warn_label("espn_proj") == "ESPN"
    for name in registry.names_for("k"):  # every declared kicker source has its config table
        assert name in CFG["sources"], name
    assert CFG["accuracy"]["untrusted"] == ["sleeper_k", "espn_k", "fp_k", "rotoballer_k"]


# ---- the Sleeper kicker adapter ----------------------------------------------------------------------------


class FakeSleeper:
    def __init__(self, payload, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def kicker_projections(self, season, week):
        self.calls.append((season, week))
        return self.payload, self.error

    def projections(self, season, week):
        raise AssertionError("the kicker adapter must never send the IDP request")


def _fetch(payload, players=None, week=11, error=None):
    known = {r["player_id"]: r["player"] for r in PROJ if r["player"]["position"] == "K"}
    ctx = FetchContext(None, FakeSleeper(payload, error), 2026, week, NOW, known if players is None else players, {})
    return sleeper_k.fetch(ctx)


def test_sleeper_k_keeps_kickers_derives_fgmiss_and_marks_it():
    res = _fetch(PROJ)
    assert res.ok and (res.name, res.kind, res.week) == ("sleeper_k", "projection", 11)
    assert set(res.rows) == REAL and res.no_projection_ids == {"5838"}  # the punter (2650) is in neither
    assert res.universe == {"K": {"complete": True, "depth": None}}
    row = res.rows["3451"]
    assert (row["team"], row["opponent"], row["imputed"]) == ("HOU", "IND", ["fgmiss"])
    line = row["stats"]
    assert line["fgmiss"] == 0.36 and (line["fga"], line["fgm"], line["fgm_yds"]) == (2.72, 2.36, 82.31)
    assert not [k for k in line if k.startswith(("pts_", "adp_", "pos_adp_"))] and line["fgmiss_50p"] == 0.13
    # the league scores only fgmiss, so the derived key is what makes the projection count his misses
    assert scoring.projected_points(line, SC) == pytest.approx(0.1 * 82.31 - 0.36 + 2.79 - 0.07)
    assert {"fga", "fgm", "fgm_yds", "xpm", "fgm_50p"} <= res.covered_keys and "fgmiss" not in res.covered_keys
    for r in res.rows.values():  # every real row: misses = attempts − makes, never negative
        s = r["stats"]
        assert s["fgmiss"] == round(max(0.0, s["fga"] - s["fgm"]), 4) and r["imputed"] == ["fgmiss"]


def test_sleeper_k_position_comes_from_the_players_db_then_the_row():
    assert set(_fetch(PROJ, players={}).rows) == REAL  # nobody known: the row's own position (the punter still out)
    db = {r["player_id"]: {"position": "LB"} for r in PROJ}
    assert _fetch(PROJ, players={**db, "3451": KICKER}).rows.keys() == {"3451"}  # the players DB wins
    published = [{**PROJ[0], "stats": {**PROJ[0]["stats"], "fgmiss": 0.2}}]
    kept = _fetch(published).rows["3451"]
    assert kept["stats"]["fgmiss"] == 0.2 and "imputed" not in kept  # a published fgmiss is never overwritten
    over = [{**PROJ[0], "stats": {**PROJ[0]["stats"], "fga": 1.0, "fgm": 1.5}}]
    assert _fetch(over).rows["3451"]["stats"]["fgmiss"] == 0.0


def test_sleeper_k_statuses():
    placeholders = [r for r in PROJ if r["player_id"] in ("5838", "2650")]
    res = _fetch(placeholders)
    assert res.status == "suspect" and "placeholder" in res.error and not res.rows
    idp_payload = [{**PROJ[0], "player_id": "x", "player": {"position": "LB", "fantasy_positions": ["LB"]}}]
    res = _fetch(idp_payload, players={})
    assert res.status == "suspect" and res.error == "no kicker rows in the response"
    res = _fetch(PROJ, week=12)
    assert (res.status, res.week) == ("not_yet_published", 11)
    assert _fetch(None, error="HTTP 503").status == "failed" and _fetch({"oops": 1}).status == "parse_error"


def test_the_kicker_request_is_its_own_and_the_idp_query_is_unchanged():
    urls = []

    class Client:
        def get(self, url, **kw):
            urls.append(url)
            return types.SimpleNamespace(ok=True, status=200, error=None, json=lambda: [])

    s = Sleeper(Client())
    s.kicker_projections(2026, 11)
    s.projections(2026, 11)
    assert urls[0].endswith("/projections/nfl/2026/11?season_type=regular&position[]=K")
    assert urls[1].endswith(f"/projections/nfl/2026/11?season_type=regular&{IDP_POSITIONS_QUERY}")
    assert IDP_POSITIONS_QUERY == "position[]=DL&position[]=LB&position[]=DB"


# ---- scoring -----------------------------------------------------------------------------------------------


def test_kicker_points_from_real_stat_lines():
    """0.1 per made yard − 1 per missed field goal + 1 per extra point − 1 per missed extra point, nothing else."""
    scored = 0
    for pid, p in ACTUAL.items():
        line = p["stats"]
        if not line.get("gp"):
            assert weeklog.stat_result(line, SC, final=True) == {"pts": None, "state": "dnp"}, pid  # active, no game
            continue
        want = 0.1 * line.get("fgm_yds", 0) - line.get("fgmiss", 0) + line.get("xpm", 0) - line.get("xpmiss", 0)
        assert scoring.points(line, SC) == pytest.approx(want), pid
        assert weeklog.stat_result(line, SC, final=True) == {"pts": round(want, 2), "state": "final"}
        scored += 1
    assert scored == 4
    assert scoring.points(ACTUAL["11786"]["stats"], SC) == pytest.approx(21.0)  # 170 yards and 4 extra points
    assert scoring.points(ACTUAL["4233"]["stats"], SC) == pytest.approx(
        0.1 * 53 - 2 + ACTUAL["4233"]["stats"]["xpm"] - 1
    )
    assert scoring.points(ACTUAL["13833"]["stats"], SC) == pytest.approx(2.0)  # 3 extra points, 1 miss, no make


def test_breakdown_lists_a_kickers_scored_line_and_only_his_groups_sources():
    row = _fetch(PROJ).rows["3451"]
    pts = scoring.projected_points(row["stats"], SC)
    info = {"sleeper_k": {"last_success": "x", "rows_used": 6}, "sleeper_proj": {"last_success": "x", "rows_used": 9}}
    out = sync.breakdown("3451", {"mu": pts, "eff": {"sleeper_k": 1.0}}, {"sleeper_k": {"3451": row}},
                         {"sleeper_k": {"3451": pts}}, {"sleeper_k": "projection", "sleeper_proj": "projection"},
                         info, SC, names=sync.K_SOURCES)  # fmt: skip
    assert [p["source"] for p in out["parts"]] == ["Sleeper"] and out["est"] is True
    assert out["parts"][0]["stats"] == [
        ["FG yards", 82.31, False], ["XP", 2.79, False], ["missed FG", 0.36, True], ["missed XP", 0.07, False],
    ]  # fmt: skip
    idp = sync.breakdown("3451", None, {}, {}, {"sleeper_proj": "projection"}, info, SC)  # default: the IDP sources
    assert [p["source"] for p in idp["parts"]] == ["Sleeper"] and "missing" in idp["parts"][0]


# ---- the roster rule ---------------------------------------------------------------------------------------

K_PLAYERS = {pid: KICKER for pid in ("ks", "kb", "w1", "w2")} | {"lb": {"position": "LB"}, "wl": {"position": "LB"}}
K_CONS = {pid: {"mu": mu, "sigma": 1.0} for pid, mu in {"ks": 8.0, "kb": 6.0, "w1": 14.0, "w2": 8.5}.items()}
K_CONS |= {"lb": {"mu": 5.0, "sigma": 1.0}, "wl": {"mu": 12.0, "sigma": 1.0}}
LOCKED = PlayerStatus(reasons=["locked"])


def _kpanel(mine, spots, statuses=None, limits=None, lineup=None):
    st = {pid: PlayerStatus() for pid in K_PLAYERS} | (statuses or {})
    waiver, limits = {"K": ["w1", "w2"], "LB": ["wl"]}, limits or {"DL": 2, "LB": 2, "DB": 2}
    return roster.panel(
        {"players": mine}, K_PLAYERS, K_CONS, st, waiver, limits, {"K": 2.0, "LB": 2.0}, 0.6, lineup or {}, spots
    )


def test_kicker_add_with_roster_room_and_no_position_limit():
    k = _kpanel(["ks"], 2)["K"]
    assert (k["starter"], k["other"], k["count"], k["limit"], k["open_spots"]) == ("ks", None, 1, None, 2)
    assert k["badges"]["w1"] == {"badge": "START", "over": "ks", "p": k["badges"]["w1"]["p"], "action": "add",
                                 "drop": None, "reference": False, "why": []}  # fmt: skip
    assert "w2" not in k["badges"]  # 8.5 against 8.0: not a likely enough upgrade on the starter
    two = _kpanel(["ks", "kb"], 1)["K"]  # two kickers and still room: nothing has to go
    assert two["count"] == 2 and two["badges"]["w1"]["action"] == "add"
    assert (two["badges"]["w2"]["badge"], two["badges"]["w2"]["action"], two["badges"]["w2"]["drop"]) == (
        "UPGRADE", "add", None,
    )  # fmt: skip
    assert _kpanel(["ks"], None)["K"]["badges"]["w1"]["action"] == "add"  # roster room unknown: taken as room


def test_kicker_add_drop_swap_and_choose_drop_when_the_roster_is_full():
    two = _kpanel(["ks", "kb"], 0)["K"]["badges"]
    assert (two["w1"]["action"], two["w1"]["drop"], two["w1"]["over"]) == ("add_drop", "kb", "ks")
    assert (two["w2"]["badge"], two["w2"]["action"], two["w2"]["drop"]) == ("UPGRADE", "add_drop", "kb")
    one = _kpanel(["ks"], 0)["K"]["badges"]["w1"]
    assert (one["action"], one["drop"], one["over"], one["reference"]) == ("swap", "ks", "ks", False)  # a straight swap
    bye = _kpanel(["ks"], 0, {"ks": PlayerStatus(reasons=["bye"])})["K"]
    assert bye["starter"] is None and bye["count"] == 1
    assert (bye["badges"]["w2"]["action"], bye["badges"]["w2"]["drop"], bye["badges"]["w2"]["over"]) == (
        "choose_drop", None, None,
    )  # fmt: skip
    none = _kpanel([], 0)["K"]
    assert none["count"] == 0 and none["badges"]["w1"]["action"] == "choose_drop"
    # the drop can come from anywhere on the roster, so a kicker's choose_drop is never "drop locked"
    locked_bye = _kpanel(["ks"], 0, {"ks": PlayerStatus(reasons=["bye", "locked"])})["K"]["badges"]["w1"]
    assert locked_bye["action"] == "choose_drop" and locked_bye["why"] == [] and locked_bye["reference"] is False


def test_kicker_reference_reasons_and_a_league_that_does_set_a_limit():
    played = _kpanel(["ks"], 0, {"ks": LOCKED}, lineup={"K": "ks"})["K"]
    assert played["starter_locked"] and played["badges"]["w1"]["action"] == "swap"
    assert played["badges"]["w1"]["why"] == ["starter_locked", "drop_locked"] and played["badges"]["w1"]["reference"]
    other_played = _kpanel(["ks", "kb"], 0, {"kb": LOCKED}, lineup={"K": "ks"})["K"]["badges"]["w1"]
    assert (other_played["action"], other_played["why"]) == ("add_drop", ["drop_locked"])
    limited = _kpanel(["ks"], 3, limits={"DL": 2, "LB": 2, "DB": 2, "K": 1})["K"]
    assert limited["limit"] == 1 and limited["badges"]["w1"]["action"] == "swap"  # room, but at the league's K limit


def test_idp_panel_ignores_roster_room_and_k_can_be_left_out():
    full, room = _kpanel(["lb"], 0), _kpanel(["lb"], 5)
    assert full["LB"] == room["LB"] and full["LB"]["badges"]["wl"]["action"] == "add" and "open_spots" not in full["LB"]
    st = {pid: PlayerStatus() for pid in K_PLAYERS}
    idp_only = roster.panel({"players": ["lb"]}, K_PLAYERS, K_CONS, st, {"LB": ["wl"]}, {"LB": 2}, {"LB": 2.0}, 0.6,
                            {}, slots=positions.IDP_SLOTS)  # fmt: skip
    assert set(idp_only) == {"DL", "LB", "DB"} and idp_only["LB"] == full["LB"]
    lineup = roster.starter_slots(LEAGUE["roster_positions"], [str(i) for i in range(len(LEAGUE["roster_positions"]))])
    assert lineup == {"K": "7", "DL": "8", "LB": "9", "DB": "10"}


def test_limits_have_no_k_and_open_spots_counts_active_players():
    assert position_limits(LEAGUE["settings"], 2) == {"DL": 2, "LB": 2, "DB": 2}  # no position_limit_k: no K entry
    assert position_limits({}, 2) == {"DL": 2, "LB": 2, "DB": 2}
    assert position_limits({**LEAGUE["settings"], "position_limit_k": 1}, 2)["K"] == 1
    me = json.loads((FIXTURES / "sleeper_rosters.json").read_text())[0]
    active = len(me["players"]) - len(me["reserve"]) - len(me["taxi"])
    assert open_spots(me, LEAGUE["roster_positions"]) == len(LEAGUE["roster_positions"]) - active == 2
    assert open_spots({"players": [str(i) for i in range(40)]}, LEAGUE["roster_positions"]) == 0  # never negative
    assert open_spots(None, LEAGUE["roster_positions"]) is None and open_spots(me, None) is None


# ---- past weeks' results: the add-only merge ----------------------------------------------------------------


class StatsSleeper:
    def __init__(self, weekly):
        self.weekly, self.calls = weekly, []

    def stats(self, season, week):
        self.calls.append(week)
        payload = self.weekly.get(week)
        return (payload, None) if payload is not None else (None, "HTTP 503")


def test_settled_weeks_gain_kicker_lines_once_and_nothing_else_changes(tmp_path):
    players = {"a": {"position": "LB"}, "b": {"position": "DE"}, "k1": KICKER, "k2": KICKER,
               "p1": {"position": "P", "fantasy_positions": ["K", "P"]}}  # fmt: skip
    now = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
    final, still_open = "2026-11-01T12:00:00+00:00", "2026-09-10T12:00:00+00:00"
    saved = {"a": {"gp": 1, "idp_tkl_solo": 5}, "k1": {"gp": 1, "xpm": 2}}
    docs = {1: {"week": 1, "captured_at": final, "stats": saved},
            2: {"week": 2, "captured_at": final, "stats": dict(saved)},
            3: {"week": 3, "captured_at": final, "stats": dict(saved), "k_added_at": "2026-11-16T00:00:00+00:00"},
            4: {"week": 4, "captured_at": still_open, "stats": dict(saved)}}  # fmt: skip
    assert datetime.fromisoformat(still_open) < SCHED.settles_at(4) <= datetime.fromisoformat(final)
    for week, doc in docs.items():
        storage.write_json(storage.week_dir(tmp_path, 2026, week) / "actuals.json", doc)
    payload = {"a": {"gp": 1, "idp_tkl_solo": 99}, "b": {"gp": 1, "idp_sack": 1}, "k1": {"gp": 1, "xpm": 99},
               "k2": {"gp": 1, "fgm_yds": 104, "xpm": 2, "xpmiss": 1}, "p1": {"gp": 1, "punts": 4},
               "TEAM_PIT": {"fgm_yds": 104, "xpm": 2, "gp": 1}, "PIT": {"gp": 1}}  # fmt: skip
    sleeper = StatsSleeper({1: payload, 2: None})
    done = kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, players)
    assert done == [1] and sleeper.calls == [2, 1]  # newest first; week 3 is marked, week 4 open, week 5+ unsaved
    doc = storage.read_json(storage.week_dir(tmp_path, 2026, 1) / "actuals.json")
    assert doc["stats"] == {
        **saved,
        "k2": payload["k2"],
    }  # one kicker added: no line changed, no IDP, punter or team row
    assert (doc["k_added_at"], doc["k_added"], doc["captured_at"], doc["week"]) == (now.isoformat(), 1, final, 1)
    assert scoring.points(doc["stats"]["k2"], SC) == pytest.approx(11.4)
    assert "k_added_at" not in storage.read_json(storage.week_dir(tmp_path, 2026, 2) / "actuals.json")  # fetch failed
    assert kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, players) == [] and sleeper.calls == [2, 1, 2]
    sleeper.weekly[2] = {"a": {"gp": 1}}  # a week with no kicker line at all is still marked, and never asked again
    assert kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, players) == [2]
    assert storage.read_json(storage.week_dir(tmp_path, 2026, 2) / "actuals.json")["k_added"] == 0
    assert kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, players) == [] and len(sleeper.calls) == 4


def test_at_most_a_few_settled_weeks_a_sync(tmp_path):
    now = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
    for week in range(1, 7):
        doc = {"week": week, "captured_at": "2026-11-16T12:00:00+00:00", "stats": {}}
        storage.write_json(storage.week_dir(tmp_path, 2026, week) / "actuals.json", doc)
    sleeper = StatsSleeper({w: {"k1": {"gp": 1, "xpm": w}} for w in range(1, 7)})
    assert kicker.MAX_ADDED_WEEKS == 4
    assert kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, {"k1": KICKER}) == [6, 5, 4, 3]
    assert kicker.add_past_actuals(sleeper, tmp_path, 2026, SCHED, now, {"k1": KICKER}) == [2, 1]


def test_last_weeks_recap_keeps_the_idp_tally_and_counts_kicker_calls_apart(tmp_path):
    def badge(slot, w, over):
        return {"slot": slot, "w": w, "over": over, "badge": "START", "p": 0.7, "reference": False, "why": [],
                "mu_w": 9.0, "mu_over": 5.0, "active": True}  # fmt: skip

    stats = {"dw": {"gp": 1, "idp_sack": 2}, "ds": {"gp": 1, "idp_sack": 1},
             "kw": {"gp": 1, "xpm": 1}, "ks": {"gp": 1, "fgm_yds": 104, "xpm": 2}}  # fmt: skip
    wd = storage.week_dir(tmp_path, 2026, 5)
    idp_only = {"dw|START|ds": badge("DL", "dw", "ds")}
    storage.write_json(wd / "actuals.json", {"week": 5, "captured_at": "2026-11-01T00:00:00+00:00", "stats": stats})
    storage.write_json(wd / "badges.json", {"badges": idp_only})
    players = {"dw": {"position": "DE"}, "ds": {"position": "DE"}, "kw": KICKER, "ks": KICKER}
    before = sync.last_week_recap(tmp_path, 2026, 6, SCHED, SC, players)
    assert set(before["slots"]) == {"DL", "LB", "DB"} and "summary_k" not in before  # no kicker call: the old shape
    assert before["summary"] == {"hits": 1, "decided": 1}
    storage.write_json(wd / "badges.json", {"badges": {**idp_only, "kw|START|ks": badge("K", "kw", "ks")}})
    recap = sync.last_week_recap(tmp_path, 2026, 6, SCHED, SC, players)
    assert recap["summary"] == before["summary"] and recap["slots"]["DL"] == before["slots"]["DL"]  # never pooled
    assert recap["summary_k"] == {"hits": 0, "decided": 1} and recap["slots"]["K"][0]["outcome"] == "miss"
    assert recap["slots"]["K"][0]["over_result"] == {"pts": 12.4, "state": "final"}


# ---- model terms -------------------------------------------------------------------------------------------

COEF = {"a": {"DL": 0.0, "LB": 18.0, "DB": 27.0, "K": 0.0}, "b": {"DL": 2.5, "LB": 1.35, "DB": 0.65, "K": 1.0},
        "lo": -0.8, "hi": 0.75}  # fmt: skip
K_ROWS = {
    "sleeper_k": {"k": {"stats": {"fga": 2.0, "fgm": 1.6, "fgm_yds": 60.0, "fgmiss": 0.4, "xpm": 2.0, "xpmiss": 0.1,
                                  "fgm_20_29": 0.6, "fgm_30_39": 1.0, "gp": 1.0, "idp_tkl_solo": 9.0}}},
    "espn_k": {"k": {"stats": {"fga": 3.0, "fgm": 2.4, "fgm_yds": 100.0, "fgmiss": 0.6, "xpm": 3.0,
                               "fgm_0_39": 1.4, "fgm_40_49": 1.0}}, "z": None},
    "fp_k": {"k": {"ranks": {"K": 3}}},
}  # fmt: skip
K_KINDS = {"sleeper_k": "projection", "espn_k": "projection", "fp_k": "rank"}


def test_mix_averages_a_kickers_kicking_line_and_nothing_else():
    lam = swing.mix("k", K_ROWS, K_KINDS, SC, {"sleeper_k": 0.3, "espn_k": 0.3, "fp_k": 0.4}, "K")
    assert lam == pytest.approx({"fga": 2.5, "fgm": 2.0, "fgm_yds": 80.0, "fgmiss": 0.5, "xpm": 2.5, "xpmiss": 0.05,
                                 "fgm_0_39": 0.7, "fgm_20_29": 0.3, "fgm_30_39": 0.5, "fgm_40_49": 0.5})  # fmt: skip
    assert "xpa" not in lam and "gp" not in lam and not [k for k in lam if k.startswith("idp_")]  # only what's given
    buckets = sum(v for k, v in lam.items() if k.startswith("fgm_") and k != "fgm_yds")
    assert buckets == pytest.approx(lam["fgm"])  # sources cut the buckets differently; mixed, they still add up
    assert swing.mix("k", K_ROWS, K_KINDS, SC, {"sleeper_k": 1.0}, "K")["fgm_yds"] == 60.0
    assert swing.mix("z", K_ROWS, K_KINDS, SC, slot="K") is None
    assert set(swing.mix("k", K_ROWS, K_KINDS, SC)) == set(scoring.idp_keys(SC))  # no slot given: the IDP keys


def test_a_kicker_without_a_variance_keeps_sigma_out_k(monkeypatch):
    monkeypatch.setattr(swing, "kicker_var", lambda line, sc, yards=None: None)
    cons = {"k": {"mu": 9.0, "sigma": 1.0, "eff": {"sleeper_k": 0.5, "espn_k": 0.5}}, "z": {"mu": 0.0, "sigma": 1.0}}
    info = swing.apply(cons, {"k": "K", "z": "K"}, K_ROWS, K_KINDS, SC, COEF, ("K",))
    assert info == {"players": 0, "mean": {"K": None}, "no_var": 1}
    assert not {"swing", "floor", "ceiling"} & cons["k"].keys()  # no zero-width range, no σ = 0 odds
    assert swing.kicker_sigma({"fgm": 2.0}, COEF, SC) is None
    players = {"s": KICKER, "w": KICKER}
    pcons = {"s": {"mu": 8.0, "sigma": 1.0}, "w": {"mu": 9.0, "sigma": 1.0}}
    st = {pid: PlayerStatus() for pid in players}
    out = roster.panel({"players": ["s"]}, players, pcons, st, {"K": ["w"]}, {}, {"K": 5.0}, 0.5, {}, 1)
    spread = math.sqrt(1 + 25)  # each side: σ_μ² + σ_out,K²
    assert out["K"]["badges"]["w"]["p"] == round(roster.p_outscores(9, spread, 8, spread), 3) == 0.555
    assert kicker.one_point_edge(5.0) == pytest.approx(0.5562, abs=1e-4) and kicker.one_point_edge(None) is None


def test_a_kicker_variance_becomes_his_swing_when_the_model_gives_one(monkeypatch):
    seen = []

    def fake(line, sc, yards=None):
        seen.append((line, sc, yards))
        return 16.0

    monkeypatch.setattr(swing, "kicker_var", fake)
    cons = {"k": {"mu": 9.0, "sigma": 1.0, "eff": {"sleeper_k": 0.5, "espn_k": 0.5}}}
    yards = kicker.yards_per_make(CFG)
    info = swing.apply(cons, {"k": "K"}, K_ROWS, K_KINDS, SC, COEF, ("K",), yards)
    assert info == {"players": 1, "mean": {"K": 4.0}}  # a_K = 0, b_K = 1: swing² = the variance
    assert (cons["k"]["swing"], cons["k"]["floor"], cons["k"]["ceiling"]) == (4.0, 5.8, 12.0)
    line, sc, got = seen[0]
    assert line["fgm_yds"] == 80.0 and sc is SC and got == yards == {
        "0_19": 19.0, "20_29": 25.4, "30_39": 34.4, "40_49": 44.4, "50p": 53.9,
    }  # fmt: skip
    priors = {**COEF, "a": {**COEF["a"], "K": 9.0}}
    assert swing.kicker_sigma({"fgm": 2.0}, priors, SC) == 5.0
    for bad in (0.0, -4.0, float("nan")):  # never a zero-width range: no usable variance, no swing
        monkeypatch.setattr(swing, "kicker_var", lambda line, sc, yards=None, bad=bad: bad)
        assert swing.kicker_sigma({"fgm": 2.0}, COEF, SC) is None


def test_kicker_var_takes_the_line_the_scoring_and_the_yards_table():
    """Whatever the model says, it is a variance or None; the pipeline must work with either."""
    line = swing.mix("k", K_ROWS, K_KINDS, SC, {"sleeper_k": 1.0}, "K")
    v = swing.kicker_var(line, SC, kicker.yards_per_make(CFG))
    assert v is None or (isinstance(v, float | int) and v >= 0)
    assert swing.kicker_var({}, SC) is None or swing.kicker_var({}, SC) >= 0
    cfg = swing.coefficients(CFG)
    assert (cfg["a"]["K"], cfg["b"]["K"]) == (0.0, 1.0) and CFG["sigma_out"]["K"] == 5.0
    assert CFG["tiers"]["sigma_floor"]["K"] == 0.75 and CFG["net"]["host_interval_s"]["www.cbssports.com"] == 3.0
    assert kicker.yards_per_make({}) == kicker.YARDS_PER_MAKE == kicker.yards_per_make({"kicker": CFG["kicker"]})


def test_form_never_touches_a_kicker():
    coef = form.coefficients(CFG)
    assert set(coef["miss"]) == set(coef["snap"]) == {"DL", "LB", "DB"}
    cons = {"k": {"mu": 9.0, "sigma": 1.0}, "d": {"mu": 9.0, "sigma": 1.0}}
    hist = {pid: {"misses": [4.0, 4.0, 4.0, 4.0], "lsnap": 0.0, "last": 4} for pid in cons}
    out = form.apply(cons, {"k": "K", "d": "DL"}, hist, coef)
    assert cons["k"] == {"mu": 9.0, "sigma": 1.0, "base": 9.0} and "form" in cons["d"]
    assert out["players"]["miss"] == 1 and set(out["snap_avg"]) == {"DL", "LB", "DB"}


def test_sigma_out_k_comes_from_the_config_when_the_cache_lacks_it(tmp_path):
    cache = {"DL": 5.681, "LB": 5.625, "DB": 5.228, "players": {"DL": 90, "LB": 80, "DB": 120}, "season": 2025}
    path = tmp_path / "2025" / "sigma_out.json"
    storage.write_json(path, cache)
    before = path.read_text()
    out = sync.sigma_out_for(None, CFG, tmp_path, 2026, {}, SC, [])  # no Sleeper call: the cache is never rebuilt for K
    assert out == {"DL": 5.681, "LB": 5.625, "DB": 5.228, "K": 5.0} and path.read_text() == before
    storage.write_json(path, {**cache, "K": 4.9})  # a cache built with kickers is used
    assert sync.sigma_out_for(None, CFG, tmp_path, 2026, {}, SC, [])["K"] == 4.9


# ---- matching and backfill: each source sees its own group ----------------------------------------------------


def test_one_matcher_per_group():
    players = {"i": {"full_name": "Sam Smith", "position": "LB", "team": "DAL", "espn_id": 77},
               "k": {"full_name": "Sam Smith", "position": "K", "team": "DAL", "espn_id": 88}}  # fmt: skip
    idp, kick = Matcher(players), Matcher(players, slots=("K",))
    assert idp.match("x", name="Sam Smith", team="DAL").pid == "i"  # a kicker's name can't make an IDP row ambiguous
    assert kick.match("x", name="Sam Smith", team="DAL", slot="K").pid == "k"
    assert idp.match("x", source_id="88", id_field="espn_id", name="Nobody", team="DAL").pid is None
    assert kick.match("x", source_id="88", id_field="espn_id").pid == "k"
    assert sync.build_matcher(CFG, players, group="k").slots == ("K",) and sync.build_matcher(CFG, players).slots == (
        "DL", "LB", "DB",
    )  # fmt: skip


def test_backfill_hands_each_source_its_own_groups_players(tmp_path):
    seen = {}

    def module(name):
        def fetch(ctx):
            seen[name] = ctx
            rows = {pid: {"stats": {"gp": 1.0}} for pid in ctx.players}
            return AdapterResult(name=name, kind="projection", status="ok", week=ctx.week, fetched_at="t", rows=rows)

        return types.SimpleNamespace(NAME=name, HOST="h", fetch=fetch)

    players = {"a": {"position": "LB", "team": "DAL"}, "k1": {**KICKER, "team": "DAL"}}
    sources = {"sleeper_proj": (module("sleeper_proj"), "projection"), "sleeper_k": (module("sleeper_k"), "projection")}
    done = backfill.run(sources, CFG, tmp_path, 2026, [1], NOW, client=None, sleeper=None, players=players,
                        matcher="idp matcher", matchers={"k": "kicker matcher"}, log=lambda m: None)  # fmt: skip
    assert done == {"sleeper_proj": [1], "sleeper_k": [1]}
    idp, kick = seen["sleeper_proj"], seen["sleeper_k"]
    assert set(idp.players) == {"a"} and idp.matcher == "idp matcher" and idp.imputer is not None
    assert set(kick.players) == {"k1"} and kick.matcher == "kicker matcher" and kick.imputer is None  # never imputed
    assert kick.cfg["kicker"]["yards_per_make"]["40_49"] == 44.4 and "kicker" not in idp.cfg and kick.archive
    rows = storage.load_source(storage.week_dir(tmp_path, 2026, 1) / "sources" / "sleeper_k.json")["rows"]
    assert rows == {"k1": {"stats": {"gp": 1.0}, "backfill": True}}


# ---- weights: measured like IDP, kept apart -------------------------------------------------------------------


def _world(tmp_path, kickers=30, weeks=(1, 2, 3, 4), with_k=True, backfilled=False):
    """A season of both groups: 60 LBs (Sleeper ±1, ESPN ±6) and `kickers` kickers (sleeper_k ±0.5, espn_k ±3)."""
    data = tmp_path / "data"
    (data / "2026").mkdir(parents=True)
    (data / "2026" / "league.json").write_text(json.dumps({"scoring_settings": {"idp_tkl_solo": 1.0, "xpm": 1.0}}))
    sched = json.loads((ROOT / "data" / "2026" / "schedule.json").read_text())
    (data / "2026" / "schedule.json").write_text(json.dumps(sched))
    lbs = {f"p{i}": {"position": "LB", "team": "NYJ"} for i in range(60)}
    ks = {f"k{i}": {"position": "K", "team": "NYJ"} for i in range(kickers)}
    rng, krng = random.Random(7), random.Random(11)  # separate streams: the IDP numbers don't depend on `with_k`
    for week in weeks:
        wd = data / "2026" / f"week{week:02d}"
        (wd / "sources").mkdir(parents=True)
        truth = {pid: 4 + rng.randint(0, 12) for pid in lbs}
        for name, sd in {"sleeper_proj": 1.0, "espn_proj": 6.0}.items():
            rows = {pid: {"stats": {"idp_tkl_solo": max(0.0, t + rng.gauss(0, sd))}} for pid, t in truth.items()}
            (wd / "sources" / f"{name}.json").write_text(json.dumps({"rows": rows}))
        stats = {pid: {"gp": 1, "idp_tkl_solo": t} for pid, t in truth.items()}
        if with_k:
            ktruth = {pid: 2 + krng.randint(0, 9) for pid in ks}
            for name, sd in {"sleeper_k": 0.5, "espn_k": 3.0}.items():
                rows = {pid: {"stats": {"xpm": max(0.0, t + krng.gauss(0, sd))}, **({"backfill": True} if backfilled
                              else {})} for pid, t in ktruth.items()}  # fmt: skip
                (wd / "sources" / f"{name}.json").write_text(json.dumps({"rows": rows}))
            stats |= {pid: {"gp": 1, "xpm": t} for pid, t in ktruth.items()}
        (wd / "actuals.json").write_text(json.dumps({"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats}))
        (wd / "players.json").write_text(json.dumps({"players": {**lbs, **(ks if with_k else {})}}))
    return data, Schedule.from_json(sched)


TRUSTED = {**CFG, "accuracy": {"untrusted": []}}
LATE = datetime(2026, 12, 1, 12, tzinfo=ET)


def test_kicker_sources_are_measured_on_kickers_with_their_own_pool(tmp_path):
    data, sched = _world(tmp_path)
    m = accuracy.measure(data, 2026, 4, sched, CFG, group="k")
    assert m["from_weeks"] == [1, 2, 3, 4] and set(m["sources"]) == {"sleeper_k", "espn_k"}
    assert m["sources"]["espn_k"]["mean_r"] > m["sources"]["sleeper_k"]["mean_r"]
    assert m["pair"] is None  # two kicker files, but the group declares three projection sources: the ratio rule
    rec = accuracy.weights(m, CFG, "k")
    assert rec["method"] == "accuracy" and rec["sources"]["sleeper_k"]["w"] > 1 > rec["sources"]["espn_k"]["w"]
    assert "projection_rule" not in rec and rec["priors"] == {"fp_k": 2.0, "rotoballer_k": 1.0, "fantasydata_k": 1.0}
    assert accuracy.source_weights(rec, CFG, "k").keys() == {
        "fp_k",
        "rotoballer_k",
        "fantasydata_k",
        "sleeper_k",
        "espn_k",
    }
    few, sched = _world(tmp_path / "few", kickers=12)  # 12 covered kickers count (≥ 10); 12 IDPs never would (≥ 15)
    assert accuracy.measure(few, 2026, 4, sched, CFG, group="k")["from_weeks"] == [1, 2, 3, 4]
    thin, sched = _world(tmp_path / "thin", kickers=9)
    assert accuracy.measure(thin, 2026, 4, sched, CFG, group="k")["from_weeks"] == []


def test_the_idp_measure_never_sees_a_kicker_file_and_the_other_way_round(tmp_path):
    both, sched = _world(tmp_path / "both")
    alone, _ = _world(tmp_path / "alone", with_k=False)
    assert accuracy.measure(both, 2026, 4, sched, CFG) == accuracy.measure(alone, 2026, 4, sched, CFG)
    rec = accuracy.weights(accuracy.measure(both, 2026, 4, sched, CFG), CFG)
    assert set(rec["sources"]) == {"sleeper_proj", "espn_proj"} and rec["projection_rule"]["rule"] == "ls"
    assert rec["priors"] == {"fp_ecr": 2.0, "rotoballer": 1.0, "fantasydata": 1.0}  # no kicker prior in the IDP record
    assert accuracy.prior_weights(CFG) == rec["priors"] == accuracy.source_weights({"sources": {}}, CFG)
    assert accuracy.measure(alone, 2026, 4, sched, CFG, group="k")["sources"] == {}
    preds, rows, kinds, mean = accuracy.source_points(both / "2026" / "week01", {"idp_tkl_solo": 1.0, "xpm": 1.0}, {})
    assert list(preds) == ["sleeper_proj", "espn_proj"] and not [pid for pid in mean if pid.startswith("k")]
    preds, *_ = accuracy.source_points(both / "2026" / "week01", {"xpm": 1.0}, {}, group="k")
    assert list(preds) == ["sleeper_k", "espn_k"]


def test_backfilled_kicker_rows_wait_for_the_archive_check(tmp_path):
    data, sched = _world(tmp_path, backfilled=True)
    assert (
        accuracy.measure(data, 2026, 4, sched, CFG, group="k")["from_weeks"] == []
    )  # untrusted: archive rows don't count
    assert accuracy.measure(data, 2026, 4, sched, TRUSTED, group="k")["from_weeks"] == [1, 2, 3, 4]
    assert accuracy.weights(accuracy.measure(data, 2026, 4, sched, CFG, group="k"), CFG, "k")["method"] == "prior"


def test_weights_json_gains_one_key_and_the_idp_record_is_untouched(tmp_path):
    data, sched = _world(tmp_path)
    path = data / "2026" / "week06" / "weights.json"
    assert accuracy.in_force_k(data, 2026, 6, sched, CFG, LATE)["method"] == "accuracy" and not path.exists()
    first = accuracy.in_force(data, 2026, 6, sched, CFG, LATE)
    before = json.loads(path.read_text())
    assert before == first and first["k"] == 4.0 and accuracy.K_KEY == "kickers" not in first  # "k" is the shrinkage
    krec = accuracy.in_force_k(data, 2026, 6, sched, CFG, LATE)
    after = json.loads(path.read_text())
    assert {k: v for k, v in after.items() if k != "kickers"} == before and after["kickers"] == krec
    assert set(krec) >= {"method", "alpha", "sources", "from_weeks", "priors", "k", "prior_split", "computed_at"}
    assert krec["computed_at"] == LATE.isoformat() and set(krec["sources"]) == {"sleeper_k", "espn_k"}
    (data / "2026" / "week01" / "actuals.json").unlink()  # written once: later data never rewrites either record
    assert accuracy.in_force_k(data, 2026, 6, sched, CFG, LATE) == krec
    again = accuracy.in_force(data, 2026, 6, sched, CFG, LATE)
    assert {k: v for k, v in again.items() if k != "kickers"} == first and json.loads(path.read_text()) == after


def test_audit_reproduces_both_records(tmp_path):
    data, sched = _world(tmp_path)
    accuracy.in_force(data, 2026, 6, sched, CFG, LATE)
    accuracy.in_force_k(data, 2026, 6, sched, CFG, LATE)
    rows = {r["check"]: r for r in audit.run(data, 2026, CFG) if r["week"] == 6}
    assert rows["weights"]["status"] == "pass" and rows["weights k"] == {
        "check": "weights k", "week": 6, "status": "pass", "detail": "reproduces",
    }  # fmt: skip
    path = data / "2026" / "week06" / "weights.json"
    doc = json.loads(path.read_text())
    doc["kickers"]["sources"]["espn_k"]["w"] += 0.5
    path.write_text(json.dumps(doc))
    rows = {r["check"]: r for r in audit.run(data, 2026, CFG) if r["week"] == 6}
    assert rows["weights"]["status"] == "pass" and rows["weights k"]["detail"] == "differs: espn_k"
