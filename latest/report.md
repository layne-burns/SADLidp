# Probe report

Generated 2026-10-12T05:30:23.273147+00:00 · 53 requests

## 1. League

| field | value |
|---|---|
| league | Slow Ass Draft League (1340072083681472512) status=in_season season=2026 |
| candidates | [{'name': "Kusch's Crooked League", 'league_id': '1364651976209473536', 'status': 'in_season'}, {'name': 'Slow Ass Draft League', 'league_id': '1340072083681472512', 'status': 'in_season'}, {'name': 'Up North Dynasty', 'league_id': '1335661319029784576', 'status': 'in_season'}] |
| my roster_id | 7 |
| roster_positions | ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'SUPER_FLEX', 'K', 'DL', 'LB', 'DB', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN', 'BN'] |
| position limits | {'LB': 2, 'QB': 4, 'DB': 2, 'DL': 2} |
| waiver settings | {'waiver_type': 2, 'waiver_day_of_week': 2, 'waiver_clear_days': 1, 'daily_waivers': 0, 'daily_waivers_hour': 0, 'daily_waivers_days': 10666, 'waiver_budget': 100, 'playoff_week_start': 15, 'last_scored_leg': 4, 'leg': 5} |
| roster fields | ['co_owners', 'keepers', 'league_id', 'metadata', 'owner_id', 'player_map', 'players', 'reserve', 'roster_id', 'settings', 'starters', 'taxi'] |

<details><summary>SADL scoring_settings (non-zero)</summary>

```json
{
 "blk_kick": 2.0,
 "bonus_rec_te": 0.5,
 "def_st_ff": 1.0,
 "def_st_fum_rec": 1.0,
 "def_st_td": 6.0,
 "def_td": 6.0,
 "ff": 1.0,
 "fgm_yds": 0.1,
 "fgmiss": -1.0,
 "fum_lost": -2.0,
 "fum_rec": 2.0,
 "fum_rec_td": 6.0,
 "idp_blk_kick": 3.0,
 "idp_def_td": 6.0,
 "idp_ff": 3.0,
 "idp_fum_rec": 3.0,
 "idp_int": 6.0,
 "idp_pass_def": 3.0,
 "idp_qb_hit": 1.0,
 "idp_sack": 6.0,
 "idp_safe": 3.0,
 "idp_tkl_ast": 1.0,
 "idp_tkl_loss": 2.0,
 "idp_tkl_solo": 2.0,
 "int": 2.0,
 "pass_2pt": 2.0,
 "pass_int": -1.0,
 "pass_td": 4.0,
 "pass_yd": 0.04,
 "pts_allow_0": 10.0,
 "pts_allow_14_20": 1.0,
 "pts_allow_1_6": 7.0,
 "pts_allow_28_34": -1.0,
 "pts_allow_35p": -4.0,
 "pts_allow_7_13": 4.0,
 "rec": 1.0,
 "rec_2pt": 2.0,
 "rec_td": 6.0,
 "rec_yd": 0.1,
 "rush_2pt": 2.0,
 "rush_td": 6.0,
 "rush_yd": 0.1,
 "sack": 1.0,
 "safe": 2.0,
 "st_ff": 1.0,
 "st_fum_rec": 1.0,
 "st_td": 6.0,
 "xpm": 1.0,
 "xpmiss": -1.0
}
```
</details>

<details><summary>full league settings</summary>

```json
{
 "bench_lock": 1,
 "best_ball": 0,
 "capacity_override": 0,
 "commissioner_direct_invite": 0,
 "daily_waivers": 0,
 "daily_waivers_days": 10666,
 "daily_waivers_hour": 0,
 "daily_waivers_last_ran": 13,
 "disable_adds": 0,
 "disable_trades": 0,
 "draft_rounds": 4,
 "faab_suggestions": 0,
 "last_report": 4,
 "last_scored_leg": 4,
 "league_average_match": 0,
 "leg": 5,
 "max_keepers": 1,
 "max_subs": 3,
 "num_teams": 12,
 "offseason_adds": 0,
 "pick_trading": 1,
 "playoff_round_type": 0,
 "playoff_seed_type": 1,
 "playoff_teams": 6,
 "playoff_type": 0,
 "playoff_week_start": 15,
 "position_limit_db": 2,
 "position_limit_dl": 2,
 "position_limit_lb": 2,
 "position_limit_qb": 4,
 "reserve_allow_cov": 0,
 "reserve_allow_dnr": 0,
 "reserve_allow_doubtful": 0,
 "reserve_allow_na": 0,
 "reserve_allow_out": 0,
 "reserve_allow_sus": 0,
 "reserve_slots": 4,
 "start_week": 1,
 "sub_lock_if_starter_active": 0,
 "sub_start_time_eligibility": 1,
 "taxi_allow_vets": 1,
 "taxi_deadline": 4,
 "taxi_slots": 3,
 "taxi_years": 2,
 "trade_deadline": 10,
 "trade_review_days": 0,
 "type": 2,
 "veto_auto_poll": 0,
 "veto_show_votes": 0,
 "veto_votes_needed": 7,
 "waiver_budget": 100,
 "waiver_clear_days": 1,
 "waiver_day_of_week": 2,
 "waiver_type": 2
}
```
</details>


## 2. D14: which position counts toward the limit?
Limits read from settings: `{'LB': 2, 'QB': 4, 'DB': 2, 'DL': 2}`

**Verdict:** see discriminating rosters: rules with violations there are ruled out

| rule | roster subset | rosters over a limit | max count seen |
|---|---|---|---|
| position | all | 5 | {'DL': 4, 'LB': 4, 'DB': 3} |
| position | excl_reserve | 2 | {'DL': 4, 'LB': 2, 'DB': 3} |
| position | excl_taxi | 4 | {'DL': 4, 'LB': 4, 'DB': 2} |
| position | excl_reserve_taxi | 1 | {'DL': 4, 'LB': 2, 'DB': 2} |
| fantasy_positions[0] | all | 9 | {'DL': 5, 'LB': 3, 'DB': 3} |
| fantasy_positions[0] | excl_reserve | 7 | {'DL': 4, 'LB': 2, 'DB': 3} |
| fantasy_positions[0] | excl_taxi | 8 | {'DL': 5, 'LB': 3, 'DB': 2} |
| fantasy_positions[0] | excl_reserve_taxi | 6 | {'DL': 4, 'LB': 2, 'DB': 2} |
| all_eligible | all | 16 | {'DL': 5, 'LB': 5, 'DB': 3} |
| all_eligible | excl_reserve | 14 | {'DL': 4, 'LB': 4, 'DB': 3} |
| all_eligible | excl_taxi | 15 | {'DL': 5, 'LB': 5, 'DB': 2} |
| all_eligible | excl_reserve_taxi | 13 | {'DL': 4, 'LB': 4, 'DB': 2} |

