"""Pure analysis functions for the probe (no network). Offline-tested in tests/probe/."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

POSITION_TO_SLOT = {
    "DL": "DL", "DE": "DL", "DT": "DL", "NT": "DL",
    "LB": "LB", "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "DB": "DB", "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB",
}  # fmt: skip
IDP_SLOTS = ("DL", "LB", "DB")
# Threshold bonus keys -> (stat used, threshold). Phase 1 verifies these semantics.
THRESHOLD_BONUSES = {
    "bonus_tkl_10p": ("tkl_total", 10),
    "bonus_sack_2p": ("idp_sack", 2),
    "idp_pass_def_3p": ("idp_pass_def", 3),
}
BONUS_LIKE = re.compile(r"^bonus|_\d+p$")


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def player_name(p: dict | None) -> str:
    if not p:
        return "?"
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "?"


# ---------------------------------------------------------------- positions (D14)


def slot_by_position(player: dict) -> str | None:
    return POSITION_TO_SLOT.get((player.get("position") or "").upper())


def slot_by_fantasy_first(player: dict) -> str | None:
    fps = player.get("fantasy_positions") or []
    return POSITION_TO_SLOT.get(fps[0].upper()) if fps else None


def idp_eligibility(player: dict) -> list[str]:
    out = []
    for fp in player.get("fantasy_positions") or []:
        slot = POSITION_TO_SLOT.get(str(fp).upper())
        if slot and slot not in out:
            out.append(slot)
    return out


def is_idp(player: dict) -> bool:
    return bool(slot_by_position(player) or idp_eligibility(player))


def position_limits(settings: dict) -> dict[str, int]:
    """{'DL': 2, ...} from any settings key named position_limit_<slot>."""
    out = {}
    for k, v in (settings or {}).items():
        if k.startswith("position_limit_") and isinstance(v, (int, float)):
            out[k.removeprefix("position_limit_").upper()] = int(v)
    return out


def _one(slot: str | None) -> list[str]:
    return [slot] if slot else []


D14_RULES = {
    "position": lambda p: _one(slot_by_position(p)),
    "fantasy_positions[0]": lambda p: _one(slot_by_fantasy_first(p)),
    # A dual-eligible player counts toward every slot he can fill. Counts under this rule are
    # always >= the others, so its violations can rule it out but 0 violations can't confirm it.
    "all_eligible": lambda p: sorted(set(idp_eligibility(p)) | set(_one(slot_by_position(p)))),
}


def _roster_sets(roster: dict) -> dict[str, set[str]]:
    players = set(roster.get("players") or [])
    reserve = set(roster.get("reserve") or [])
    taxi = set(roster.get("taxi") or [])
    return {
        "all": players | reserve | taxi,
        "excl_reserve": (players | taxi) - reserve,
        "excl_taxi": (players | reserve) - taxi,
        "excl_reserve_taxi": players - reserve - taxi,
    }


def d14_analysis(rosters: list[dict], players: dict[str, dict], limits: dict[str, int]) -> dict:
    """Which position rule keeps every SADL roster within its IDP limits?

    If Sleeper enforces limits by rule X, rosters should (barring mid-season eligibility
    changes) never exceed a limit when counted by X. A violation rules a hypothesis out;
    0 violations under several rules is inconclusive unless a discriminating roster exists.
    """
    if not players:
        return {"inconclusive": "players DB unavailable", "limits": limits}
    if not limits:
        return {"inconclusive": "no position_limit_* keys in league settings", "limits": {}}
    if not rosters:
        return {"inconclusive": "no rosters", "limits": limits}
    result: dict = {"limits": limits, "rules": {}, "discriminating": []}
    counts_by: dict[tuple, dict[str, int]] = {}
    for rule_name, rule in D14_RULES.items():
        per_variant: dict = {}
        for roster in rosters:
            reserve = set(roster.get("reserve") or [])
            taxi = set(roster.get("taxi") or [])
            for variant, ids in _roster_sets(roster).items():
                counts = Counter(s for pid in ids for s in rule(players.get(pid, {})))
                v = per_variant.setdefault(variant, {"violations": [], "max_seen": Counter()})
                for slot in IDP_SLOTS:
                    n = counts.get(slot, 0)
                    v["max_seen"][slot] = max(v["max_seen"][slot], n)
                    counts_by.setdefault((roster.get("roster_id"), variant, slot), {})[rule_name] = n
                    if slot in limits and n > limits[slot]:
                        v["violations"].append(
                            {
                                "roster_id": roster.get("roster_id"),
                                "slot": slot,
                                "count": n,
                                "players": [
                                    {
                                        "id": pid,
                                        "name": player_name(players.get(pid)),
                                        "position": players.get(pid, {}).get("position"),
                                        "fantasy_positions": players.get(pid, {}).get("fantasy_positions"),
                                        "reserve": pid in reserve,
                                        "taxi": pid in taxi,
                                    }
                                    for pid in sorted(ids)
                                    if slot in rule(players.get(pid, {}))
                                ],
                            }
                        )
        result["rules"][rule_name] = {
            k: {"violations": v["violations"], "max_seen": dict(v["max_seen"])} for k, v in per_variant.items()
        }
    for (roster_id, variant, slot), by_rule in counts_by.items():
        over = {rule: n > limits[slot] for rule, n in by_rule.items()} if slot in limits else {}
        if over and len(set(over.values())) > 1:
            result["discriminating"].append(
                {"roster_id": roster_id, "subset": variant, "slot": slot, "counts": by_rule, "limit": limits[slot]}
            )
    result["verdict"] = (
        "inconclusive: no roster distinguishes the rules"
        if not result["discriminating"]
        else "see discriminating rosters: rules with violations there are ruled out"
    )
    dual = []
    for roster in rosters:
        for pid in _roster_sets(roster)["all"]:
            p = players.get(pid, {})
            elig = idp_eligibility(p)
            if len(elig) >= 2:
                dual.append(
                    {
                        "roster_id": roster.get("roster_id"),
                        "player_id": pid,
                        "name": player_name(p),
                        "team": p.get("team"),
                        "position": p.get("position"),
                        "fantasy_positions": p.get("fantasy_positions"),
                        "in_reserve": pid in set(roster.get("reserve") or []),
                        "in_taxi": pid in set(roster.get("taxi") or []),
                    }
                )
    result["dual_eligible_rostered"] = dual
    order_tally: Counter = Counter()
    disagreements = 0
    for p in players.values():
        if len(idp_eligibility(p)) >= 2:
            order_tally["/".join(p.get("fantasy_positions") or [])] += 1
        a, b = slot_by_position(p), slot_by_fantasy_first(p)
        if a and b and a != b:
            disagreements += 1
    result["dual_fantasy_positions_orderings"] = dict(order_tally.most_common())
    result["players_where_position_and_fantasy_first_disagree"] = disagreements
    return result


# ---------------------------------------------------------------- scoring


def score_line(stats: dict, scoring: dict) -> float:
    return sum(num(coef) * num(stats.get(k)) for k, coef in scoring.items() if stats.get(k) is not None)


def tackles_total(stats: dict) -> float:
    if stats.get("idp_tkl") is not None:
        return num(stats["idp_tkl"])
    return num(stats.get("idp_tkl_solo")) + num(stats.get("idp_tkl_ast"))


def derive_bonuses(stats: dict, scoring: dict) -> dict:
    """Add threshold bonus keys that SADL scores but the stat line does not carry."""
    out = dict(stats)
    for key, (stat, threshold) in THRESHOLD_BONUSES.items():
        if key in scoring and key not in stats:
            value = tackles_total(stats) if stat == "tkl_total" else num(stats.get(stat))
            if value >= threshold:
                out[key] = 1
    return out


def _nontrivial(points: float, stats: dict) -> bool:
    return bool(points) or any(num(v) for v in stats.values())


def _bonus_hit(stats: dict, key: str) -> bool:
    stat, threshold = THRESHOLD_BONUSES[key]
    value = tackles_total(stats) if stat == "tkl_total" else num(stats.get(stat))
    return num(stats.get(key)) > 0 or value >= threshold


def ground_truth(
    stats_by_pid: dict[str, dict],
    players_points: dict[str, float],
    scoring: dict,
    idp_ids: set[str],
    players: dict[str, dict] | None = None,
    tol: float = 0.011,
) -> dict:
    """Compare SADL points computed from Sleeper stats with Sleeper's own players_points."""
    players = players or {}
    rows = []
    for pid in sorted(idp_ids & set(players_points)):
        st = stats_by_pid.get(pid) or {}
        sleeper = num(players_points[pid])
        delivered = round(score_line(st, scoring), 2)
        derived = round(score_line(derive_bonuses(st, scoring), scoring), 2)
        rows.append((pid, sleeper, delivered, derived, st))
    out = {
        "players_compared": len(rows),
        "match_as_delivered": sum(abs(s - d) <= tol for _, s, d, _, _ in rows),
        "match_with_derived_bonuses": sum(abs(s - d) <= tol for _, s, _, d, _ in rows),
        "max_abs_diff_as_delivered": max((abs(s - d) for _, s, d, _, _ in rows), default=0.0),
        "max_abs_diff_with_derived": max((abs(s - d) for _, s, _, d, _ in rows), default=0.0),
        # players with points or any recorded stat; 0-point bench players with no line match trivially
        "nontrivial_compared": sum(_nontrivial(s, st) for _, s, _, _, st in rows),
        "nontrivial_match_as_delivered": sum(_nontrivial(s, st) and abs(s - d) <= tol for _, s, d, _, st in rows),
        "nontrivial_match_with_derived": sum(_nontrivial(s, st) and abs(s - d) <= tol for _, s, _, d, st in rows),
        # threshold-bonus hits in this week (delivered key or threshold met); SPEC §10 needs >= 1
        "bonus_hits": {k: sum(_bonus_hit(st, k) for *_, st in rows) for k in THRESHOLD_BONUSES if num(scoring.get(k))},
        "mismatches": [],
    }
    for pid, s, d1, d2, st in rows:
        if abs(s - d1) > tol and abs(s - d2) > tol and len(out["mismatches"]) < 25:
            out["mismatches"].append(
                {
                    "player_id": pid,
                    "name": player_name(players.get(pid)),
                    "sleeper_points": s,
                    "ours_as_delivered": d1,
                    "ours_with_derived": d2,
                    "scored_keys": {k: st[k] for k in scoring if num(st.get(k)) and num(scoring[k])},
                }
            )
    return out


