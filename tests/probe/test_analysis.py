from datetime import datetime, timezone

from probe import analysis as an

PLAYERS = {
    "1": {"full_name": "Edge Hybrid", "position": "LB", "fantasy_positions": ["DL", "LB"], "team": "BUF"},
    "2": {"full_name": "Pure DL", "position": "DE", "fantasy_positions": ["DL"], "team": "BUF"},
    "3": {"full_name": "Pure LB", "position": "LB", "fantasy_positions": ["LB"], "team": "KC"},
    "4": {"full_name": "Box Safety", "position": "S", "fantasy_positions": ["DB", "LB"], "team": "KC"},
    "5": {"full_name": "Corner", "position": "CB", "fantasy_positions": ["DB"], "team": "SF"},
    "9": {"full_name": "Quarterback", "position": "QB", "fantasy_positions": ["QB"], "team": "SF"},
}


def test_slots_and_eligibility():
    assert an.slot_by_position(PLAYERS["1"]) == "LB"
    assert an.slot_by_fantasy_first(PLAYERS["1"]) == "DL"
    assert an.idp_eligibility(PLAYERS["4"]) == ["DB", "LB"]
    assert an.is_idp(PLAYERS["5"]) and not an.is_idp(PLAYERS["9"])


def test_position_limits_parsed_from_settings():
    assert an.position_limits({"position_limit_dl": 2, "position_limit_lb": 2, "waiver_type": 2}) == {"DL": 2, "LB": 2}


def test_d14_distinguishes_rules():
    # Roster holds two LB-designated players, one of them DL-first in fantasy_positions, plus two DL.
    rosters = [{"roster_id": 1, "players": ["1", "2", "3", "9", "x"], "reserve": [], "taxi": None}]
    players = dict(PLAYERS, x={"position": "DT", "fantasy_positions": ["DL"]})
    res = an.d14_analysis(rosters, players, {"DL": 2, "LB": 2, "DB": 2})
    by_pos = res["rules"]["position"]["all"]
    by_fp = res["rules"]["fantasy_positions[0]"]["all"]
    assert by_pos["violations"] == [] and by_pos["max_seen"] == {"DL": 2, "LB": 2, "DB": 0}
    assert by_fp["violations"] == [{"roster_id": 1, "slot": "DL", "count": 3}]
    assert [d["player_id"] for d in res["dual_eligible_rostered"]] == ["1"]
    assert res["dual_fantasy_positions_orderings"] == {"DL/LB": 1, "DB/LB": 1}
    assert res["players_where_position_and_fantasy_first_disagree"] == 1


def test_d14_reserve_variant_excludes_ir():
    rosters = [{"roster_id": 7, "players": ["2", "x", "y"], "reserve": ["y"]}]
    players = {"2": PLAYERS["2"], "x": {"position": "DT"}, "y": {"position": "DE"}}
    res = an.d14_analysis(rosters, players, {"DL": 2})
    assert res["rules"]["position"]["all"]["violations"]
    assert res["rules"]["position"]["excl_reserve"]["violations"] == []


SCORING = {"idp_tkl_solo": 1.0, "idp_tkl_ast": 0.5, "idp_sack": 3.0, "bonus_tkl_10p": 2.0, "pass_yd": 0.04}


def test_score_line_ignores_unscored_and_missing():
    assert an.score_line({"idp_tkl_solo": 4, "idp_tkl_ast": 2, "gp": 1}, SCORING) == 5.0


def test_derive_bonuses_uses_total_tackles():
    st = {"idp_tkl_solo": 6, "idp_tkl_ast": 4}
    assert an.derive_bonuses(st, SCORING)["bonus_tkl_10p"] == 1
    assert "bonus_tkl_10p" not in an.derive_bonuses({"idp_tkl_solo": 9}, SCORING)
    # a delivered bonus is never re-derived
    assert an.derive_bonuses({"idp_tkl_solo": 12, "bonus_tkl_10p": 0}, SCORING)["bonus_tkl_10p"] == 0


def test_ground_truth_reports_both_variants():
    stats = {"1": {"idp_tkl_solo": 6, "idp_tkl_ast": 4}, "2": {"idp_sack": 1}}
    points = {"1": 10.0, "2": 3.0, "9": 20.0}
    res = an.ground_truth(stats, points, SCORING, {"1", "2"}, PLAYERS)
    assert res["players_compared"] == 2
    assert res["match_as_delivered"] == 1  # player 1 needs the derived bonus
    assert res["match_with_derived_bonuses"] == 2
    assert res["mismatches"] == []


def test_ground_truth_lists_real_mismatches():
    res = an.ground_truth({"2": {"idp_sack": 1}}, {"2": 4.0}, SCORING, {"2"}, PLAYERS)
    assert res["mismatches"][0]["name"] == "Pure DL"
    assert res["mismatches"][0]["scored_keys"] == {"idp_sack": 1}