A violation rules a hypothesis out (unless eligibility changed after the add). 0 violations under several rules is inconclusive unless a *discriminating* roster exists. `all_eligible` counts a dual-eligible player at every slot he can fill.

| roster | subset | slot | limit | counts by rule |
|---|---|---|---|---|
| 1 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 1 | excl_reserve | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 1 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 1 | excl_reserve_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 2 | all | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 2 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 4} |
| 2 | excl_reserve | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 2 | excl_reserve | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 4} |
| 2 | excl_taxi | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 2 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 4} |
| 2 | excl_reserve_taxi | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 2 | excl_reserve_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 4} |
| 3 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 3 | excl_reserve | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 3 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 3 | excl_reserve_taxi | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 7 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 7 | excl_reserve | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 7 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 7 | excl_reserve_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 8 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 8 | excl_reserve | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 8 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 8 | excl_reserve_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 2, 'all_eligible': 3} |
| 9 | all | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 9 | all | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 9 | excl_reserve | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 9 | excl_reserve | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
| 9 | excl_taxi | DL | 2 | {'position': 2, 'fantasy_positions[0]': 3, 'all_eligible': 3} |
| 9 | excl_taxi | LB | 2 | {'position': 2, 'fantasy_positions[0]': 1, 'all_eligible': 3} |
<details><summary>violations under 'position' (all rostered)</summary>

```json
[
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "10885",
    "name": "Lukas Van Ness",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12562",
    "name": "Nic Scourton",
    "position": "DL",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12573",
    "name": "Mason Graham",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5862",
    "name": "Brian Burns",
    "position": "DE",
    "reserve": true,
    "taxi": false
   }
  ],
  "roster_id": 3,
  "slot": "DL"
 },
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "10892",
    "name": "Will Anderson",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "11665",
    "name": "Jared Verse",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "2227",
    "name": "Aaron Donald",
    "position": "DT",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5840",
    "name": "Josh Hines-Allen",
    "position": "DE",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 5,
  "slot": "DL"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DB",
     "WR"
    ],
    "id": "12530",
    "name": "Travis Hunter",
    "position": "DB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DB"
    ],
    "id": "13371",
    "name": "Dillon Thieneman",
    "position": "DB",
    "reserve": false,
    "taxi": true
   },
   {
    "fantasy_positions": [
     "DB"
    ],
    "id": "4081",
    "name": "Budda Baker",
    "position": "DB",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 5,
  "slot": "DB"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "10888",
    "name": "Jalen Carter",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "11760",
    "name": "Austin Booker",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "3973",
    "name": "Myles Garrett",
    "position": "DE",
    "reserve": true,
    "taxi": false
   }
  ],
  "roster_id": 10,
  "slot": "DL"
 },
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "11687",
    "name": "Edgerrin Cooper",
    "position": "LB",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "12617",
    "name": "Demetrius Knight",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "7640",
    "name": "Micah Parsons",
    "position": "LB",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "8329",
    "name": "Devin Lloyd",
    "position": "LB",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 12,
  "slot": "LB"
 }
]
```
</details>
<details><summary>violations under 'fantasy_positions[0]' (all rostered)</summary>

```json
[
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "11667",
    "name": "Dallas Turner",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "2393",
    "name": "Danielle Hunter",
    "position": "DE",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5912",
    "name": "Carl Granderson",
    "position": "DE",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 2,
  "slot": "DL"
 },
 {
  "count": 5,
  "players": [
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "10885",
    "name": "Lukas Van Ness",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12562",
    "name": "Nic Scourton",
    "position": "DL",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12573",
    "name": "Mason Graham",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "13363",
    "name": "David Bailey",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5862",
    "name": "Brian Burns",
    "position": "DE",
    "reserve": true,
    "taxi": false
   }
  ],
  "roster_id": 3,
  "slot": "DL"
 },
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "10892",
    "name": "Will Anderson",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "11665",
    "name": "Jared Verse",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "2227",
    "name": "Aaron Donald",
    "position": "DT",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5840",
    "name": "Josh Hines-Allen",
    "position": "DE",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 5,
  "slot": "DL"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DB",
     "WR"
    ],
    "id": "12530",
    "name": "Travis Hunter",
    "position": "DB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DB"
    ],
    "id": "13371",
    "name": "Dillon Thieneman",
    "position": "DB",
    "reserve": false,
    "taxi": true
   },
   {
    "fantasy_positions": [
     "DB"
    ],
    "id": "4081",
    "name": "Budda Baker",
    "position": "DB",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 5,
  "slot": "DB"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "10917",
    "name": "Byron Young",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "12574",
    "name": "Abdul Carter",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "8385",
    "name": "George Karlaftis",
    "position": "DL",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 9,
  "slot": "DL"
 },
 {
  "count": 5,
  "players": [
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "10888",
    "name": "Jalen Carter",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "11760",
    "name": "Austin Booker",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "13381",
    "name": "Jaishawn Barham",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_posit
… (truncated; see report.json)
```
</details>
<details><summary>violations under 'all_eligible' (all rostered)</summary>

```json
[
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "10898",
    "name": "Tuli Tuipulotu",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "11742",
    "name": "Cedric Gray",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "12578",
    "name": "Carson Schwesinger",
    "position": "LB",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 1,
  "slot": "LB"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "11667",
    "name": "Dallas Turner",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "2393",
    "name": "Danielle Hunter",
    "position": "DE",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5912",
    "name": "Carl Granderson",
    "position": "DE",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 2,
  "slot": "DL"
 },
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "11667",
    "name": "Dallas Turner",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "2393",
    "name": "Danielle Hunter",
    "position": "DE",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "5041",
    "name": "Fred Warner",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5912",
    "name": "Carl Granderson",
    "position": "DE",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 2,
  "slot": "LB"
 },
 {
  "count": 5,
  "players": [
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "10885",
    "name": "Lukas Van Ness",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12562",
    "name": "Nic Scourton",
    "position": "DL",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "12573",
    "name": "Mason Graham",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "13363",
    "name": "David Bailey",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5862",
    "name": "Brian Burns",
    "position": "DE",
    "reserve": true,
    "taxi": false
   }
  ],
  "roster_id": 3,
  "slot": "DL"
 },
 {
  "count": 3,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "13363",
    "name": "David Bailey",
    "position": "LB",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "5862",
    "name": "Brian Burns",
    "position": "DE",
    "reserve": true,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "LB"
    ],
    "id": "7672",
    "name": "Ernest Jones",
    "position": "LB",
    "reserve": false,
    "taxi": false
   }
  ],
  "roster_id": 3,
  "slot": "LB"
 },
 {
  "count": 4,
  "players": [
   {
    "fantasy_positions": [
     "DL",
     "LB"
    ],
    "id": "10892",
    "name": "Will Anderson",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "11665",
    "name": "Jared Verse",
    "position": "DL",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy_positions": [
     "DL"
    ],
    "id": "2227",
    "name": "Aaron Donald",
    "position": "DT",
    "reserve": false,
    "taxi": false
   },
   {
    "fantasy
… (truncated; see report.json)
```
</details>

