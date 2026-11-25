"""Team defenses as a third group (D25): the slot, the status rule, the two adapters on real (trimmed) pages, the
scoring of real stat lines against a league's own points, the roster-room rule, and the shared D/ST page."""

import json
from datetime import UTC, datetime

from test_sync_site import CFG, FIXTURES, SCHED

from sadlidp import kicker, positions, scoring, storage
from sadlidp.net import Response
from sadlidp.pool import player_status
from sadlidp.positions import DEF, K
from sadlidp.roster import panel
from sadlidp.sleeper import DEF_POSITIONS_QUERY, IDP_POSITIONS_QUERY, K_POSITIONS_QUERY, Sleeper, position_limits
from sadlidp.sources import fp_def, fp_dst, registry, sleeper_def
from sadlidp.sources.base import FetchContext
from sadlidp.sync import league_slots, scoring_differences, warn_label

NOW = datetime(2026, 11, 25, 19, 0, tzinfo=UTC)
PROJ = json.loads((FIXTURES / "sleeper_def_projections_week13_trimmed.json").read_text())
TRUTH = json.loads((FIXTURES / "sleeper_def_week11_ground_truth.json").read_text())
DST_PAGE = (FIXTURES / "fp_dst_week12_trimmed.html").read_text()
CROOKED = json.loads((FIXTURES / "sleeper_league_crooked.json").read_text())
SADL_SC = json.loads((FIXTURES / "sleeper_league.json").read_text())["scoring_settings"]


def defense(team: str, **kw) -> dict:
    """A team defense as Sleeper's players DB lists it: no status, no full name."""
    return {"position": "DEF", "fantasy_positions": ["DEF"], "team": team, "first_name": team, "last_name": "D",
            "active": True, "status": None, "injury_status": None, **kw}  # fmt: skip


# ---- the slot ----------------------------------------------------------------------------------------------


def test_a_team_defense_is_its_own_slot_and_group():
    kc = defense("KC", first_name="Kansas City", last_name="Chiefs")
    assert positions.slot_of(kc) == DEF and positions.group_of(DEF) == "def" and positions.is_defense(kc)
    assert not positions.is_idp(kc) and not positions.is_kicker(kc) and positions.idp_slot_of(kc) is None
    assert positions.also_eligible(kc) == [] and positions.player_name(kc) == "Kansas City Chiefs"
    assert positions.slot_of({"position": "LB", "fantasy_positions": ["LB"]}) == "LB"  # nothing else moves
    assert positions.slot_of({"position": "K", "fantasy_positions": ["DL"]}) == "DL"  # id 5251 stays DL
    assert positions.ROOM_SLOTS == (K, DEF) and positions.EVAL["def"] == {"pool": 20, "top": 5, "min_cover": 10}


def test_a_defense_with_no_status_is_playable_and_nobody_else_is():
    week = 5
    jax = player_status(defense("JAX"), SCHED, week, NOW.replace(month=10, day=6))
    assert jax.playable and jax.kickoff is not None
    assert player_status(defense("KC"), SCHED, week, NOW).reasons == ["bye"]  # KC's week-5 bye still applies
    lb = player_status({"position": "LB", "team": "JAX", "status": None, "active": True}, SCHED, week, NOW)
    assert lb.reasons[0] == "no status"  # only a team defense is excused


# ---- the registry and the request --------------------------------------------------------------------------


def test_the_def_sources_are_their_own_group_after_the_kickers():
    assert registry.names_for("def") == ("sleeper_def", "fp_def")
    assert registry.group_of_source("fp_def") == "def" and registry.group_of_source("fp_dst") == "idp"
    assert list(registry.labels())[-2:] == ["sleeper_def", "fp_def"]  # the IDP and K order is untouched
    assert registry.projection_count("def") == 1 and registry.sources_for("def") is registry.DEF_SOURCES
    assert warn_label("fp_def") == "FantasyPros defenses" and warn_label("fp_k") == "FantasyPros kickers"
    for name in registry.names_for("def"):
        assert CFG["sources"][name]["enabled"] and CFG["sources"][name]["archive"], name
    assert CFG["sigma_out"]["DEF"] == 5.7 and CFG["tiers"]["sigma_floor"]["DEF"] == 0.75
    assert {"sleeper_def", "fp_def"} <= set(CFG["accuracy"]["untrusted"])


