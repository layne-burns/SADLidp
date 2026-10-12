"""Sleeper probe: league facts, D14 position-limit counting, scoring ground truth, endpoints."""

from __future__ import annotations

import traceback
from pathlib import Path

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


def _slim(p: dict) -> dict:
    return {k: p.get(k) for k in SLIM_PLAYER_FIELDS}


class SleeperProbe:
    def __init__(self, client: Client):
        self.client = client
        self.section: dict = {"requests": [], "errors": []}
        self.samples: dict[str, object] = {}
        self._roster_list: list[dict] = []
        self._player_db: dict[str, dict] = {}

    def _json(self, label: str, url: str):
        r = self.client.get(url)
        self.section["requests"].append(
            {"label": label, "url": r.url, "status": r.status, "error": r.error,
             "bytes": len(r.body), "elapsed_s": r.elapsed_s, "headers": r.headers}
        )  # fmt: skip
        if not r.ok:
            self.section["errors"].append(f"{label}: HTTP {r.status}" + (f" ({r.error})" if r.error else ""))
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
            self.section["errors"].append(f"{name}: {type(e).__name__} ({Path(tb.filename).name}:{tb.lineno})")
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
        state_week = int(state["week"]) if str(state.get("week") or "").isdigit() else None
        week = target_week or state.get("display_week") or state_week
        # Without the schedule, state.week-1 may have ended <4 days ago; state.week-2 is safely settled.
        settled = settled_week or (state_week - 2 if state_week and state_week > 2 else None)
        s["weeks_used"] = {
            "target_week_from_schedule": target_week,
            "settled_week_from_schedule": settled_week,
            "state_week": state.get("week"),
            "state_display_week": state.get("display_week"),
            "week_used": week,
            "settled_week_used": settled,
            "settled_source": "schedule" if settled_week else "state.week-2 (schedule unavailable)",
        }

        players = self._step("players_db", self._players) or {}
        if not players:
            s["d14"] = {"inconclusive": "players DB unavailable; D14 not evaluated"}
        league = self._step("league", self._league, username, league_names, season)
        if league:
            self._step("league_details", self._league_details, league, players)
            if settled and players:
                self._step("ground_truth", self._ground_truth, league, players, settled)
            elif settled:
                s["ground_truth"] = {"week": settled, "error": "players DB unavailable; ground truth not evaluated"}
            self._step("transactions", self._transactions, league, week)
        self._step("projections", self._projections, season, week, players)
        self._step("past_projections", self._past_projections, season, players)
        return s, self.samples

    # -- steps ---------------------------------------------------------------

    def _players(self) -> dict:
        players = self._json("players_db", f"{V1}/players/nfl")
        if not isinstance(players, dict) or not players:
            self.section["players_db"] = {"error": "request failed or empty"}
            self.section["injuries"] = {"error": "players DB unavailable"}
            return {}
        idp = {pid: p for pid, p in players.items() if an.is_idp(p)}
        sample_fields = sorted(next(iter(idp.values())).keys()) if idp else []
        self.section["players_db"] = {"total": len(players), "idp": len(idp), "fields": sample_fields}
        self.section["injuries"] = an.injury_tallies(players)
        self.samples["players_idp.json"] = {pid: _slim(p) for pid, p in idp.items() if p.get("team")}
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

    def _league_details(self, league: dict, players: dict) -> None:
        s = self.section
        lid = league["league_id"]
        settings = league.get("settings") or {}
        scoring = league.get("scoring_settings") or {}
        s["league"] = {
            "league_id": lid,
            "name": league.get("name"),
            "status": league.get("status"),
            "season": league.get("season"),
            "previous_league_id": league.get("previous_league_id"),
            "total_rosters": league.get("total_rosters"),
            "roster_positions": league.get("roster_positions"),
            "position_limits": an.position_limits(settings),
            "waiver_settings": {k: settings.get(k) for k in WAIVER_SETTING_KEYS},
            "settings": settings,
            "scoring_nonzero": {k: v for k, v in sorted(scoring.items()) if an.num(v)},
        }
        self.samples["league.json"] = {k: v for k, v in league.items() if not k.startswith("_")}

        rosters = self._json("rosters", f"{V1}/league/{lid}/rosters")
        if not isinstance(rosters, list):
            s["d14"] = {"inconclusive": "rosters unavailable"}
            return
        self.samples["rosters.json"] = rosters
        self._roster_list, self._player_db = rosters, players
        rostered = {pid for r in rosters for key in ("players", "reserve", "taxi") for pid in (r.get(key) or [])}
        # rostered players regardless of team, so released players can be reconstructed offline
        self.samples["players_rostered.json"] = {pid: _slim(players[pid]) for pid in sorted(rostered) if pid in players}
        user_id = league.get("_user_id")
        mine = [r for r in rosters if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or [])]
        s["my_roster_id"] = mine[0].get("roster_id") if len(mine) == 1 else None
        s["roster_fields"] = sorted({k for r in rosters for k in r})
        if players:
            s["d14"] = an.d14_analysis(rosters, players, an.position_limits(settings))

    def _stats_api(self, season, week: int, players: dict) -> dict[str, dict]:
        """api.sleeper.com stats with slot and granular position filters; use whichever has more IDPs."""
        variants, shapes = {}, {}
        for v, pos in (("slot", SLOT_POSITIONS), ("granular", GRANULAR_POSITIONS)):
            payload = self._json(
                f"stats_api_{v}_w{week}",
                f"{API}/stats/nfl/{season}/{week}?season_type=regular&{self._positions_query(pos)}",
            )
            variants[v] = an.stats_by_player(payload)
            shapes[v] = an.projection_shape(payload, players) if payload is not None else {"error": "request failed"}
        best = max(variants, key=lambda v: sum(an.is_idp(players.get(pid, {})) for pid in variants[v]))
        self.section.setdefault("stats_endpoints", {})[f"week{week}"] = {"api_shapes": shapes, "api_variant_used": best}
        return variants[best]

    def _ground_truth(self, league: dict, players: dict, week: int) -> None:
        gt = self._ground_truth_week(league, players, week)
        self.section["ground_truth"] = gt
        best = gt.get("v1") or gt.get("api") or {}
        # bonus_hits only lists threshold keys SADL scores; {} means none to test, so no fallback
        hits = best.get("bonus_hits") or {}
        if best.get("players_compared") and hits and not any(hits.values()) and week > 1:
            # SPEC §10: the acceptance fixture needs at least one threshold-bonus hit; try the week before
            self.section["ground_truth_prev_week"] = self._ground_truth_week(league, players, week - 1)

    def _ground_truth_week(self, league: dict, players: dict, week: int) -> dict:
        lid, season = league["league_id"], league.get("season")
        scoring = league.get("scoring_settings") or {}
        matchups = self._json(f"matchups_w{week}", f"{V1}/league/{lid}/matchups/{week}")
        if matchups is None:
            return {"week": week, "error": "matchups request failed"}
        points: dict[str, float] = {}
        for m in matchups:
            points.update(m.get("players_points") or {})
        idp_ids = {pid for pid in points if an.is_idp(players.get(pid, {}))}

        by_pid_v1 = an.stats_by_player(self._json(f"stats_v1_w{week}", f"{V1}/stats/nfl/regular/{season}/{week}"))
        by_pid_api = self._stats_api(season, week, players)
        self.section.setdefault("stats_endpoints", {}).setdefault(f"week{week}", {})["v1_rows"] = len(by_pid_v1)
        best = by_pid_v1 or by_pid_api
        idp_lines = [st for pid, st in best.items() if an.is_idp(players.get(pid, {}))]
        gt = {"week": week, "rostered_idp_in_players_points": len(idp_ids)}
        for label, by_pid in (("v1", by_pid_v1), ("api", by_pid_api)):
            if by_pid:
                gt[label] = an.ground_truth(by_pid, points, scoring, idp_ids, players)
        gt["relevant_keys"] = an.relevant_keys(idp_lines, scoring)
        gt["bonus_consistency"] = an.bonus_consistency(idp_lines)
        self.samples[f"ground_truth_week{week:02d}.json"] = {
            "week": week,
            "season": season,
            "scoring_settings": scoring,
            "matchups": matchups,
            "stats": {pid: best[pid] for pid in idp_ids if pid in best},
        }
        return gt

    def _transactions(self, league: dict, week: int | None) -> None:
        lid = league["league_id"]
        txns: list[dict] = []
        for w in range(1, (int(week) if week else 1) + 1):
            txns += self._json(f"transactions_w{w}", f"{V1}/league/{lid}/transactions/{w}") or []
        self.samples["transactions.json"] = txns
        self.section["waivers"] = an.waiver_timing(txns)
        self.section["rollover"] = an.leg_boundaries(txns)
        self.section["failed_waiver_notes"] = an.failed_waiver_notes(txns)
        if self._roster_list and self._player_db:
            limits = an.position_limits(league.get("settings") or {})
            self.section["d14_add_time"] = an.d14_add_time_replay(txns, self._roster_list, self._player_db, limits)

    def _projections(self, season: int, week: int | None, players: dict) -> None:
        if not week:
            self.section["projections"] = {"error": "no target week (schedule and state unavailable)"}
            return
        out: dict = {"week": week}
        for variant, positions in (("slot_positions", SLOT_POSITIONS), ("granular_positions", GRANULAR_POSITIONS)):
            url = f"{API}/projections/nfl/{season}/{week}?season_type=regular&{self._positions_query(positions)}"
            payload = self._json(f"projections_{variant}", url)
            if payload is None:
                out[variant] = {"error": "request failed"}
                continue
            out[variant] = an.projection_shape(payload, players)
            if variant == "slot_positions":
                self.samples[f"projections_week{int(week):02d}_sample.json"] = an.rows_from_payload(payload)[:40]
        self.section["projections"] = out

    def _past_projections(self, season: int, players: dict) -> None:
        """Can week-1 projections still be retrieved, and are they pre-game (not overwritten by actuals)?"""
        proj = self._json(
            "projections_week1",
            f"{API}/projections/nfl/{season}/1?season_type=regular&{self._positions_query(SLOT_POSITIONS)}",
        )
        actual = self._json("stats_v1_week1", f"{V1}/stats/nfl/regular/{season}/1")
        if proj is None or actual is None:
            self.section["past_projections_week1"] = {"error": "request failed; retrievability unknown"}
            return
        p_by = an.stats_by_player(proj)
        a_by = an.stats_by_player(actual)
        both = [pid for pid in p_by if pid in a_by and p_by[pid].get("idp_tkl_solo") is not None]
        diffs = [abs(an.num(p_by[p]["idp_tkl_solo"]) - an.num(a_by[p].get("idp_tkl_solo"))) for p in both]
        self.section["past_projections_week1"] = {
            "shape": an.projection_shape(proj, players),
            "players_with_projection_and_actual": len(both),
            "share_identical_to_actual": round(sum(d < 1e-9 for d in diffs) / len(diffs), 3) if diffs else None,
            "mean_abs_diff_solo_tackles": round(sum(diffs) / len(diffs), 3) if diffs else None,
        }