Dual-eligible rostered players: **20**

| roster | player | team | position | fantasy_positions | reserve | taxi |
|---|---|---|---|---|---|---|
| 1 | Tuli Tuipulotu | LAC | DL | ['DL', 'LB'] | False | False |
| 2 | Danielle Hunter | HOU | DE | ['DL', 'LB'] | False | False |
| 2 | Carl Granderson | NO | DE | ['DL', 'LB'] | False | False |
| 2 | Dallas Turner | MIN | LB | ['DL', 'LB'] | False | False |
| 3 | David Bailey | NYJ | LB | ['DL', 'LB'] | False | False |
| 3 | Brian Burns | NYG | DE | ['DL', 'LB'] | True | False |
| 5 | Will Anderson | HOU | DL | ['DL', 'LB'] | False | False |
| 5 | Josh Hines-Allen | JAX | DE | ['DL', 'LB'] | False | False |
| 7 | Nik Bonitto | DEN | LB | ['DL', 'LB'] | False | False |
| 7 | Greg Rousseau | BUF | DE | ['DL', 'LB'] | False | False |
| 8 | Donovan Ezeiruaku | DAL | DL | ['DL', 'LB'] | False | False |
| 9 | Abdul Carter | NYG | DL | ['DL', 'LB'] | False | False |
| 9 | Byron Young | LAR | LB | ['DL', 'LB'] | False | False |
| 10 | T.J. Watt | PIT | LB | ['DL', 'LB'] | False | False |
| 10 | Jaishawn Barham | DAL | LB | ['DL', 'LB'] | False | False |
| 11 | Rueben Bain | TB | DL | ['DL', 'LB'] | False | False |
| 11 | Andrew Van Ginkel | MIN | LB | ['DL', 'LB'] | False | False |
| 11 | Arvell Reese | NYG | LB | ['DL', 'LB'] | False | False |
| 12 | Boye Mafe | CIN | DL | ['DL', 'LB'] | False | False |
| 12 | Micah Parsons | GB | LB | ['DL', 'LB'] | True | False |
<details><summary>fantasy_positions orderings among dual-eligible players</summary>

```json
{
 "DB/DL": 3,
 "DB/LB": 48,
 "DL/LB": 235,
 "DL/LB/LEO": 2
}
```
</details>

Players whose `position` and `fantasy_positions[0]` map to different slots: **160**


## 3. Waiver timing
<details><summary>observed waiver processing</summary>

```json
{
 "transaction_types": {
  "free_agent": 82,
  "trade": 54,
  "waiver": 117
 },
 "waiver_processed_at_pt": {
  "Fri 13:00 PT": 3,
  "Fri 14:00 PT": 1,
  "Sat 09:00 PT": 1,
  "Sun 08:00 PT": 3,
  "Wed 00:00 PT": 108,
  "Wed 23:00 PT": 1
 },
 "waiver_processed_at_utc": {
  "Fri 20:00 UTC": 3,
  "Fri 21:00 UTC": 1,
  "Sat 16:00 UTC": 1,
  "Sun 15:00 UTC": 3,
  "Thu 06:00 UTC": 1,
  "Wed 07:00 UTC": 108
 },
 "waiver_statuses": {
  "complete": 75,
  "failed": 42
 }
}
```
</details>

<details><summary>failed waiver claims: Sleeper's reasons (D14 evidence if they cite limits)</summary>

```json
{
 "This player was claimed by another owner.": 14,
 "Unfortunately, your roster will have too many players after this transaction.": 28
}
```
</details>


## 4. Week rollover
| source | value |
|---|---|
| target_week_from_schedule | 5 |
| settled_week_from_schedule | 4 |
| state_week | 5 |
| state_display_week | 5 |
| week_used | 5 |
| settled_week_used | 4 |
| settled_source | schedule |

Rollover N→N+1 lies between leg N's last and leg N+1's first free-agent/trade transaction:

| leg | first | last | n |
|---|---|---|---|
| 1 | Mon 2026-03-23 21:07 ET | Wed 2026-09-09 16:14 ET | 72 |
| 2 | Wed 2026-09-16 13:18 ET | Mon 2026-09-21 07:39 ET | 6 |
| 3 | Wed 2026-09-23 08:48 ET | Fri 2026-09-25 15:50 ET | 16 |
| 4 | Wed 2026-09-30 12:06 ET | Sun 2026-10-04 11:31 ET | 24 |
| 5 | Wed 2026-10-07 07:07 ET | Sun 2026-10-11 12:29 ET | 18 |

<details><summary>Sleeper /state/nfl</summary>

```json
{
 "display_week": 5,
 "league_create_season": "2026",
 "league_season": "2026",
 "leg": 5,
 "previous_season": "2025",
 "season": "2026",
 "season_has_scores": true,
 "season_start_date": "2026-09-09",
 "season_type": "regular",
 "week": 5
}
```
</details>


## 5. Injury codes (IDP players on NFL teams)
<details><summary>tallies</summary>

```json
{
 "active": {
  "False": 12,
  "True": 1297
 },
 "idp_players_on_teams": 1309,
 "injury_status": {
  "COV": 2,
  "IR": 123,
  "NA": 2,
  "Out": 96,
  "PUP": 19,
  "Questionable": 38,
  "Sus": 1,
  "null": 1028
 },
 "status": {
  "Active": 1163,
  "Inactive": 146
 }
}
```
</details>


## 6. Scoring ground truth and bonus semantics
Week **4**, rostered IDPs with Sleeper points: **76**

| stats endpoint | compared | exact, non-trivial (as delivered) | exact, non-trivial (derived bonuses) | max diff | max diff (derived) | bonus hits |
|---|---|---|---|---|---|---|
| v1 | 76 | 69/69 | 69/69 | 0.0 | 0.0 | {} |
| api | 76 | 69/69 | 69/69 | 0.0 | 0.0 | {} |