def test_relevant_keys_and_bonus_consistency():
    lines = [{"idp_tkl_solo": 10, "idp_tkl_ast": 1, "idp_tkl": 11, "bonus_tkl_10p": 1}, {"idp_sack": 2}]
    assert set(an.relevant_keys(lines, SCORING)) == {"idp_tkl_solo", "idp_tkl_ast", "idp_sack", "bonus_tkl_10p"}
    bc = an.bonus_consistency(lines)
    assert bc["idp_tkl_equals_solo_plus_ast"] == 1
    assert bc["bonus_keys"]["bonus_tkl_10p"]["present=True,threshold_met=True"] == 1
    assert bc["bonus_keys"]["bonus_sack_2p"]["present=False,threshold_met=True"] == 1


def test_projection_shape_handles_list_and_dict():
    rows = [
        {"player_id": "1", "stats": {"gp": 1, "idp_tkl_solo": 4.5}, "opponent": "MIA", "company": "rotowire",
         "player": {"position": "LB"}},
        {"player_id": "2", "stats": {"idp_tkl_solo": 3}, "opponent": None},
    ]  # fmt: skip
    shape = an.projection_shape(rows, PLAYERS)
    assert shape["rows"] == 2 and shape["with_gp"] == 1 and shape["with_opponent"] == 1
    assert shape["positions"] == {"LB": 1, "DE": 1}
    assert shape["idp_tkl_solo_noninteger_share"] == 0.5
    assert an.projection_shape({"1": {"idp_sack": 1}})["rows"] == 1


def test_waiver_timing_buckets_pacific():
    # 2026-10-07 10:05 UTC = Wed 03:05 PDT
    ts = int(datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc).timestamp() * 1000)
    res = an.waiver_timing([{"type": "waiver", "status": "complete", "status_updated": ts}, {"type": "free_agent"}])
    assert res["waiver_processed_at_pt"] == {"Wed 03:00 PT": 1}
    assert res["transaction_types"] == {"waiver": 1, "free_agent": 1}


SCHED = [
    {"season": "2026", "game_type": "REG", "week": "5", "gameday": "2026-10-08", "gametime": "20:15",
     "home_team": "LA", "away_team": "SF"},
    {"season": "2026", "game_type": "REG", "week": "5", "gameday": "2026-10-12", "gametime": "20:15",
     "home_team": "KC", "away_team": "BUF"},
    {"season": "2026", "game_type": "REG", "week": "6", "gameday": "2026-10-15", "gametime": "20:15",
     "home_team": "BUF", "away_team": "SF"},
    {"season": "2026", "game_type": "REG", "week": "4", "gameday": "2026-10-05", "gametime": "20:15",
     "home_team": "KC", "away_team": "LA"},
]  # fmt: skip


def test_target_and_settled_week():
    now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)  # Monday afternoon ET, MNF not yet played
    assert an.target_week(SCHED, now) == 5
    assert an.settled_week(SCHED, now) == 4
    later = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
    assert an.target_week(SCHED, later) == 6


def test_schedule_summary_byes_and_kickoff_tz():
    k = an.kickoff(SCHED[0])
    assert k.utcoffset().total_seconds() == -4 * 3600  # EDT
    summ = an.schedule_summary(SCHED, 2026, datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc))
    assert summ["target_week"] == 6
    assert summ["byes_in_target_week"] == ["KC", "LA"]
    assert "LA" in summ["teams"]


def test_crosswalk_coverage():
    rows = [
        {"position": "LB", "sleeper_id": "1", "fantasypros_id": "NA", "espn_id": "", "gsis_id": "00-1"},
        {"position": "CB", "sleeper_id": "", "fantasypros_id": "77", "espn_id": "5", "gsis_id": ""},
        {"position": "QB", "sleeper_id": "3", "fantasypros_id": "8", "espn_id": "6", "gsis_id": "x"},
    ]
    cov = an.crosswalk_coverage(rows)
    assert cov["idp_rows"] == 2
    assert cov["share_with_sleeper_id"] == 0.5 and cov["share_with_fantasypros_id"] == 0.5


def test_espn_shape():
    payload = {"players": [{"player": {"defaultPositionId": 11, "stats": [
        {"scoringPeriodId": 6, "statSourceId": 1, "stats": {"107": 4.2, "99": 0.3}},
        {"scoringPeriodId": 5, "statSourceId": 0, "stats": {"107": 5}},
    ]}}]}  # fmt: skip
    shape = an.espn_shape(payload, 6)
    assert shape["players_with_week_projection"] == 1
    assert shape["projection_stat_ids"] == {"107": 1, "99": 1}
    assert shape["prior_week_actual_stat_ids"] == {"107": 1}