def relevant_keys(stat_lines: list[dict], scoring: dict) -> dict[str, dict]:
    """Scoring keys with a non-zero coefficient that some IDP actually recorded."""
    seen: Counter = Counter()
    for st in stat_lines:
        for k, coef in scoring.items():
            if num(coef) and num(st.get(k)):
                seen[k] += 1
    return {k: {"coefficient": scoring[k], "players_with_stat": n} for k, n in sorted(seen.items())}


def bonus_consistency(stat_lines: list[dict]) -> dict:
    out: dict = {"rows": 0, "idp_tkl_present": 0, "idp_tkl_equals_solo_plus_ast": 0, "bonus_keys": {}}
    for st in stat_lines:
        if not any(k.startswith("idp_") for k in st):
            continue
        out["rows"] += 1
        if st.get("idp_tkl") is not None:
            out["idp_tkl_present"] += 1
            if abs(num(st["idp_tkl"]) - num(st.get("idp_tkl_solo")) - num(st.get("idp_tkl_ast"))) < 1e-9:
                out["idp_tkl_equals_solo_plus_ast"] += 1
    for key, (stat, threshold) in THRESHOLD_BONUSES.items():
        c = Counter()
        for st in stat_lines:
            if not any(k.startswith("idp_") for k in st):
                continue
            value = tackles_total(st) if stat == "tkl_total" else num(st.get(stat))
            present = num(st.get(key)) > 0
            met = value >= threshold
            c[f"present={present},threshold_met={met}"] += 1
            if key == "bonus_tkl_10p" and present:
                c["present_and_solo_ge_10"] += num(st.get("idp_tkl_solo")) >= 10
        out["bonus_keys"][key] = dict(c)
    other = Counter(k for st in stat_lines for k in st if BONUS_LIKE.search(k) and k not in THRESHOLD_BONUSES)
    out["other_bonus_like_keys"] = dict(other.most_common(20))
    return out