_Non-trivial = the player scored or recorded a stat; idle bench players match trivially._
<details><summary>relevant scoring keys (recorded by IDPs that week)</summary>

```json
{
 "idp_blk_kick": {
  "coefficient": 3.0,
  "players_with_stat": 2
 },
 "idp_def_td": {
  "coefficient": 6.0,
  "players_with_stat": 1
 },
 "idp_ff": {
  "coefficient": 3.0,
  "players_with_stat": 24
 },
 "idp_fum_rec": {
  "coefficient": 3.0,
  "players_with_stat": 11
 },
 "idp_int": {
  "coefficient": 6.0,
  "players_with_stat": 25
 },
 "idp_pass_def": {
  "coefficient": 3.0,
  "players_with_stat": 128
 },
 "idp_qb_hit": {
  "coefficient": 1.0,
  "players_with_stat": 142
 },
 "idp_sack": {
  "coefficient": 6.0,
  "players_with_stat": 73
 },
 "idp_tkl_ast": {
  "coefficient": 1.0,
  "players_with_stat": 390
 },
 "idp_tkl_loss": {
  "coefficient": 2.0,
  "players_with_stat": 118
 },
 "idp_tkl_solo": {
  "coefficient": 2.0,
  "players_with_stat": 419
 },
 "st_ff": {
  "coefficient": 1.0,
  "players_with_stat": 4
 },
 "st_fum_rec": {
  "coefficient": 1.0,
  "players_with_stat": 3
 }
}
```
</details>
<details><summary>bonus consistency</summary>

```json
{
 "bonus_keys": {
  "bonus_sack_2p": {
   "present=False,threshold_met=False": 531,
   "present=True,threshold_met=True": 3
  },
  "bonus_tkl_10p": {
   "present=False,threshold_met=False": 515,
   "present=True,threshold_met=True": 19,
   "present_and_solo_ge_10": 1
  },
  "idp_pass_def_3p": {
   "present=False,threshold_met=False": 529,
   "present=True,threshold_met=True": 5
  }
 },
 "idp_tkl_equals_solo_plus_ast": 512,
 "idp_tkl_present": 512,
 "other_bonus_like_keys": {
  "bonus_def_int_td_50p": 1
 },
 "rows": 534
}
```
</details>


No threshold-bonus hit that week, so the previous week was checked too:

Week **3**, rostered IDPs with Sleeper points: **73**

| stats endpoint | compared | exact, non-trivial (as delivered) | exact, non-trivial (derived bonuses) | max diff | max diff (derived) | bonus hits |
|---|---|---|---|---|---|---|
| v1 | 73 | 67/67 | 67/67 | 0.0 | 0.0 | {} |
| api | 73 | 67/67 | 67/67 | 0.0 | 0.0 | {} |

_Non-trivial = the player scored or recorded a stat; idle bench players match trivially._
<details><summary>relevant scoring keys (recorded by IDPs that week)</summary>

```json
{
 "fum_lost": {
  "coefficient": -2.0,
  "players_with_stat": 1
 },
 "idp_blk_kick": {
  "coefficient": 3.0,
  "players_with_stat": 3
 },
 "idp_def_td": {
  "coefficient": 6.0,
  "players_with_stat": 2
 },
 "idp_ff": {
  "coefficient": 3.0,
  "players_with_stat": 25
 },
 "idp_fum_rec": {
  "coefficient": 3.0,
  "players_with_stat": 14
 },
 "idp_int": {
  "coefficient": 6.0,
  "players_with_stat": 23
 },
 "idp_pass_def": {
  "coefficient": 3.0,
  "players_with_stat": 129
 },
 "idp_qb_hit": {
  "coefficient": 1.0,
  "players_with_stat": 133
 },
 "idp_sack": {
  "coefficient": 6.0,
  "players_with_stat": 58
 },
 "idp_tkl_ast": {
  "coefficient": 1.0,
  "players_with_stat": 416
 },
 "idp_tkl_loss": {
  "coefficient": 2.0,
  "players_with_stat": 109
 },
 "idp_tkl_solo": {
  "coefficient": 2.0,
  "players_with_stat": 408
 },
 "st_ff": {
  "coefficient": 1.0,
  "players_with_stat": 3
 },
 "st_fum_rec": {
  "coefficient": 1.0,
  "players_with_stat": 3
 }
}
```
</details>
<details><summary>bonus consistency</summary>

```json
{
 "bonus_keys": {
  "bonus_sack_2p": {
   "present=False,threshold_met=False": 525,
   "present=True,threshold_met=True": 6
  },
  "bonus_tkl_10p": {
   "present=False,threshold_met=False": 512,
   "present=True,threshold_met=True": 19,
   "present_and_solo_ge_10": 2
  },
  "idp_pass_def_3p": {
   "present=False,threshold_met=False": 526,
   "present=True,threshold_met=True": 5
  }
 },
 "idp_tkl_equals_solo_plus_ast": 513,
 "idp_tkl_present": 513,
 "other_bonus_like_keys": {
  "bonus_def_int_td_50p": 2
 },
 "rows": 531
}
```
</details>


## 7. Sleeper projections (week 5)

| query | rows | with gp | with opponent | company | positions | non-integer solo share |
|---|---|---|---|---|---|---|
| slot_positions | 4357 | 628 | 628 | {'rotowire': 4357} | {'LB': 957, 'DB': 856, 'CB': 771, 'DE': 542, 'DT': 526, 'DL': 303, 'OLB': 123, 'SS': 70, 'ILB': 64, 'FS': 60, 'NT': 47, 'S': 23, 'OL': 5, 'LS': 3, 'G': 3, 'T': 2, 'K': 1, 'TE': 1} | 0.989 |
| granular_positions | 1252 | 193 | 193 | {'rotowire': 1252} | {'LB': 943, 'OLB': 123, 'DE': 80, 'ILB': 64, 'DL': 15, 'DB': 14, 'DT': 6, 'SS': 3, 'LS': 2, 'OL': 1, 'S': 1} | 0.99 |

<details><summary>key fill rates (slot query)</summary>

