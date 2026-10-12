"""Sleeper probe: league facts, D14 position-limit counting, scoring ground truth, endpoints."""

from __future__ import annotations

import traceback

from . import analysis as an
from .phttp import Client

V1 = "https://api.sleeper.app/v1"
API = "https://api.sleeper.com"
SLOT_POSITIONS = ["DL", "LB", "DB"]
GRANULAR_POSITIONS = ["DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "CB", "S", "SS", "FS"]
SLIM_PLAYER_FIELDS = (
    "player_id", "full_name", "first_name", "last_name", "search_full_name", "position",
    "fantasy_positions", "depth_chart_position", "team", "status", "active", "injury_status",
    "years_exp", "espn_id", "gsis_id", "sportradar_id", "rotowire_id", "yahoo_id",
)  # fmt: skip
WAIVER_SETTING_KEYS = (
    "waiver_type", "waiver_day_of_week", "waiver_clear_days", "daily_waivers",
    "daily_waivers_hour", "daily_waivers_days", "waiver_budget", "playoff_week_start",
    "last_scored_leg", "leg",
)  # fmt: skip


class SleeperProbe:
    def __init__(self, client: Client):
        self.client = client
        self.section: dict = {"requests": [], "errors": []}
        self.samples: dict[str, object] = {}

    def _json(self, label: str, url: str):
        r = self.client.get(url)
        self.section["requests"].append(
            {"label": label, "url": r.url, "status": r.status, "error": r.error,
             "bytes": len(r.body), "elapsed_s": r.elapsed_s, "headers": r.headers}
        )  # fmt: skip
        if not r.ok:
            return None
        try:
            return r.json()
        except ValueError:
            self.section["errors"].append(f"{label}: response is not JSON")
            return None

    def _step(self, name: str, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - each step is independent; record and continue
            tb = traceback.extract_tb(e.__traceback__)[-1]
            self.section["errors"].append(f"{name}: {type(e).__name__}: {e} (line {tb.lineno})")
            return None

    @staticmethod
    def _positions_query(positions: list[str]) -> str:
        return "&".join(f"position[]={p}" for p in positions)

    def run(
        self,
        username: str,
        league_names: list[str],
        season_hint: int,
        target_week: int | None,
        settled_week: int | None,
    ) -> tuple[dict, dict]:
        s = self.section
        state = self._json("state", f"{V1}/state/nfl") or {}
        s["state"] = state
        season = int(state.get("league_season") or state.get("season") or season_hint)
        s["season"] = season
        s["weeks_used"] = {
            "target_week_from_schedule": target_week,
            "settled_week_from_schedule": settled_week,
            "state_week": state.get("week"),
            "state_display_week": state.get("display_week"),
        }
        week = target_week or state.get("display_week") or state.get("week")
        settled = settled_week or (int(state["week"]) - 1 if state.get("week") else None)

        players = self._step("players_db", self._players) or {}
        league = self._step("league", self._league, username, league_names, season)
        if league:
            self._step("league_details", self._league_details, league, players, settled, week)
        self._step("projections", self._projections, season, week, players)
        self._step("past_projections", self._past_projections, season, players)
        return s, self.samples

    # -- steps ---------------------------------------------------------------

    def _players(self) -> dict:
        players = self._json("players_db", f"{V1}/players/nfl") or {}
        idp = {pid: p for pid, p in players.items() if an.is_idp(p)}
        sample_fields = sorted(next(iter(idp.values())).keys()) if idp else []
        self.section["players_db"] = {"total": len(players), "idp": len(idp), "fields": sample_fields}
        self.section["injuries"] = an.injury_tallies(players)
        self.samples["players_idp.json"] = {
            pid: {k: p.get(k) for k in SLIM_PLAYER_FIELDS} for pid, p in idp.items() if p.get("team")
        }
        return players

    def _league(self, username: str, league_names: list[str], season: int) -> dict | None:
        user = self._json("user", f"{V1}/user/{username}") or {}
        user_id = user.get("user_id")
        self.section["user_id"] = user_id
        if not user_id:
            self.section["errors"].append(f"user '{username}' not found")
            return None
        leagues = self._json("leagues", f"{V1}/user/{user_id}/leagues/nfl/{season}") or []
        wanted = {n.strip().lower() for n in league_names}
        matched = [lg for lg in leagues if (lg.get("name") or "").strip().lower() in wanted]
        self.section["league_match"] = {
            "season": season,
            "candidates": [
                {"name": lg.get("name"), "league_id": lg.get("league_id"), "status": lg.get("status")} for lg in leagues
            ],
            "matched_ids": [lg.get("league_id") for lg in matched],
        }
        if len(matched) != 1:
            self.section["errors"].append(f"expected exactly 1 league named {sorted(wanted)}, found {len(matched)}")
            return None
        league = self._json("league", f"{V1}/league/{matched[0]['league_id']}")
        if league:
            league["_user_id"] = user_id
        return league

    def _league_details(self, league: dict, players: dict, settled: int | None, week: int | None) -> None:
        s = self.section
        lid = league["league_id"]
        settings = league.get("settings") or {}
        scoring = league.get("scoring_settings") or {}
        s["league"] = {
            "league_id": lid, "name": league.get("name"), "status": league.get("status"),
            "season": league.get("season"), "previous_league_id": league.get("previous_league_id"),
            "total_rosters": league.get("total_rosters"), "roster_positions": league.get("roster_positions"),
            "position_limits": an.position_limits(settings),
            "waiver_settings": {k: settings.get(k) for k in WAIVER_SETTING_KEYS},
            "settings": settings,
            "scoring_nonzero": {k: v for k, v in sorted(scoring.items()) if an.num(v)},
        }  # fmt: skip
        self.samples["league.json"] = {k: v for k, v in league.items() if not k.startswith("_")}

        rosters = self._json("rosters", f"{V1}/league/{lid}/rosters") or []
        self.samples["rosters.json"] = rosters
        user_id = league.get("_user_id")
        mine = [r for r in rosters if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or [])]
        s["my_roster_id"] = mine[0].get("roster_id") if len(mine) == 1 else None
        s["roster_fields"] = sorted({k for r in rosters for k in r})
        s["d14"] = an.d14_analysis(rosters, players, an.position_limits(settings))

        if settled:
            self._ground_truth(lid, league, scoring, players, settled)
        self._transactions(lid, settings, week)

    def _ground_truth(self, lid: str, league: dict, scoring: dict, players: dict, week: int) -> None:
        season = league.get("season")
        matchups = self._json("matchups", f"{V1}/league/{lid}/matchups/{week}") or []
        points: dict[str, float] = {}
        for m in matchups:
            points.update(m.get("players_points") or {})
        idp_ids = {pid for pid in points if an.is_idp(players.get(pid, {}))}

        stats_v1 = self._json("stats_v1", f"{V1}/stats/nfl/regular/{season}/{week}")
        stats_api = self._json(
            "stats_api", f"{API}/stats/nfl/{season}/{week}?season_type=regular&{self._positions_query(SLOT_POSITIONS)}"
        )
        by_pid_v1 = {r["player_id"]: r["stats"] for r in an.rows_from_payload(stats_v1)}
        by_pid_api = {str(r.get("player_id")): r.get("stats") or {} for r in an.rows_from_payload(stats_api)}
        self.section["stats_endpoints"] = {
            "v1_rows": len(by_pid_v1), "api_shape": an.projection_shape(stats_api, players),
        }  # fmt: skip
        idp_lines = [st for pid, st in (by_pid_v1 or by_pid_api).items() if an.is_idp(players.get(pid, {}))]
        gt = {"week": week, "rostered_idp_in_players_points": len(idp_ids)}
        for label, by_pid in (("v1", by_pid_v1), ("api", by_pid_api)):
            if by_pid:
                gt[label] = an.ground_truth(by_pid, points, scoring, idp_ids, players)
        self.section["ground_truth"] = gt
        self.section["relevant_keys"] = an.relevant_keys(idp_lines, scoring)
        self.section["bonus_consistency"] = an.bonus_consistency(idp_lines)
        best = by_pid_v1 or by_pid_api
        self.samples[f"ground_truth_week{week:02d}.json"] = {
            "week": week, "season": season, "scoring_settings": scoring, "matchups": matchups,
            "stats": {pid: best[pid] for pid in idp_ids if pid in best},
        }  # fmt: skip

    def _transactions(self, lid: str, settings: dict, week: int | None) -> None:
        txns: list[dict] = []
        for w in range(1, (int(week) if week else 1) + 1):
            txns += self._json(f"transactions_w{w}", f"{V1}/league/{lid}/transactions/{w}") or []
        self.section["waivers"] = an.waiver_timing(txns)

    def _projections(self, season: int, week: int | None, players: dict) -> None:
        if not week:
            self.section["errors"].append("projections: no target week")
            return
        out = {"week": week}
        for variant, positions in (("slot_positions", SLOT_POSITIONS), ("granular_positions", GRANULAR_POSITIONS)):
            url = f"{API}/projections/nfl/{season}/{week}?season_type=regular&{self._positions_query(positions)}"
            payload = self._json(f"projections_{variant}", url)
            out[variant] = an.projection_shape(payload, players)
            if variant == "slot_positions" and payload:
                self.samples[f"projections_week{int(week):02d}_sample.json"] = an.rows_from_payload(payload)[:40]
        self.section["projections"] = out

    def _past_projections(self, season: int, players: dict) -> None:
        """Can week-1 projections still be retrieved, and are they pre-game (not overwritten by actuals)?"""
        proj = self._json(
            "projections_week1",
            f"{API}/projections/nfl/{season}/1?season_type=regular&{self._positions_query(SLOT_POSITIONS)}",
        )
        actual = self._json("stats_v1_week1", f"{V1}/stats/nfl/regular/{season}/1")
        shape = an.projection_shape(proj, players)
        p_by = {str(r.get("player_id")): r.get("stats") or {} for r in an.rows_from_payload(proj)}
        a_by = {r["player_id"]: r["stats"] for r in an.rows_from_payload(actual)}
        both = [pid for pid in p_by if pid in a_by and p_by[pid].get("idp_tkl_solo") is not None]
        diffs = [abs(an.num(p_by[p]["idp_tkl_solo"]) - an.num(a_by[p].get("idp_tkl_solo"))) for p in both]
        self.section["past_projections_week1"] = {
            "shape": shape,
            "players_with_projection_and_actual": len(both),
            "share_identical_to_actual": round(sum(d < 1e-9 for d in diffs) / len(diffs), 3) if diffs else None,
            "mean_abs_diff_solo_tackles": round(sum(diffs) / len(diffs), 3) if diffs else None,
        }