# ---------------------------------------------------------------- projections / stats shape


def key_fill_rates(stat_lines: list[dict], prefixes=("idp_", "bonus", "st_", "def_", "gp", "gs")) -> dict:
    n = len(stat_lines)
    present: Counter = Counter()
    nonzero: Counter = Counter()
    for st in stat_lines:
        for k, v in st.items():
            if k.startswith(prefixes):
                present[k] += 1
                nonzero[k] += num(v) != 0
    return (
        {k: {"present": round(present[k] / n, 3), "nonzero": round(nonzero[k] / n, 3)} for k in sorted(present)}
        if n
        else {}
    )


def rows_from_payload(payload) -> list[dict]:
    """Normalize Sleeper list-of-rows or {player_id: stats} payloads to rows with str ids and dict stats."""
    if isinstance(payload, list):
        rows = [
            dict(r, player_id=str(r["player_id"]), stats=r.get("stats") if isinstance(r.get("stats"), dict) else {})
            for r in payload
            if isinstance(r, dict) and r.get("player_id") is not None
        ]
        return rows
    if isinstance(payload, dict):
        return [{"player_id": str(pid), "stats": st} for pid, st in payload.items() if isinstance(st, dict)]
    return []


def stats_by_player(payload) -> dict[str, dict]:
    return {r["player_id"]: r["stats"] for r in rows_from_payload(payload)}