```json
{
 "bonus_rec_wr": {
  "nonzero": 0.0,
  "present": 0.0
 },
 "def_fum_td": {
  "nonzero": 0.001,
  "present": 0.002
 },
 "def_kr_yd": {
  "nonzero": 0.0,
  "present": 0.0
 },
 "def_pr_td": {
  "nonzero": 0.009,
  "present": 0.009
 },
 "def_pr_yd": {
  "nonzero": 0.033,
  "present": 0.033
 },
 "gp": {
  "nonzero": 0.144,
  "present": 0.144
 },
 "idp_blk_kick": {
  "nonzero": 0.0,
  "present": 0.0
 },
 "idp_ff": {
  "nonzero": 0.049,
  "present": 0.049
 },
 "idp_fum_rec": {
  "nonzero": 0.036,
  "present": 0.036
 },
 "idp_fum_ret_yd": {
  "nonzero": 0.001,
  "present": 0.001
 },
 "idp_int": {
  "nonzero": 0.045,
  "present": 0.045
 },
 "idp_int_ret_yd": {
  "nonzero": 0.032,
  "present": 0.032
 },
 "idp_pass_def": {
  "nonzero": 0.093,
  "present": 0.093
 },
 "idp_qb_hit": {
  "nonzero": 0.076,
  "present": 0.076
 },
 "idp_sack": {
  "nonzero": 0.077,
  "present": 0.077
 },
 "idp_sack_yd": {
  "nonzero": 0.073,
  "present": 0.073
 },
 "idp_safe": {
  "nonzero": 0.001,
  "present": 0.001
 },
 "idp_tkl": {
  "nonzero": 0.144,
  "present": 0.144
 },
 "idp_tkl_ast": {
  "nonzero": 0.144,
  "present": 0.144
 },
 "idp_tkl_loss": {
  "nonzero": 0.116,
  "present": 0.116
 },
 "idp_tkl_solo": {
  "nonzero": 0.144,
  "present": 0.144
 }
}
```
</details>


### Past-week projections (week 1)
<details><summary>week 1 retrievability</summary>

```json
{
 "mean_abs_diff_solo_tackles": 1.217,
 "players_with_projection_and_actual": 430,
 "shape": {
  "companies": {
   "rotowire": 4357
  },
  "idp_tkl_solo_noninteger_share": 0.998,
  "key_fill_rates": {
   "bonus_rec_wr": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "def_fum_td": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "def_kr_yd": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "def_pr_td": {
    "nonzero": 0.007,
    "present": 0.007
   },
   "def_pr_yd": {
    "nonzero": 0.029,
    "present": 0.029
   },
   "gp": {
    "nonzero": 0.101,
    "present": 0.101
   },
   "idp_blk_kick": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "idp_ff": {
    "nonzero": 0.05,
    "present": 0.05
   },
   "idp_fum_rec": {
    "nonzero": 0.035,
    "present": 0.035
   },
   "idp_fum_ret_yd": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "idp_int": {
    "nonzero": 0.042,
    "present": 0.042
   },
   "idp_int_ret_yd": {
    "nonzero": 0.029,
    "present": 0.029
   },
   "idp_pass_def": {
    "nonzero": 0.086,
    "present": 0.086
   },
   "idp_qb_hit": {
    "nonzero": 0.074,
    "present": 0.074
   },
   "idp_sack": {
    "nonzero": 0.067,
    "present": 0.067
   },
   "idp_sack_yd": {
    "nonzero": 0.064,
    "present": 0.064
   },
   "idp_safe": {
    "nonzero": 0.0,
    "present": 0.0
   },
   "idp_tkl": {
    "nonzero": 0.101,
    "present": 0.101
   },
   "idp_tkl_ast": {
    "nonzero": 0.101,
    "present": 0.101
   },
   "idp_tkl_loss": {
    "nonzero": 0.094,
    "present": 0.094
   },
   "idp_tkl_solo": {
    "nonzero": 0.101,
    "present": 0.101
   }
  },
  "positions": {
   "CB": 771,
   "DB": 856,
   "DE": 542,
   "DL": 303,
   "DT": 526,
   "FS": 60,
   "G": 3,
   "ILB": 64,
   "K": 1,
   "LB": 957,
   "LS": 3,
   "NT": 47,
   "OL": 5,
   "OLB": 123,
   "S": 23,
   "SS": 70,
   "T": 2,
   "TE": 1
  },
  "row_keys": [
   "category",
   "company",
   "date",
   "game_id",
   "last_modified",
   "opponent",
   "player",
   "player_id",
   "season",
   "season_type",
   "sport",
   "stats",
   "status",
   "team",
   "updated_at",
   "week",
   "week_shard"
  ],
  "rows": 4357,
  "with_gp": 441,
  "with_opponent": 441
 },
 "share_identical_to_actual": 0.002
}
```
</details>

<details><summary>stats endpoints</summary>