class Calls:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        body = self.routes.get(url, 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        return Response(
            url=url, final_url=url, status=200, body=(body if isinstance(body, str) else json.dumps(body)).encode()
        )


def _sleeper_ctx(payload, players=None, week=13):
    url = f"https://api.sleeper.com/projections/nfl/2026/{week}?season_type=regular&{DEF_POSITIONS_QUERY}"
    client = Calls({url: payload})
    return FetchContext(client, Sleeper(client), 2026, week, NOW, players or {}, {}), client


def test_sleeper_def_keeps_the_line_and_its_points_allowed_tier():
    ctx, client = _sleeper_ctx(PROJ)
    res = sleeper_def.fetch(ctx)
    assert res.ok and client.calls[0].endswith("&position[]=DEF") and len(client.calls) == 1
    assert IDP_POSITIONS_QUERY.startswith("position[]=DL") and K_POSITIONS_QUERY == "position[]=K"
    kc = res.rows["KC"]
    assert kc["team"] == "KC" and kc["opponent"] == "LAR"
    line = kc["stats"]
    assert line["pts_allow"] == 22.0 and line["pts_allow_21_27"] == 1.0  # the tier of the projected mean
    assert not any(k.startswith(("pts_std", "pts_ppr", "pts_half", "adp_", "pos_adp_")) for k in line)
    assert res.universe == {DEF: {"complete": True, "depth": None}} and "imputed" not in kc
    # the league scores the one-hot tier of the projected points allowed, and nothing else of points allowed
    for row in PROJ:
        line = sleeper_def.published_line(row["stats"])
        tiers = {k: v for k, v in line.items() if k.startswith("pts_allow_")}
        assert len(tiers) == 1 and list(tiers.values()) == [1.0], row["player_id"]
        bare = {k: v for k, v in line.items() if k not in tiers}
        gain = scoring.projected_points(line, SADL_SC) - scoring.projected_points(bare, SADL_SC)
        assert abs(gain - SADL_SC[next(iter(tiers))]) < 1e-9, row["player_id"]


def test_sleeper_def_statuses():
    assert sleeper_def.fetch(_sleeper_ctx([{"player_id": "1", "week": 13, "player": {"position": "LB"},
                                            "stats": {"gp": 1}, "opponent": "X"}])[0]).error == (
        "no team-defense rows in the response")  # fmt: skip
    placeholder = [{"player_id": "KC", "week": 13, "player": {"position": "DEF"}, "stats": {}, "opponent": None}]
    assert sleeper_def.fetch(_sleeper_ctx(placeholder)[0]).status == "suspect"
    other_week = [{**PROJ[0], "week": 12}]
    assert sleeper_def.fetch(_sleeper_ctx(other_week)[0]).status == "not_yet_published"
    assert sleeper_def.fetch(_sleeper_ctx(404)[0]).status == "failed"
    assert sleeper_def.fetch(_sleeper_ctx({"a": 1})[0]).status == "parse_error"


# ---- FantasyPros D/ST: one page, two readers ----------------------------------------------------------------


def _fp_ctx(players, pages=None, page=DST_PAGE, week=12):
    client = Calls({f"https://www.fantasypros.com/nfl/rankings/dst.php?week={week}": page})
    cfg = {"_root": CFG["_root"]}
    return FetchContext(client, None, 2026, week, NOW, players, cfg, pages=pages), client


def test_fp_def_ranks_by_team_code_and_shares_the_page_with_the_signal():
    teams = ["MIN", "JAX", "DEN", "TB", "NYG", "WAS", "HOU", "CLE", "CIN", "NYJ", "SF"]  # LAC left out of the DB
    players = {t: defense(t) for t in teams}
    pages: dict = {}
    ctx, client = _fp_ctx(players, pages)
    signal = fp_dst.fetch(ctx)
    res = fp_def.fetch(ctx)
    assert len(client.calls) == 1  # one request serves both
    assert res.ok and res.kind == "rank" and signal.ok and signal.kind == "signal"
    assert res.rows["MIN"]["ranks"] == {DEF: 1} and res.rows["JAX"]["ranks"] == {DEF: 2}  # JAC is JAX
    assert res.lists[DEF]["NYJ"] == 10 and "LAC" not in res.rows and res.universe[DEF]["depth"] == 12
    assert res.report["unresolved_teams"] == {"LAC": 1}  # a team the players DB lacks is reported, not dropped silently
    assert signal.rows["JAX"]["rank"] == 2 and set(signal.rows) >= {"MIN", "LAC"}  # the signal is as it was
    alone, c2 = _fp_ctx(players)  # no memo: fp_def fetches the page itself
    assert fp_def.fetch(alone).ok and len(c2.calls) == 1
    late, _ = _fp_ctx(players, page=DST_PAGE.replace('"week": "12"', '"week": "11"'))
    assert fp_def.fetch(late).status == "not_yet_published"
    none, _ = _fp_ctx({})
    assert fp_def.fetch(none).status == "suspect"


# ---- scoring real lines ------------------------------------------------------------------------------------


def test_def_lines_score_exactly_what_the_league_scored():
    sc = TRUTH["scoring_settings"]
    assert len(TRUTH["players_points"]) >= 12
    for team, pts in TRUTH["players_points"].items():
        assert abs(scoring.points(TRUTH["stats"][team], sc) - pts) < 0.01, team
    # Crooked scores K and DEF exactly like SADL: one consensus serves both (D26)
    lines = list(TRUTH["stats"].values())
    assert scoring_differences(SADL_SC, CROOKED["scoring_settings"], {"k", "def"}, lines) == []
    assert scoring_differences(SADL_SC, CROOKED["scoring_settings"], {"idp"}, [{"rec": 1}]) == ["rec"]


# ---- the roster rule ---------------------------------------------------------------------------------------


def _dpanel(roster, open_spots, waiver=("NE",), statuses=None, limits=None):
    players = {t: defense(t) for t in ("KC", "JAX", "NE", "DET")}
    cons = {"KC": {"mu": 5.0, "sigma": 0.8}, "JAX": {"mu": 6.0, "sigma": 0.8}, "NE": {"mu": 9.5, "sigma": 0.8},
            "DET": {"mu": 4.0, "sigma": 0.8}}  # fmt: skip
    from sadlidp.pool import PlayerStatus

    st = {pid: PlayerStatus() for pid in players}
    st.update(statuses or {})
    return panel({"players": roster}, players, cons, st, {DEF: list(waiver)}, limits or {}, {DEF: 5.7}, 0.6, {},
                 open_spots, (DEF,))[DEF]  # fmt: skip


def test_a_defense_add_follows_roster_room_like_a_kicker():
    from sadlidp.pool import PlayerStatus

    free = _dpanel(["JAX"], 2)
    assert free["badges"]["NE"]["action"] == "add" and free["limit"] is None and free["open_spots"] == 2
    full = _dpanel(["JAX", "KC"], 0)
    assert full["badges"]["NE"]["badge"] == "START" and full["badges"]["NE"]["action"] == "add_drop"
    assert full["badges"]["NE"]["drop"] == "KC"  # B, the weaker of yours
    bye = _dpanel(["JAX", "KC"], 0, statuses={"KC": PlayerStatus(reasons=["bye"])})
    assert bye["starter"] == "JAX" and bye["badges"]["NE"]["drop"] == "KC"  # your defense on bye goes first
    swap = _dpanel(["JAX"], 0)
    assert swap["badges"]["NE"]["action"] == "swap" and swap["badges"]["NE"]["drop"] == "JAX"
    capped = _dpanel(["JAX"], 3, limits={DEF: 1})  # a league that sets a DEF limit
    assert capped["badges"]["NE"]["action"] == "swap" and capped["limit"] == 1
    assert position_limits({"position_limit_def": 1}, 2)["DEF"] == 1
    assert position_limits({"position_limit_dl": 2}, 2) == {"DL": 2, "LB": 2, "DB": 2}  # SADL as before
    assert position_limits({}, 2, (K, DEF)) == {}  # a league starting no IDP gets no IDP limit


def test_league_slots_follow_the_roster_and_the_enabled_groups():
    on = {"idp": True, "k": True, "def": True}
    assert league_slots(CROOKED["roster_positions"], on) == (K, DEF)
    sadl = json.loads((FIXTURES / "sleeper_league.json").read_text())["roster_positions"]
    assert league_slots(sadl, on) == ("DL", "LB", "DB", K)
    assert league_slots(["IDP_FLEX", "BN"], on) == ("DL", "LB", "DB")
    assert league_slots(CROOKED["roster_positions"], {**on, "def": False}) == (K,)


# ---- past results: one request a week for both groups -------------------------------------------------------


class StatsSleeper:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def stats(self, season, week):
        self.calls.append(week)
        return self.payload, None


def test_settled_weeks_gain_def_lines_add_only_sharing_the_kickers_request(tmp_path):
    week = 4
    captured = datetime(2026, 10, 20, tzinfo=UTC).isoformat()  # after week 4 settled
    path = storage.week_dir(tmp_path, 2026, week) / "actuals.json"
    storage.write_json(path, {"captured_at": captured, "stats": {"11017": {"gp": 1, "idp_tkl": 3}}})
    players = {"11017": {"position": "LB"}, "11786": {"position": "K"}, "JAX": defense("JAX")}
    payload = {"11017": {"gp": 1, "idp_tkl": 9}, "11786": {"gp": 1, "fgm_yds": 80}, "JAX": {"gp": 1, "sack": 3},
               "TEAM_JAX": {"gp": 1, "sack": 3}}  # fmt: skip
    sl, fetched = StatsSleeper(payload), {}
    now = datetime(2026, 11, 25, tzinfo=UTC)
    assert kicker.add_past_actuals(sl, tmp_path, 2026, SCHED, now, players, fetched=fetched) == [week]
    assert kicker.add_past_actuals(sl, tmp_path, 2026, SCHED, now, players, group="def", fetched=fetched) == [week]
    doc = storage.read_json(path)
    assert sl.calls == [week]  # the second pass reused the first one's payload
    assert doc["stats"]["11017"] == {"gp": 1, "idp_tkl": 3}  # no existing line changes
    assert doc["stats"]["JAX"] == {"gp": 1, "sack": 3} and "TEAM_JAX" not in doc["stats"]
    assert doc["k_added"] == 1 and doc["def_added"] == 1 and doc["def_added_at"]
    again = kicker.add_past_actuals(sl, tmp_path, 2026, SCHED, now, players, group="def")
    assert again == [] and sl.calls == [week]  # marked done: never refetched