def projection_shape(payload, players: dict[str, dict] | None = None) -> dict:
    players = players or {}
    rows = rows_from_payload(payload)
    stat_lines = [r.get("stats") or {} for r in rows]
    solo = [num(s.get("idp_tkl_solo")) for s in stat_lines if s.get("idp_tkl_solo") is not None]
    pos = Counter()
    for r in rows:
        p = r.get("player") or players.get(str(r.get("player_id")), {})
        pos[p.get("position") or "?"] += 1
    return {
        "rows": len(rows),
        "row_keys": sorted({k for r in rows[:200] for k in r}),
        "with_gp": sum(s.get("gp") is not None for s in stat_lines),
        "with_opponent": sum(r.get("opponent") not in (None, "") for r in rows),
        "companies": dict(Counter(r.get("company") for r in rows).most_common(5)),
        "positions": dict(pos.most_common()),
        "idp_tkl_solo_noninteger_share": round(sum(v != int(v) for v in solo) / len(solo), 3) if solo else None,
        "key_fill_rates": key_fill_rates(stat_lines),
    }


# ---------------------------------------------------------------- injuries, waivers


def injury_tallies(players: dict[str, dict]) -> dict:
    idp = [p for p in players.values() if is_idp(p) and p.get("team")]
    return {
        "idp_players_on_teams": len(idp),
        "injury_status": dict(Counter(p.get("injury_status") for p in idp).most_common()),
        "status": dict(Counter(p.get("status") for p in idp).most_common()),
        "active": dict(Counter(p.get("active") for p in idp).most_common()),
    }