```json
{
 "week3": {
  "api_shapes": {
   "granular": {
    "companies": {
     "sportradar": 301
    },
    "idp_tkl_solo_noninteger_share": 0.0,
    "key_fill_rates": {
     "bonus_def_int_td_50p": {
      "nonzero": 0.003,
      "present": 0.003
     },
     "bonus_sack_2p": {
      "nonzero": 0.01,
      "present": 0.01
     },
     "bonus_tkl_10p": {
      "nonzero": 0.037,
      "present": 0.037
     },
     "def_snp": {
      "nonzero": 0.558,
      "present": 0.558
     },
     "gp": {
      "nonzero": 0.734,
      "present": 0.734
     },
     "gs": {
      "nonzero": 0.372,
      "present": 0.372
     },
     "idp_def_td": {
      "nonzero": 0.003,
      "present": 0.003
     },
     "idp_ff": {
      "nonzero": 0.04,
      "present": 0.04
     },
     "idp_fum_rec": {
      "nonzero": 0.013,
      "present": 0.013
     },
     "idp_fum_ret_yd": {
      "nonzero": 0.007,
      "present": 0.007
     },
     "idp_int": {
      "nonzero": 0.017,
      "present": 0.017
     },
     "idp_int_ret_yd": {
      "nonzero": 0.01,
      "present": 0.01
     },
     "idp_pass_def": {
      "nonzero": 0.103,
      "present": 0.103
     },
     "idp_pass_def_3p": {
      "nonzero": 0.003,
      "present": 0.003
     },
     "idp_qb_hit": {
      "nonzero": 0.183,
      "present": 0.183
     },
     "idp_sack": {
      "nonzero": 0.083,
      "present": 0.083
     },
     "idp_sack_yd": {
      "nonzero": 0.08,
      "present": 0.08
     },
     "idp_tkl": {
      "nonzero": 0.475,
      "present": 0.475
     },
     "idp_tkl_ast": {
      "nonzero": 0.412,
      "present": 0.412
     },
     "idp_tkl_loss": {
      "nonzero": 0.163,
      "present": 0.163
     },
     "idp_tkl_solo": {
      "nonzero": 0.389,
      "present": 0.389
     },
     "st_ff": {
      "nonzero": 0.01,
      "present": 0.01
     },
     "st_fum_rec": {
      "nonzero": 0.007,
      "present": 0.007
     },
     "st_snp": {
      "nonzero": 0.555,
      "present": 0.555
     },
     "st_tkl_solo": {
      "nonzero": 0.096,
      "present": 0.096
     }
    },
    "positions": {
     "DB": 8,
     "DE": 17,
     "DL": 13,
     "DT": 3,
     "LB": 256,
     "LS": 2,
     "OL": 1,
     "SS": 1
    },
    "row_keys": [
     "category",
     "company",
     "date",
     "game_id",
     "last_modified",
     "opponent",
     "player",
     "player_id",
     "season",
     "season_type",
     "sport",
     "stats",
     "status",
     "team",
     "updated_at",
     "week",
     "week_shard"
    ],
    "rows": 301,
    "with_gp": 221,
    "with_opponent": 301
   },
   "slot": {
    "companies": {
     "sportradar": 1094
    },
    "idp_tkl_solo_noninteger_share": 0.0,
    "key_fill_rates": {
     "bonus_def_int_td_50p": {
      "nonzero": 0.002,
      "present": 0.002
     },
     "bonus_sack_2p": {
      "nonzero": 0.005,
      "present": 0.005
     },
     "bonus_tkl_10p": {
      "nonzero": 0.017,
      "present": 0.017
     },
     "def_snp": {
      "nonzero": 0.555,
      "present": 0.555
     },
     "gp": {
      "nonzero": 0.668,
      "present": 0.668
     },
     "gs": {
      "nonzero": 0.323,
      "present": 0.323
     },
     "idp_blk_kick": {
      "nonzero": 0.003,
      "present": 0.003
     },
     "idp_def_td": {
      "nonzero": 0.002,
      "present": 0.002
     },
     "idp_ff": {
      "nonzero": 0.023,
      "present": 0.023
     },
     "idp_fum_rec": {
      "nonzero": 0.013,
      "present": 0.013
     },
     "idp_fum_ret_yd": {
      "nonzero": 0.002,
      "present": 0.002
     },
     "idp_int": {
      "nonzero": 0.021,
      "present": 0.021
     },
     "idp_int_ret_yd": {
      "nonzero": 0.013,
      "present": 0.013
     },
     "idp_pass_def": {
      "nonzero": 0.118,
      "present": 0.118
     },
     "idp_pass_def_3p": {
      "nonzero": 0.005,
      "present": 0.005
     },
     "idp_qb_hit": {
      "nonzero": 0.122,
      "present": 0.122
     },
     "idp_sack": {
      "nonzero": 0.053,
      "present": 0.053
     },
     
… (truncated; see report.json)
```
</details>


## 8. Schedule and ID crosswalk

<details><summary>nflverse schedule</summary>

```json
{
 "byes_in_target_week": [
  "CAR",
  "KC"
 ],
 "columns": [
  "game_id",
  "season",
  "game_type",
  "week",
  "gameday",
  "weekday",
  "gametime",
  "away_team",
  "away_score",
  "home_team",
  "home_score",
  "location",
  "result",
  "total",
  "overtime",
  "old_game_id",
  "gsis",
  "nfl_detail_id",
  "pfr",
  "pff",
  "espn",
  "ftn",
  "away_rest",
  "home_rest",
  "away_moneyline",
  "home_moneyline",
  "spread_line",
  "away_spread_odds",
  "home_spread_odds",
  "total_line",
  "under_odds",
  "over_odds",
  "div_game",
  "roof",
  "surface",
  "temp",
  "wind",
  "away_qb_id",
  "home_qb_id",
  "away_qb_name",
  "home_qb_name",
  "away_coach",
  "home_coach",
  "referee",
  "stadium_id",
  "stadium"
 ],
 "gametime_examples": [
  "09:30",
  "13:00",
  "15:00",
  "16:05",
  "16:25",
  "16:30",
  "17:00",
  "20:00",
  "20:15",
  "20:20",
  "20:35"
 ],
 "missing_kickoff": 0,
 "season_games": 272,
 "settled_week": 4,
 "source": {
  "bytes": 2184052,
  "content_type": "text/plain; charset=utf-8",
  "error": null,
  "final_url": "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
  "status": 200,
  "url": "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
 },
 "target_week": 5,
 "teams": [
  "ARI",
  "ATL",
  "BAL",
  "BUF",
  "CAR",
  "CHI",
  "CIN",
  "CLE",
  "DAL",
  "DEN",
  "DET",
  "GB",
  "HOU",
  "IND",
  "JAX",
  "KC",
  "LA",
  "LAC",
  "LV",
  "MIA",
  "MIN",
  "NE",
  "NO",
  "NYG",
  "NYJ",
  "PHI",
  "PIT",
  "SEA",
  "SF",
  "TB",
  "TEN",
  "WAS"
 ]
}
```
</details>

<details><summary>DynastyProcess crosswalk</summary>

```json
{
 "bytes": 2639486,
 "columns": [
  "mfl_id",
  "sportradar_id",
  "fantasypros_id",
  "gsis_id",
  "pff_id",
  "sleeper_id",
  "nfl_id",
  "espn_id",
  "yahoo_id",
  "fleaflicker_id",
  "cbs_id",
  "pfr_id",
  "cfbref_id",
  "rotowire_id",
  "rotoworld_id",
  "ktc_id",
  "stats_id",
  "stats_global_id",
  "fantasy_data_id",
  "swish_id",
  "name",
  "merge_name",
  "position",
  "team",
  "birthdate",
  "age",
  "draft_year",
  "draft_round",
  "draft_pick",
  "draft_ovr",
  "twitter_username",
  "height",
  "weight",
  "college",
  "db_season"
 ],
 "content_type": "text/plain; charset=utf-8",
 "error": null,
 "final_url": "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv",
 "idp_rows": 6538,
 "share_with_espn_id": 0.677,
 "share_with_fantasypros_id": 0.339,
 "share_with_gsis_id": 0.672,
 "share_with_sleeper_id": 0.507,
 "status": 200,
 "url": "https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv"
}
```
</details>


## 9. Candidate sources