def waiver_timing(transactions: list[dict]) -> dict:
    types: Counter = Counter()
    by_pt: Counter = Counter()
    by_utc: Counter = Counter()
    statuses: Counter = Counter()
    for t in transactions:
        types[t.get("type")] += 1
        if t.get("type") != "waiver":
            continue
        statuses[t.get("status")] += 1
        ts = t.get("status_updated") or t.get("created")
        if not ts:
            continue
        dt = datetime.fromtimestamp(num(ts) / 1000, tz=timezone.utc)
        by_pt[dt.astimezone(PT).strftime("%a %H:00 PT")] += 1
        by_utc[dt.strftime("%a %H:00 UTC")] += 1
    return {
        "transaction_types": dict(types.most_common()),
        "waiver_statuses": dict(statuses.most_common()),
        "waiver_processed_at_pt": dict(by_pt.most_common(8)),
        "waiver_processed_at_utc": dict(by_utc.most_common(8)),
    }


def _et(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ET).strftime("%a %Y-%m-%d %H:%M ET")


def leg_boundaries(transactions: list[dict]) -> dict:
    """First/last creation time of free-agent and trade transactions per leg (week).

    Sleeper's week rollover N -> N+1 lies between leg N's last and leg N+1's first
    transaction. Waivers are excluded: their leg may be assigned when they process.
    """
    by_leg: dict[int, list[float]] = {}
    for t in transactions:
        if t.get("type") in ("free_agent", "trade") and t.get("leg") and t.get("created"):
            by_leg.setdefault(int(t["leg"]), []).append(num(t["created"]))
    return {str(leg): {"first": _et(min(v)), "last": _et(max(v)), "n": len(v)} for leg, v in sorted(by_leg.items())}


def d14_add_time_replay(
    transactions: list[dict], rosters: list[dict], players: dict[str, dict], limits: dict[str, int], min_leg: int = 2
) -> dict:
    """Which counting rule did Sleeper enforce when free-agent/waiver adds went through?

    Replays completed transactions backwards from the current rosters. For every in-season
    (leg >= min_leg) IDP add, counts the roster right after the add under each rule, with and
    without current IR/taxi players. Sleeper enforces limits at add time (trades and the draft
    can exceed them), so the enforced rule never shows an add that ends over the limit.
    Leg 1 is skipped: draft picks aren't transactions, so pre-draft rosters can't be rebuilt.
    """
    state = {r["roster_id"]: set(r.get("players") or []) for r in rosters}
    reserve = {r["roster_id"]: set(r.get("reserve") or []) for r in rosters}
    taxi = {r["roster_id"]: set(r.get("taxi") or []) for r in rosters}
    done = sorted(
        (t for t in transactions if t.get("status") == "complete"),
        key=lambda t: num(t.get("status_updated") or t.get("created")),
        reverse=True,
    )
    variants = ("all", "excl_reserve", "excl_reserve_taxi")
    out = {rule: {v: {"adds_checked": 0, "at_limit": 0, "over": []} for v in variants} for rule in D14_RULES}
    for t in done:
        adds, drops = t.get("adds") or {}, t.get("drops") or {}
        if t.get("type") in ("free_agent", "waiver") and int(num(t.get("leg"))) >= min_leg:
            for pid, rid in adds.items():
                p = players.get(pid)
                if not p or not is_idp(p) or rid not in state:
                    continue
                sets = {
                    "all": state[rid],
                    "excl_reserve": state[rid] - reserve[rid],
                    "excl_reserve_taxi": state[rid] - reserve[rid] - taxi[rid],
                }
                for rule, fn in D14_RULES.items():
                    for slot in fn(p):
                        if slot not in limits:
                            continue
                        for v, ids in sets.items():
                            n = sum(slot in fn(players.get(q, {})) for q in ids)
                            o = out[rule][v]
                            o["adds_checked"] += 1
                            o["at_limit"] += n == limits[slot]
                            if n > limits[slot]:
                                o["over"].append(
                                    {
                                        "leg": t.get("leg"),
                                        "roster_id": rid,
                                        "player": player_name(p),
                                        "slot": slot,
                                        "count": n,
                                    }
                                )
        for pid, rid in adds.items():
            state.get(rid, set()).discard(pid)
        for pid, rid in drops.items():
            state.setdefault(rid, set()).add(pid)
    consistent = sorted(
        r for r in D14_RULES if not out[r]["excl_reserve"]["over"] and out[r]["excl_reserve"]["adds_checked"]
    )
    return {"min_leg": min_leg, "rules": out, "consistent_rules_excl_reserve": consistent}


def failed_waiver_notes(transactions: list[dict]) -> dict:
    """Sleeper's reasons for failed waiver claims (may mention position limits: D14 evidence)."""
    notes = Counter(
        (t.get("metadata") or {}).get("notes")
        for t in transactions
        if t.get("type") == "waiver" and t.get("status") == "failed"
    )
    return {str(k): v for k, v in notes.most_common(15)}


# ---------------------------------------------------------------- schedule


def parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def kickoff(row: dict) -> datetime | None:
    day, t = (row.get("gameday") or "").strip(), (row.get("gametime") or "").strip()
    if not day:
        return None
    try:
        hh, mm = (t or "13:00").split(":")[:2]
        return datetime.fromisoformat(day).replace(hour=int(hh), minute=int(mm), tzinfo=ET)
    except ValueError:
        return None


def regular_season(rows: list[dict], season: int) -> list[dict]:
    return [r for r in rows if str(r.get("season")) == str(season) and r.get("game_type") == "REG"]


def target_week(rows: list[dict], now: datetime) -> int | None:
    """Week of the earliest regular-season game not yet kicked off (SPEC §4.3)."""
    upcoming = [(k, int(r["week"])) for r in rows if (k := kickoff(r)) and k > now]
    return min(upcoming)[1] if upcoming else None


def settled_week(rows: list[dict], now: datetime, days: int = 4) -> int | None:
    """Latest week whose last game kicked off at least `days` days (+4 h) before now."""
    last: dict[int, datetime] = {}
    for r in rows:
        k = kickoff(r)
        if k:
            w = int(r["week"])
            last[w] = max(last.get(w, k), k)
    done = [w for w, k in last.items() if k + timedelta(hours=4, days=days) <= now]
    return max(done) if done else None


def schedule_summary(rows: list[dict], season: int, now: datetime) -> dict:
    reg = regular_season(rows, season)
    teams = sorted({r.get("home_team") for r in reg} | {r.get("away_team") for r in reg})
    tw = target_week(reg, now)
    week_teams = {t for r in reg if tw and int(r["week"]) == tw for t in (r["home_team"], r["away_team"])}
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "season_games": len(reg),
        "teams": teams,
        "gametime_examples": sorted({r.get("gametime") for r in reg})[:12],
        "missing_kickoff": sum(kickoff(r) is None for r in reg),
        "target_week": tw,
        "settled_week": settled_week(reg, now),
        "byes_in_target_week": sorted(set(teams) - week_teams) if tw else [],
    }


def crosswalk_coverage(rows: list[dict]) -> dict:
    idp_rows = [r for r in rows if (r.get("position") or "").upper() in POSITION_TO_SLOT]

    def share(col):
        return (
            round(sum(bool((r.get(col) or "").strip()) and r.get(col) != "NA" for r in idp_rows) / len(idp_rows), 3)
            if idp_rows
            else None
        )

    return {
        "columns": list(rows[0].keys()) if rows else [],
        "idp_rows": len(idp_rows),
        "share_with_sleeper_id": share("sleeper_id"),
        "share_with_fantasypros_id": share("fantasypros_id"),
        "share_with_espn_id": share("espn_id"),
        "share_with_gsis_id": share("gsis_id"),
    }


# ---------------------------------------------------------------- ESPN


def espn_shape(payload, week: int) -> dict:
    players = payload.get("players", []) if isinstance(payload, dict) else []
    pos = Counter()
    proj_keys: Counter = Counter()
    actual_keys: Counter = Counter()
    with_proj = 0
    for entry in players:
        p = entry.get("player") or {}
        pos[p.get("defaultPositionId")] += 1
        got = False
        for s in p.get("stats") or []:
            if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1:
                proj_keys.update((s.get("stats") or {}).keys())
                got = True
            if s.get("scoringPeriodId") == week - 1 and s.get("statSourceId") == 0:
                actual_keys.update((s.get("stats") or {}).keys())
        with_proj += got
    return {
        "players": len(players),
        "default_position_ids": dict(pos.most_common()),
        "players_with_week_projection": with_proj,
        "projection_stat_ids": dict(proj_keys.most_common(40)),
        "prior_week_actual_stat_ids": dict(actual_keys.most_common(40)),
    }