| source | status | error | tables | first table headers | rows | week labels | markers | followed |
|---|---|---|---|---|---|---|---|---|
| fantasynerds_idp_weekly | 200 |  | 1 | PLAYER, TEAM, POS, TKLS, ASS TKLS, SACKS, PASS DEF, INT, PTS | 0 | [5, 6] | {'login_prompt': True, 'paywall_or_upgrade': True} |  |
| razzball_idp_ros | 200 |  | 6 |  | 7 | [] | {'login_prompt': True, 'paywall_or_upgrade': True} |  |
| razzball_idp_weekly | 404 (guessed URL) |  | 0 |  |  |  | {} |  |
| espn_idp_rankings_article | 202 |  | 0 |  |  | [] | {} |  |
| idpguru | 200 |  | 0 |  |  | [1, 2, 3, 4, 5] | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | https://idpguru.com/plans/premium-content-pricing-plans/?access=denied&mepr-unauth-page=8466&redirect_to=%2F2026%2F09%2F2026-weekly-idp-rankings%2F |
| dynastynerds_idp | 200 |  | 0 |  |  | [1, 2, 3, 4, 5, 6] | {'login_prompt': True, 'paywall_or_upgrade': True} | https://www.dynastynerds.com/idp/week-5-idp-start-sit-2026/ |
| rotoballer_idp | 200 |  | 0 |  |  | [1, 2, 3, 4, 5, 6] | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True, 'bot_challenge': True} | https://www.rotoballer.com/week-5-idp-rankings-streamers-start-sit-fantasy-football-2026/1959614 |
| thehuddle_idp | 402 |  | 0 |  |  |  | {} |  |
| razzball_idp | 200 |  | 2 |  | 2 | [3, 4, 5] | {'login_prompt': True, 'paywall_or_upgrade': True} | https://football.razzball.com/projections-idp-restofseason/ |

<details><summary>index pages: matching same-site links</summary>

```json
{
 "dynastynerds_idp": [
  {
   "href": "https://www.dynastynerds.com/idp/week-5-idp-start-sit-2026/",
   "text": "IDP Week 5 IDP Start/Sit Fantasy Football 2026 | Lineup Advice and Hidden Gems Jon Glosser \u00b7 Oct 9, 2026 Set your Week 5 IDP fantasy football lineups with our t"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-5-idp-waiver-wire-2026/",
   "text": "IDP Week 5 IDP Waiver Wire Pickups | Top DL, LB, DB Fantasy Football Adds Jon Glosser \u00b7 Oct 6, 2026 The Week 5 IDP Waiver Wire is here with the best defensive l"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-4-idp-start-sit-2026/",
   "text": "IDP Week 4 IDP Start/Sit | Lineup Advice and Hidden IDP Gems Jon Glosser \u00b7 Oct 2, 2026 The Week 4 IDP Start/Sit is here, featuring DL, LB, and DB players to sta"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-4-idp-waiver-wire-2026/",
   "text": "IDP Week 4 IDP Waiver Wire Pickups | Top DL, LB, DB Fantasy Football Adds Jon Glosser \u00b7 Sep 29, 2026 The Week 4 IDP Waiver Wire is here. Find the best defensive"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-3-idp-start-sit/",
   "text": "IDP Week 3 IDP Start/Sit | Lineup Advice and Hidden IDP Gems Jon Glosser \u00b7 Sep 25, 2026 The Week 3 IDP Start/Sit is here with six players to start, three to sit"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-3-idp-waiver-wire-2026/",
   "text": "IDP Week 3 IDP Waiver Wire Pickups | Top DL, LB, DB Fantasy Football Adds Jon Glosser \u00b7 Sep 22, 2026 The Week 3 IDP Waiver Wire is here. Find the best defensive"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-2-idp-start-sit-2026/",
   "text": "IDP Week 2 IDP Start/Sit | Streamers, Sleepers, & Hidden Gems Jon Glosser \u00b7 Sep 18, 2026 IDP Director Jon Glosser brings you his Week 2 IDP Start/Sit along with"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-2-idp-waiver-wire-2026/",
   "text": "IDP Week 2 IDP Waiver Wire Pickups | Top DL, LB, DB Fantasy Football Adds Jon Glosser \u00b7 Sep 15, 2026 Need help with the Week 2 IDP waiver wire? We break down th"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-1-idp-start-sit-2026/",
   "text": "IDP Week 1 IDP Start/Sit Fantasy Football 2026 | Lineup Advice & Streamers Jon Glosser \u00b7 Sep 11, 2026 IDP Director Jon Glosser is back with your starts, sits, a"
  },
  {
   "href": "https://www.dynastynerds.com/idp/week-1-idp-waiver-wire-2026/",
   "text": "IDP Week 1 IDP Waiver Wire Pickups | Top DL, LB, DB Fantasy Football Adds Jon Glosser \u00b7 Sep 8, 2026 Get ahead of your league with the top Week 1 IDP waiver wire"
  }
 ],
 "idpguru": [
  {
   "href": "https://idpguru.com/2026/09/2026-weekly-idp-rankings/",
   "text": "2026 Week 5 IDP Rankings"
  },
  {
   "href": "https://idpguru.com/2026/09/2026-rest-of-season-idp-rankings/",
   "text": "2026 Rest of Season IDP Rankings: Weeks 5-17"
  }
 ],
 "razzball_idp": [
  {
   "href": "https://football.razzball.com/projections-idp-restofseason/",
   "text": "IDP"
  },
  {
   "href": "https://football.razzball.com/pigskinonator-idp/",
   "text": "Individual Defense (IDP)"
  },
  {
   "href": "https://football.razzball.com/weekly-rankings-idp/",
   "text": "Individual Defense (IDP)"
  }
 ],
 "rotoballer_idp": [
  {
   "href": "https://www.rotoballer.com/week-5-idp-rankings-streamers-start-sit-fantasy-football-2026/1959614",
   "text": ""
  },
  {
   "href": "https://www.rotoballer.com/week-4-idp-rankings-streamers-start-sit-fantasy-football-2026/1953178",
   "text": ""
  },
  {
   "href": "https://www.rotoballer.com/week-3-idp-rankings-streamers-start-sit-fantasy-football-2026/1933908",
   "text": ""
  },
  {
   "href": "https://www.rotoballer.com/week-2-idp-rankings-streamers-start-sit-fantasy-football-2026/1932774",
   "text": ""
  },
  {
   "href": "https://www.rotoballer.com/week-1-idp-rankings-streamers-start-sit-fantasy-football-2026/1922997",
   "text": ""
  },
  {
   "href": "https://www.rotoballe
… (truncated; see report.json)
```
</details>

<details><summary>ESPN fantasy API (IDP slots)</summary>