# ---------------------------------------------------------------- ESPN league: matching and stat-id mapping

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str | None) -> str:
    """'Jevón Holland Jr.' -> 'jevon holland' (accents, punctuation and suffixes removed)."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[.'’`]", "", text)
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t and t not in NAME_SUFFIXES]
    return " ".join(tokens)


def espn_week_line(entry: dict, week: int | None, source: int) -> dict | None:
    """One ESPN player's single-week stat line (statSourceId 0 = actual, 1 = projected)."""
    player = entry.get("player") or entry
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == source and s.get("statSplitTypeId") == 1:
            return s.get("stats") or {}
    return None


def match_espn_to_sleeper(espn_entries: list[dict], sleeper_players: dict[str, dict]) -> dict:
    """ESPN player id -> Sleeper player id: Sleeper's `espn_id` first, then a unique normalized name among IDPs."""
    by_espn_id = {str(p.get("espn_id")): pid for pid, p in sleeper_players.items() if p.get("espn_id")}
    by_name: dict[str, list[str]] = {}
    for pid, p in sleeper_players.items():
        if is_idp(p):
            by_name.setdefault(normalize_name(player_name(p)), []).append(pid)
    matched, how = {}, Counter()
    for e in espn_entries:
        player = e.get("player") or e
        eid = str(player.get("id"))
        if eid in by_espn_id:
            matched[eid] = by_espn_id[eid]
            how["espn_id"] += 1
            continue
        candidates = by_name.get(normalize_name(player.get("fullName")), [])
        if len(candidates) == 1:
            matched[eid] = candidates[0]
            how["name"] += 1
        else:
            how["ambiguous_name" if candidates else "unmatched"] += 1
    return {"map": matched, "how": dict(how)}


def espn_stat_mapping(
    pairs: list[tuple[dict, dict]], sadl_keys: list[str] | None = None, min_n: int = 8, threshold: float = 0.9
) -> dict:
    """Which Sleeper stat key does each ESPN stat id measure?

    `pairs` holds (ESPN actual line, Sleeper actual line) for the same player and week. For each
    ESPN stat id, every Sleeper `idp_*` key is scored by exact agreement over the players where
    either value is non-zero (so idle players don't make every pairing look perfect).
    """
    espn_ids = sorted({k for e, _ in pairs for k, v in e.items() if num(v)}, key=lambda k: (len(k), k))
    sleeper_keys = sorted({k for _, s in pairs for k, v in s.items() if k.startswith("idp_") and num(v)})
    mapping: dict[str, dict] = {}
    for eid in espn_ids:
        scored = []
        for key in sleeper_keys:
            rows = [(num(e.get(eid)), num(s.get(key))) for e, s in pairs if num(e.get(eid)) or num(s.get(key))]
            if rows:
                agree = sum(abs(a - b) < 1e-9 for a, b in rows)
                scored.append((agree / len(rows), len(rows), key))
        if not scored:
            continue
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        rate, n, key = scored[0]
        mapping[eid] = {
            "sleeper_key": key if rate >= threshold and n >= min_n else None,
            "best": key,
            "rate": round(rate, 3),
            "n": n,
            "runner_up": {"key": scored[1][2], "rate": round(scored[1][0], 3)} if len(scored) > 1 else None,
        }
    mapped = {m["sleeper_key"] for m in mapping.values() if m["sleeper_key"]}
    return {
        "pairs": len(pairs),
        "mapping": mapping,
        "sadl_keys_without_espn_stat": sorted(k for k in (sadl_keys or []) if k not in mapped),
    }