```json
{
 "default": {
  "bytes": 11376,
  "content_type": "application/json;charset=utf-8",
  "error": null,
  "final_url": "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info",
  "key_paths": [
   "$: object",
   "players[]: array",
   "players[].draftAuctionValue: int",
   "players[].droppedByEliminatedTeam: bool",
   "players[].id: int",
   "players[].keeperValue: int",
   "players[].keeperValueFuture: int",
   "players[].lineupLocked: bool",
   "players[].onTeamId: int",
   "players[].player: object",
   "players[].player.active: bool",
   "players[].player.defaultPositionId: int",
   "players[].player.draftRanksByRankType: object",
   "players[].player.draftRanksByRankType.ELIMINATION: object",
   "players[].player.draftRanksByRankType.PPR: object",
   "players[].player.draftRanksByRankType.STANDARD: object",
   "players[].player.draftRanksByRankType.SUPERFLEX: object",
   "players[].player.droppable: bool",
   "players[].player.eligibleSlots[]: array",
   "players[].player.firstName: str",
   "players[].player.fullName: str",
   "players[].player.id: int",
   "players[].player.injured: bool",
   "players[].player.injuryStatus: str",
   "players[].player.jersey: str",
   "players[].player.lastName: str",
   "players[].player.lastNewsDate: int",
   "players[].player.outlooks: object",
   "players[].player.outlooks.outlooksByWeek: object",
   "players[].player.ownership: object",
   "players[].player.ownership.activityLevel: NoneType",
   "players[].player.ownership.auctionValueAverage: float",
   "players[].player.ownership.auctionValueAverageChange: float",
   "players[].player.ownership.averageDraftPosition: float",
   "players[].player.ownership.averageDraftPositionPercentChange: float",
   "players[].player.ownership.date: int",
   "players[].player.ownership.leagueType: int",
   "players[].player.ownership.percentChange: float",
   "players[].player.ownership.percentOwned: float",
   "players[].player.ownership.percentStarted: float",
   "players[].player.proTeamId: int",
   "players[].player.rankings: object",
   "players[].player.rankings.0[]: array",
   "players[].player.rankings.1[]: array",
   "players[].player.rankings.2[]: array",
   "players[].player.rankings.3[]: array",
   "players[].player.rankings.4[]: array",
   "players[].player.rankings.5[]: array",
   "players[].player.seasonOutlook: str",
   "players[].player.stats[]: array",
   "players[].ratings: object",
   "players[].ratings.0: object",
   "players[].ratings.0.positionalRanking: int",
   "players[].ratings.0.totalRanking: int",
   "players[].ratings.0.totalRating: float",
   "players[].rosterLocked: bool",
   "players[].status: str",
   "players[].tradeLocked: bool",
   "players[].waiverProcessDate: int"
  ],
  "shape": {
   "default_position_ids": {
    "3": 1
   },
   "players": 1,
   "players_with_week_projection": 1,
   "prior_week_actual_stat_ids": {
    "107": 1,
    "108": 1,
    "109": 1,
    "110": 1,
    "113": 1,
    "155": 1,
    "210": 1
   },
   "projection_stat_ids": {
    "100": 1,
    "103": 1,
    "104": 1,
    "105": 1,
    "106": 1,
    "107": 1,
    "108": 1,
    "109": 1,
    "110": 1,
    "112": 1,
    "113": 1,
    "93": 1,
    "94": 1,
    "95": 1,
    "96": 1,
    "97": 1,
    "98": 1,
    "99": 1
   }
  },
  "status": 200,
  "url": "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info"
 },
 "scoring_period": {
  "bytes": 11376,
  "content_type": "application/json;charset=utf-8",
  "error": null,
  "final_url": "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=5",
  "key_paths": [
   "$: object",
   "players[]: array",
   "players[].draftAuctionValue: int",
   "players[].droppedByEliminatedTeam: bool",
   "players[].id: int",
   "players[].keeperValue: int",
   "players[].keeperValueFuture: int",
   "players[].l
… (truncated; see report.json)
```
</details>


### FantasyPros (structure only)
cookie configured: **False** · login credentials configured: **False** · encryption configured: **False**

#### Auto-login feasibility
| question | answer |
|---|---|
| login page reachable / form found | {'https://secure.fantasypros.com/accounts/login/': (200, True)} |
| bot protection on login page | {'https://secure.fantasypros.com/accounts/login/': {'turnstile': True}} |
| login attempted | False |
| login refused (safety check) | None |
| login POST | None |
| after login | None |
| **login succeeded** | None |
<details><summary>login form fields (names/types only)</summary>

```json
{
 "https://secure.fantasypros.com/accounts/login/": {
  "action_path": "/accounts/login/",
  "fields": [
   {
    "name": "csrfmiddlewaretoken",
    "type": "hidden"
   },
   {
    "name": "username",
    "type": "text"
   },
   {
    "name": "password",
    "type": "password"
   },
   {
    "name": "token",
    "type": "hidden"
   },
   {
    "name": "import_id",
    "type": "hidden"
   }
  ],
  "has_hidden_token": true,
  "method": "post"
 }
}
```
</details>

#### Pages
| page | mode | status | error | final path | markers | week labels | table headers | ecrData players |
|---|---|---|---|---|---|---|---|---|
| rankings_idp | public | 200 |  | /nfl/rankings/idp.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [2, 3, 4, 5] | [] | 376 |
| rankings_dl | public | 200 |  | /nfl/rankings/dl.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [4, 5] | [] | 232 |
| rankings_lb | public | 200 |  | /nfl/rankings/lb.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [3, 4, 5] | [] | 167 |
| rankings_db | public | 200 |  | /nfl/rankings/db.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [2, 3, 4, 5] | [] | 164 |
| projections_idp | public | 200 |  | /nfl/projections/qb.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [5] | [['Player', 'ATT', 'CMP', 'YDS', 'TDS', 'INTS', 'ATT', 'YDS']] |  |
| projections_dl | public | 200 |  | /nfl/projections/dl.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [5] | [['Player', 'TACKLE', 'ASSIST', 'SACK', 'PD', 'INT', 'FF', 'FR']] |  |
| projections_lb | public | 200 |  | /nfl/projections/lb.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [5] | [['Player', 'TACKLE', 'ASSIST', 'SACK', 'PD', 'INT', 'FF', 'FR']] |  |
| projections_db | public | 200 |  | /nfl/projections/db.php | {'login_prompt': True, 'logout_link': True, 'paywall_or_upgrade': True} | [5] | [['Player', 'TACKLE', 'ASSIST', 'SACK', 'PD', 'INT', 'FF', 'FR']] |  |


### The Odds API
<details><summary>props availability</summary>

```json
{
 "reason": "ODDS_API_KEY not configured",
 "status": "skipped"
}
```
</details>
