"""One sync run (SPEC.md §3–§7): fetch, freeze, score, blend, tier, and write the week's data.

Fatal: the documented Sleeper /v1 endpoints (raise SleeperError). Everything else degrades: a failed
source keeps its earlier rows (marked stale), and the page says so.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import gate as gatemod
from . import scoring, storage, weeklog
from .config import load_alias
from .consensus import consensus
from .net import Client
from .pool import PlayerStatus, player_status, rostered_ids
from .positions import IDP_SLOTS, also_eligible, player_name, slot_of
from .roster import panel, starter_slots
from .schedule import ET, NFLVERSE_GAMES, Schedule
from .sigma import within_player_sd
from .sleeper import Sleeper, my_roster, position_limits, resolve_league, season_of
from .sources import sleeper_proj
from .sources.base import SUSPECT, AdapterResult
from .tiers import build_tiers, place

SLIM_FIELDS = (
    "full_name", "first_name", "last_name", "position", "fantasy_positions",
    "team", "status", "active", "injury_status", "espn_id", "fantasy_data_id",
)  # fmt: skip
SOURCES = {sleeper_proj.NAME: (sleeper_proj, "projection")}


@dataclass
class SyncOutcome:
    synced: bool
    reason: str
    season: int | None = None
    week: int | None = None
    window: str | None = None
    warnings: list[str] = field(default_factory=list)


def _guard(label: str, fn: Callable, default, warnings: list[str], log: Callable[[str], None]):
    """Run one non-core step; a failure becomes a warning, never a failed sync (SPEC §7)."""
    try:
        return fn()
    except Exception as e:  # isolation by design: the traceback goes to the job log only
        warnings.append(f"{label} failed ({type(e).__name__}); skipped this sync")
        log(traceback.format_exc())
        return default


def fetch_schedule(client: Client, cfg: dict, season: int, data_dir: Path) -> tuple[Schedule, str]:
    r = client.get(NFLVERSE_GAMES)
    if r.ok:
        sched = Schedule.from_csv(r.text(), season, load_alias(cfg, "nflverse_teams"))
        if sched.games:
            return sched, "nflverse"
    cached = storage.read_json(data_dir / str(season) / "schedule.json")
    if cached:
        return Schedule.from_json(cached), "cached copy (nflverse unavailable)"
    raise RuntimeError("schedule unavailable and no cached copy")


def sigma_out_for(
    sleeper: Sleeper, cfg: dict, data_dir: Path, season: int, players: dict, sc: dict, warnings: list, week: int = 0
) -> dict:
    """σ_out per position (SPEC §6.7): this season's residuals once enabled (`sigma_out.method =
    "residuals"`) and 3 settled weeks exist; otherwise last season's within-player SD, cached per season."""
    residual = {}
    if (cfg.get("sigma_out") or {}).get("method") == "residuals":
        from .stats import residual_sigma_out

        residual = residual_sigma_out(data_dir, season, cfg, week) or {}
        if all(s in residual for s in IDP_SLOTS):
            return residual
    prev = season - 1
    path = data_dir / str(prev) / "sigma_out.json"
    est = storage.read_json(path)
    if not est:
        weekly = {}
        for w in range(1, 19):
            payload, err = sleeper.stats(prev, w)
            if not isinstance(payload, dict):
                warnings.append(f"σ_out: {prev} week {w} stats unavailable ({err}); using config defaults")
                break
            weekly[w] = {str(k): v for k, v in payload.items() if isinstance(v, dict)}
        if len(weekly) == 18:
            est = {**within_player_sd(weekly, players, sc), "season": prev}
            storage.write_json(path, est)
    fallback = cfg.get("sigma_out") or {}
    return {s: float(residual.get(s) or (est or {}).get(s) or fallback.get(s, 5.0)) for s in IDP_SLOTS}


def refresh_actuals(
    sleeper: Sleeper, data_dir: Path, season: int, sched: Schedule, now: datetime, players: dict
) -> list[int]:
    """Write actual IDP stat lines for past weeks: once, then again until 4 days after the week (stat corrections)."""
    written = []
    for week in sorted({g.week for g in sched.games}):
        kicks = [g.kickoff for g in sched.week_games(week) if g.kickoff]
        if not kicks or max(kicks) + timedelta(hours=4) > now:
            continue
        path = storage.week_dir(data_dir, season, week) / "actuals.json"
        settled_at = sched.settles_at(week)
        captured = (storage.read_json(path) or {}).get("captured_at")
        if captured and datetime.fromisoformat(captured) >= settled_at:
            continue  # captured after the stat-correction window: final
        payload, _ = sleeper.stats(season, week)
        if isinstance(payload, dict):
            lines = {str(k): v for k, v in payload.items() if isinstance(v, dict) and slot_of(players.get(str(k), {}))}
            storage.write_json(path, {"week": week, "captured_at": now.isoformat(), "stats": lines})
            written.append(week)
    return written


def backfill_projections(sleeper: Sleeper, data_dir: Path, season: int, before_week: int, now: datetime) -> list[int]:
    """Past weeks' pre-game Sleeper projections (still served; Phase 1), written once and flagged."""
    done = []
    for week in range(1, before_week):
        path = storage.week_dir(data_dir, season, week) / "sources" / f"{sleeper_proj.NAME}.json"
        if path.exists():
            continue
        res = sleeper_proj.fetch(sleeper, season, week, now)
        if res.ok:
            storage.save_source(path, res, {pid: {**row, "backfill": True} for pid, row in res.rows.items()})
            done.append(week)
    return done


def zero_fill(source: dict, valued: dict, idp: dict, status_of: Callable[[str], PlayerStatus]) -> dict[str, float]:
    """§6.4 absence rule: a complete source that lists neither a row nor a placeholder for an active
    player with an unlocked game this week projects him at 0 (placeholders stay missing)."""
    complete = {slot for slot, u in (source.get("universe") or {}).items() if (u or {}).get("complete")}
    placeholders = set(source.get("no_projection_ids") or [])
    out = {}
    for pid, p in idp.items():
        if pid in valued or pid in placeholders or slot_of(p) not in complete or not p.get("active"):
            continue
        st = status_of(pid)
        if st.kickoff is not None and not st.locked:
            out[pid] = 0.0
    return out


def build_week_logs(
    wdir: Path,
    my_panel: dict,
    tiers_by_slot: dict,
    cons: dict,
    sigma_out: dict,
    kickoff_of: Callable[[str], datetime | None],
    now: datetime,
    prewaiver: bool,
    claimed: Callable[[str], str | None] = lambda pid: None,
) -> tuple[dict, dict]:
    """badges.json and tiers_log.json documents for later evaluation (weeklog.py); written by the caller.
    `claimed(pid)` says who has rostered a player ("me", "other" or None)."""
    current = {}
    for slot in IDP_SLOTS:
        for w, b in my_panel[slot]["badges"].items():
            kicks = [k for k in (kickoff_of(w), kickoff_of(b["over"]) if b["over"] else None) if k]
            current[f"{w}|{b['badge']}|{b['over'] or ''}"] = {
                "slot": slot, "w": w, "badge": b["badge"], "over": b["over"], "p": b["p"], "action": b["action"],
                "drop": b["drop"], "reference": b["reference"], "why": b["why"], "mu_w": round(cons[w]["mu"], 3),
                "mu_over": round(cons[b["over"]]["mu"], 3) if b["over"] else 0.0, "sigma_out": sigma_out.get(slot),
                "lock_at": min(kicks).isoformat() if kicks else None,
            }  # fmt: skip
    prev = (storage.read_json(wdir / "badges.json") or {}).get("badges", {})
    badges = {"schema_version": 1, "badges": weeklog.update_badge_log(prev, current, now, claimed)}
    rows = {
        pid: {"slot": slot, "tier": t["tier"], "mu": round(cons[pid]["mu"], 3), "sigma": round(cons[pid]["sigma"], 3)}
        for slot in IDP_SLOTS
        for t in tiers_by_slot[slot]
        for pid in t["players"]
    }
    tiers = weeklog.update_tiers_log(storage.read_json(wdir / "tiers_log.json") or {}, rows, now, kickoff_of, prewaiver)
    return badges, tiers


def live_results(payload, pids, sc: dict, status_of: Callable[[str], PlayerStatus], now: datetime) -> dict[str, dict]:
    """This week's scored results for players whose game has started (D22): SADL points so far, `live`
    until kickoff + 4 h and `final` after; `dnp` if the game is over and he has no stat line;
    `unavailable` if the /stats fetch failed (shown as such, never as "not played yet")."""
    out = {}
    for pid in pids:
        st = status_of(pid)
        if st.kickoff is None or st.kickoff > now:
            continue
        if isinstance(payload, dict):
            out[pid] = weeklog.stat_result(payload.get(pid), sc, st.kickoff + timedelta(hours=4) <= now)
        else:
            out[pid] = {"pts": None, "state": "unavailable"}
    return out


def last_week_recap(data_dir: Path, season: int, week: int, sched: Schedule, sc: dict, players: dict) -> dict | None:
    """Last week's issued suggestions with both players' final scores (D22). Scores come from
    actuals.json and stay provisional until the stat-correction window closes (last kickoff + 4 d 4 h)."""
    prev = week - 1
    wd = storage.week_dir(data_dir, season, prev)
    badges = (storage.read_json(wd / "badges.json") or {}).get("badges", {})
    act = storage.read_json(wd / "actuals.json")
    if prev < 1 or not badges or not act:
        return None
    stats = act.get("stats") or {}
    settles = sched.settles_at(prev)
    provisional = bool(settles) and datetime.fromisoformat(act["captured_at"]) < settles

    def res(pid: str) -> dict:
        return weeklog.stat_result(stats.get(pid), sc, final=True)

    slots: dict[str, list] = {slot: [] for slot in IDP_SLOTS}
    for e in badges.values():
        if not e.get("active") or e.get("slot") not in slots:
            continue  # withdrawn before lock: never issued
        w, over = e["w"], e.get("over")
        w_res, over_res = res(w), res(over) if over else None
        slots[e["slot"]].append({
            **{k: e.get(k) for k in ("badge", "p", "reference", "why", "mu_w", "mu_over", "over")},
            "name": player_name(players.get(w, {})), "team": (players.get(w) or {}).get("team"),
            "over_name": player_name(players.get(over, {})) if over else None,
            "result": w_res, "over_result": over_res,
            "outcome": weeklog.outcome(w_res, over, over_res),
        })  # fmt: skip
    for rows in slots.values():
        rows.sort(key=lambda x: (x["reference"], -((x["result"] or {}).get("pts") or -1.0)))
    counted = [
        x for rows in slots.values() for x in rows if not x["reference"] and x["outcome"] in ("hit", "miss", "push")
    ]
    return {
        "week": prev,
        "provisional": provisional,
        "settles": settles.isoformat() if settles else None,
        "slots": slots,
        "summary": {"hits": sum(1 for x in counted if x["outcome"] == "hit"), "decided": len(counted)},
    }


SOURCE_LABELS = {"sleeper_proj": "Sleeper"}
STAT_LABELS = (
    ("idp_tkl_solo", "solo"), ("idp_tkl_ast", "ast"), ("idp_tkl_loss", "TFL"), ("idp_sack", "sack"),
    ("idp_qb_hit", "QB hit"), ("idp_pass_def", "PD"), ("idp_int", "INT"), ("idp_ff", "FF"), ("idp_fum_rec", "FR"),
    ("idp_def_td", "TD"), ("idp_safe", "safety"), ("idp_blk_kick", "blocked kick"),
)  # fmt: skip


def breakdown(pid: str, cons_row: dict | None, source_rows: dict, values: dict, groups: dict, info: dict,
              sc: dict) -> dict:  # fmt: skip
    """Per-source parts of a player's number for the page (D19): SADL points, the projected stats behind
    them, the share of μ, or why a source has nothing for him."""
    eff = (cons_row or {}).get("eff") or {}
    frozen_pts = (cons_row or {}).get("parts") or {}  # a locked player: each source's points at his kickoff
    parts = []
    for name in SOURCES:
        label = SOURCE_LABELS.get(name, name)
        src = info.get(name) or {}
        pts = values.get(name, {}).get(pid, frozen_pts.get(name))
        if pts is not None:
            row = (source_rows.get(name) or {}).get(pid)
            stats = (row or {}).get("stats") or {}
            shown = [[lbl, round(scoring.num(stats.get(k)), 2)] for k, lbl in STAT_LABELS
                     if scoring.num(sc.get(k)) and scoring.num(stats.get(k)) >= 0.05]  # fmt: skip
            parts.append({"source": label, "kind": groups.get(name), "pts": round(pts, 2),
                          "share": eff.get(name), "stats": shown, "late": bool(row and row.get("late")),
                          "zero": row is None})  # fmt: skip
        elif not src.get("last_success"):
            parts.append({"source": label, "missing": "nothing posted for this week yet"})
        elif src.get("stale"):
            parts.append({"source": label, "missing": "not used this week (its data is too old)"})
        elif not src.get("rows_used"):
            parts.append({"source": label, "missing": "nothing posted for this week yet"})
        else:
            parts.append({"source": label, "missing": "no projection for him"})
    return {"parts": parts, "mu": round(cons_row["mu"], 2) if cons_row else None}


def attach_breakdowns(site_part, make: Callable[[str], dict]) -> None:
    """Add a `breakdown` to every player card in the site data (cards carry id, mu and slot)."""
    if isinstance(site_part, dict):
        if "id" in site_part and "mu" in site_part and "slot" in site_part:
            site_part["breakdown"] = make(site_part["id"])
        for v in site_part.values():
            attach_breakdowns(v, make)
    elif isinstance(site_part, list):
        for v in site_part:
            attach_breakdowns(v, make)


def _player_card(pid, p, cons_row, status: PlayerStatus, tier, badge, mine: bool) -> dict:
    return {
        "id": pid,
        "name": player_name(p),
        "team": p.get("team"),
        "opp": status.opponent,
        "slot": slot_of(p),
        "also": also_eligible(p),
        "mu": round(cons_row["mu"], 2) if cons_row else None,
        "sigma": round(cons_row["sigma"], 2) if cons_row and cons_row.get("sigma") is not None else None,
        "min": round(cons_row["min"], 2) if cons_row and cons_row["min"] is not None else None,
        "max": round(cons_row["max"], 2) if cons_row and cons_row["max"] is not None else None,
        "n": cons_row["n_sources"] if cons_row else 0,
        "tier": tier,
        "flags": status.flags,
        "unavailable": status.reasons,
        "kickoff": status.kickoff.isoformat() if status.kickoff else None,
        "badge": badge,
        "mine": mine,
        "late": bool(cons_row and cons_row.get("late")),
        "result": None,
    }


def run_sync(
    cfg: dict,
    data_dir: Path | str = "data",
    cache_dir: Path | str = ".cache",
    now: datetime | None = None,
    force: bool = False,
    client: Client | None = None,
    log: Callable[[str], None] = print,
) -> SyncOutcome:
    data_dir = Path(data_dir)
    now = (now or datetime.now(ET)).replace(microsecond=0)  # whole seconds: every browser parses the timestamps
    client = client or Client()
    sleeper = Sleeper(client, cache_dir)
    warnings: list[str] = []

    state = sleeper.state()
    season = season_of(state, now.year)
    sched, sched_source = fetch_schedule(client, cfg, season, data_dir)
    week = sched.target_week(now) or int(state.get("display_week") or state.get("week") or 0) or None
    if not force and state.get("season_type") != "regular":
        return SyncOutcome(False, f"off-season ({state.get('season_type')})", season, week)
    if week is None:
        return SyncOutcome(False, "no upcoming regular-season week", season, None)

    season_dir = data_dir / str(season)
    cached_league = storage.read_json(season_dir / "league.json", {})
    league, user_id, window = None, None, None
    if not force:
        # The gate decides from league.json on main; Sleeper league calls only once a window is due.
        if not cached_league.get("settings"):
            league, user_id = resolve_league(sleeper, cfg, season, cached_league.get("user_id"))
        known = league or cached_league
        settings = known.get("settings") or {}
        if known.get("status") == "complete":
            return SyncOutcome(False, "league is complete", season, week)
        if week > gatemod.last_fantasy_week(settings):
            return SyncOutcome(False, f"week {week} is after the league's last week", season, week)
        done = set(storage.read_json(season_dir / "gate.json", {}).get("windows_done", []))
        window = gatemod.due(now, gatemod.windows(sched, season, week, settings, cfg.get("gate") or {}), done)
        if window is None:
            return SyncOutcome(False, "no gate window due", season, week)
    if league is None:
        league, user_id = resolve_league(sleeper, cfg, season, cached_league.get("user_id"))
        if not force and league.get("status") == "complete":
            return SyncOutcome(False, "league is complete", season, week)
    settings = league.get("settings") or {}

    log(f"sync: season {season}, target week {week}, {'window ' + window.id if window else 'forced'}")
    rosters = sleeper.rosters(league["league_id"])
    players = {str(k): v for k, v in sleeper.players(now.astimezone(ET).date().isoformat()).items()}
    idp = {pid: p for pid, p in players.items() if slot_of(p)}
    unknown = sched.unknown_teams({p.get("team") for p in idp.values()})
    if unknown:
        raise RuntimeError(f"team codes missing from the schedule (alias table needed): {sorted(unknown)}")

    sc = league.get("scoring_settings") or {}
    statuses: dict[str, PlayerStatus] = {}

    def status_of(pid: str) -> PlayerStatus:
        if pid not in statuses:
            statuses[pid] = player_status(players.get(pid, {}), sched, week, now)
        return statuses[pid]

    def kickoff_of(pid: str) -> datetime | None:
        return status_of(pid).kickoff

    # actuals first, so this sync's imputation and logs see last week's stats (SPEC §6.5)
    actuals = _guard("actuals refresh", lambda: refresh_actuals(sleeper, data_dir, season, sched, now, players), [],
                     warnings, log)  # fmt: skip

    # sources -> frozen snapshots
    wdir = storage.week_dir(data_dir, season, week)
    run = {
        "generated_at": now.isoformat(),
        "season": season,
        "week": week,
        "sleeper_week": {"week": state.get("week"), "display_week": state.get("display_week")},
        "schedule": sched_source,
        "sources": {},
    }
    values: dict[str, dict[str, float]] = {}
    groups: dict[str, str] = {}
    late_rows: dict[str, dict] = {}
    source_rows: dict[str, dict] = {}
    max_age = timedelta(hours=float((cfg.get("sources") or {}).get("max_source_age_h", 96)))
    shrink = float((cfg.get("sources") or {}).get("shrink_guard", 0.6))
    fetched_ok: set[str] = set()
    for name, (module, kind) in SOURCES.items():
        if not ((cfg.get("sources") or {}).get(name) or {}).get("enabled", True):
            continue
        path = wdir / "sources" / f"{name}.json"
        existing = storage.load_source(path)
        try:
            res = module.fetch(sleeper, season, week, now)
        except Exception as e:  # a broken adapter is a failed source, never a failed sync (SPEC §7)
            log(traceback.format_exc())
            res = AdapterResult(name=name, kind=kind, status="failed", week=week, fetched_at=now.isoformat(),
                                error=f"{type(e).__name__} in fetch")  # fmt: skip
        before = len(existing.get("rows") or {})
        if res.ok and before and len(res.rows) < shrink * before:  # SPEC §7: a shrunken fetch isn't merged
            res.status, res.error = SUSPECT, f"{len(res.rows)} rows against {before} before"
        info = res.summary()
        late_ok = bool(((cfg.get("sources") or {}).get(name) or {}).get("late_rows"))
        if res.ok:
            fetched_ok.add(name)
            merged, counts = storage.merge_rows(existing.get("rows", {}), res.rows, now, kickoff_of, late_ok)
            storage.save_source(path, res, merged)
            info.update(merge=counts, last_success=res.fetched_at)
            rows = merged
        else:
            info["last_success"] = existing.get("last_success")
            rows = existing.get("rows", {})
            why = {"not_yet_published": f"week {week} not posted yet",
                   SUSPECT: "this fetch looked incomplete and was skipped"}.get(res.status, res.status)  # fmt: skip
            why += f" ({res.error})" if res.error else ""
            warnings.append(f"{name}: {why}" + ("; using earlier rows" if rows else ""))
        info["rows_used"] = len(rows)
        last = info.get("last_success")
        if not last or now - datetime.fromisoformat(last) > max_age:
            if rows:
                warnings.append(f"{name}: rows older than {max_age}; left out of the consensus")
            info["stale"] = True
            run["sources"][name] = info
            continue
        groups[name] = kind
        late_rows[name] = {pid: r for pid, r in rows.items() if r.get("late")}
        source_rows[name] = rows
        values[name] = {
            pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items() if pid in idp
        }
        if kind == "projection":
            zeros = zero_fill(storage.load_source(path), values[name], idp, status_of)
            values[name].update(zeros)
            info["zero_filled"] = len(zeros)
        run["sources"][name] = info

    if not any(values.values()):
        warnings.append("no source has usable rows for this week yet; the tiers are empty until one does")
    cons = consensus(values, groups, {pid: slot_of(idp[pid]) for pid in idp}, cfg)
    late = {pid for name in values for pid, r in late_rows.get(name, {}).items() if r.get("late")}
    for pid in late & cons.keys():
        cons[pid]["late"] = True  # projection first captured after his kickoff (SPEC §6.5)

    # frozen week record: each player's values at his last sync before kickoff (SPEC §6.5, D15). A locked
    # player keeps exactly what he showed, whatever happens to other players or sources afterwards.
    frozen_path = wdir / "frozen.json"
    records = {
        pid: {
            "mu": round(c["mu"], 4),
            "sigma": round(c["sigma"], 4),
            "min": c["min"],
            "max": c["max"],
            "n_sources": c["n_sources"],
            "eff": c.get("eff") or {},
            "parts": {name: round(v[pid], 3) for name, v in values.items() if pid in v},
            "reasons": list(status_of(pid).reasons),
            "flags": list(status_of(pid).flags),
        }
        for pid, c in cons.items()
    }
    frozen, _ = storage.merge_rows((storage.read_json(frozen_path) or {}).get("players", {}), records, now, kickoff_of)
    for pid, rec in frozen.items():
        stored = rec.get("kickoff")
        if pid in idp and (status_of(pid).locked or (stored and datetime.fromisoformat(stored) <= now)):
            cons[pid] = {k: rec.get(k) for k in ("mu", "sigma", "min", "max", "n_sources", "eff", "parts")}

    # waiver pool and tiers
    rostered = rostered_ids(rosters)
    z = float((cfg.get("tiers") or {}).get("z", 1.0))
    tiers_by_slot, waiver_order = {}, {}
    for slot in IDP_SLOTS:
        pool = {
            pid: (c["mu"], c["sigma"])
            for pid, c in cons.items()
            if slot_of(idp[pid]) == slot and pid not in rostered and status_of(pid).playable
        }
        tiers_by_slot[slot] = build_tiers(pool, z)
        waiver_order[slot] = [pid for t in tiers_by_slot[slot] for pid in t["players"]]

    me = my_roster(rosters, user_id)
    if me is None:
        warnings.append("your roster wasn't found in the league; My IDPs is empty")
    limits = position_limits(settings, int((cfg.get("roster") or {}).get("position_limit", 2)))
    unset = [slot for slot in IDP_SLOTS if not settings.get(f"position_limit_{slot.lower()}")]
    if unset:
        warnings.append(f"league has no position limit for {', '.join(unset)}; assuming the config fallback")
    sigma_fallback = {s: float((cfg.get("sigma_out") or {}).get(s, 5.0)) for s in IDP_SLOTS}
    sigma_out = _guard(
        "σ_out estimate",
        lambda: sigma_out_for(sleeper, cfg, data_dir, season, players, sc, warnings, week),
        sigma_fallback,
        warnings,
        log,
    )
    matchups = _guard("matchups", lambda: sleeper.matchups(league["league_id"], week)[0], None, warnings, log)
    my_matchup = next((m for m in (matchups or []) if me and m.get("roster_id") == me.get("roster_id")), {})
    lineup = starter_slots(league.get("roster_positions") or [], my_matchup.get("starters"))
    # a locked rostered player is judged on his status at kickoff (a later injury tag can't empty the slot)
    panel_status: dict[str, PlayerStatus] = {}
    for pid in (me or {}).get("players") or []:
        st, rec = status_of(pid), frozen.get(pid)
        panel_status[pid] = st
        if st.locked and rec is not None:
            reasons = [*rec.get("reasons", []), "locked"]
            panel_status[pid] = PlayerStatus(reasons, list(rec.get("flags", [])), st.kickoff, st.opponent)
        elif st.locked and pid in cons:  # projected before kickoff but no record (first week): ignore later tags
            panel_status[pid] = PlayerStatus(["locked"], list(st.flags), st.kickoff, st.opponent)
    threshold = float((cfg.get("swap") or {}).get("threshold", 0.6))
    my_panel = panel(me or {}, players, cons, panel_status, waiver_order, limits, sigma_out, threshold, lineup)

    mine = {str(pid) for pid in (me or {}).get("players") or []}

    def claimed(pid: str) -> str | None:
        return "me" if pid in mine else "other" if pid in rostered else None

    logs = _guard("decision logs", lambda: build_week_logs(wdir, my_panel, tiers_by_slot, cons, sigma_out, kickoff_of,
                                                         now, prewaiver=bool(window and window.kind == "pre-waiver"),
                                                         claimed=claimed),
                  (None, None), warnings, log)  # fmt: skip
    badge_doc, tiers_doc = logs
    started = [pid for pid in idp if status_of(pid).locked]
    live_stats, stats_err = _guard("live stats", lambda: sleeper.stats(season, week), (None, None), warnings, log)
    if started and stats_err:  # an exception already has its own banner from _guard
        warnings.append(f"Live scores unavailable this sync ({stats_err}); games already played show no score.")
    results = live_results(live_stats, started, sc, status_of, now)

    def was_playable(pid: str) -> bool:
        """Playable at his kickoff: per the frozen record, else nothing but `locked` stops him now."""
        rec = frozen.get(pid)
        return not rec.get("reasons") if rec is not None else status_of(pid).comparable

    # site data: only μ, σ, range, n, position, tier and flags (SPEC §6.4) -- never per-source values
    depth = int((cfg.get("display") or {}).get("depth", 25))
    logged_tier = {pid: r.get("tier") for pid, r in ((tiers_doc or {}).get("last_pre_kickoff") or {}).items()}
    positions_out = {}
    for slot in IDP_SLOTS:
        badges = my_panel[slot]["badges"]
        shown, out_tiers = 0, []
        for t in tiers_by_slot[slot]:
            cards = []
            for pid in t["players"]:
                if shown >= depth:
                    break
                cards.append(_player_card(pid, idp[pid], cons[pid], status_of(pid), t["tier"], badges.get(pid), False))
                shown += 1
            if cards:
                out_tiers.append({"tier": t["tier"], "anchor_mu": round(t["mu"], 2), "players": cards})
        no_projection = sum(
            1
            for pid, p in idp.items()
            if slot_of(p) == slot and pid not in cons and pid not in rostered and status_of(pid).playable
        )
        played_ids = sorted(
            (pid for pid in cons if slot_of(idp[pid]) == slot and pid not in rostered and status_of(pid).locked
             and was_playable(pid)),
            key=lambda pid: (logged_tier.get(pid) or 999, -cons[pid]["mu"], pid),
        )  # fmt: skip
        played = []
        for pid in played_ids[:depth]:
            card = _player_card(pid, idp[pid], cons[pid], status_of(pid), logged_tier.get(pid), None, False)
            card["result"] = results.get(pid)
            played.append(card)
        positions_out[slot] = {
            "tiers": out_tiers,
            "tier_count": len(tiers_by_slot[slot]),
            "hidden": len(waiver_order[slot]) - shown,
            "no_projection": no_projection,
            "played": played,
            "played_hidden": max(0, len(played_ids) - depth),
        }
    my_out = {}
    for slot in IDP_SLOTS:
        pnl = my_panel[slot]
        cards = []
        for pid in pnl["players"]:
            c = cons.get(pid)
            tier = place(c["mu"], c["sigma"], tiers_by_slot[slot], z) if c else None
            card = _player_card(pid, players.get(pid, {}), c, panel_status.get(pid, status_of(pid)), tier, None, True)
            card["result"] = results.get(pid)
            card.update(reserve=pid in pnl["reserve"], taxi=pid in pnl["taxi"], role=(
                "starter" if pid == pnl["starter"] else "other" if pid == pnl["other"] else None))  # fmt: skip
            cards.append(card)
        suggestions = [
            {**b, "player": _player_card(w, idp[w], cons[w], status_of(w), None, None, False)}
            for w, b in sorted(pnl["badges"].items(), key=lambda kv: (kv[1]["reference"], -cons[kv[0]]["mu"], kv[0]))[
                :5
            ]
        ]
        names = {pid: player_name(players.get(pid, {})) for pid in pnl["players"]}
        played_sugg = []  # issued this week (active at lock) and already locked: shown with their results
        issued = (badge_doc or {}).get("badges", {}).values()
        for e in sorted(issued, key=lambda e: (e.get("lock_at") or "", e["w"])):
            if e["slot"] != slot or not e.get("active") or not e.get("lock_at"):
                continue
            if datetime.fromisoformat(e["lock_at"]) > now:
                continue
            w, over = e["w"], e.get("over")
            if status_of(w).kickoff is None or status_of(w).kickoff > now:
                continue  # issued, but the suggested player hasn't played yet: still in the live list
            c = cons.get(w) or {"mu": e["mu_w"], "sigma": None, "min": None, "max": None, "n_sources": 0}
            card = _player_card(w, players.get(w, {}), c, status_of(w), None, None, False)
            card["result"] = results.get(w)
            for pid in (over, e.get("drop")):
                if pid:
                    names.setdefault(pid, player_name(players.get(pid, {})))
            played_sugg.append({
                **{k: e.get(k) for k in ("badge", "over", "p", "action", "drop", "reference", "why", "mu_over")},
                "player": card, "over_result": results.get(over) if over else None,
                "outcome": weeklog.outcome(results.get(w), over, results.get(over) if over else None),
            })  # fmt: skip
        played_sugg.sort(key=lambda x: (-((x["player"]["result"] or {}).get("pts") or -1.0), -(x["player"]["mu"] or 0)))
        my_out[slot] = {
            "players": cards,
            "count": pnl["count"],
            "limit": pnl["limit"],
            "starter_locked": pnl["starter_locked"],
            "suggestions": suggestions,
            "played_suggestions": played_sugg[:6],
            "played_suggestions_hidden": max(0, len(played_sugg) - 6),
            "names": names,
        }

    backfilled = _guard("backfill", lambda: backfill_projections(sleeper, data_dir, season, week, now), [], warnings,
                        log)  # fmt: skip
    if backfilled:
        log(f"sync: backfilled Sleeper projections for weeks {backfilled}")  # a note, not a page banner

    recap = _guard("last week's results", lambda: last_week_recap(data_dir, season, week, sched, sc, players), None,
                   warnings, log)  # fmt: skip

    settles = sched.settles_at(week)
    run.update(
        settles_at=settles.isoformat() if settles else None,
        warnings=warnings,
        backfilled=backfilled,
        actuals_written=actuals,
        window=window.id if window else "forced",
    )
    site_data = {
        "generated_at": now.isoformat(),
        "season": season,
        "week": week,
        "league": {
            "name": league.get("name"),
            "waiver_type": {0: "rolling", 1: "reverse standings", 2: "FAAB"}.get(settings.get("waiver_type"), "?"),
            "limits": limits,
        },
        "positions": positions_out,
        "my_idps": my_out,
        "last_week": recap,
        "sigma_out": sigma_out,
        "sources": run["sources"],
        "warnings": warnings,
    }
    attach_breakdowns(
        site_data, lambda pid: breakdown(pid, cons.get(pid), source_rows, values, groups, run["sources"], sc)
    )
    storage.write_json(wdir / "consensus.json", site_data)
    storage.write_json(frozen_path, {"schema_version": 1, "players": frozen})
    if badge_doc is not None:
        storage.write_json(wdir / "badges.json", badge_doc)
        storage.write_json(wdir / "tiers_log.json", tiers_doc)
    storage.write_json(wdir / "run.json", run)
    storage.write_json(wdir / "rosters.json", rosters)
    # the week's players as of each one's kickoff (teamless included): what later replays read (SPEC §6.5)
    slim = {pid: {k: p.get(k) for k in SLIM_FIELDS} for pid, p in idp.items()}
    week_players = (storage.read_json(wdir / "players.json") or {}).get("players", {})
    week_players, _ = storage.merge_rows(week_players, slim, now, kickoff_of)
    storage.write_json(wdir / "players.json", {"schema_version": 1, "players": week_players})
    storage.write_json(season_dir / "schedule.json", sched.to_json())
    storage.write_json(
        season_dir / "league.json",
        {
            "league_id": league["league_id"],
            "user_id": user_id,
            "name": league.get("name"),
            "status": league.get("status"),
            "roster_positions": league.get("roster_positions"),
            "settings": settings,
            "scoring_settings": sc,
        },
    )
    results_missing = window is not None and window.kind == "results" and not isinstance(live_stats, dict)
    if window and fetched_ok and any(values.values()) and not results_missing:  # else the next slot retries
        gate_path = season_dir / "gate.json"
        done_list = storage.read_json(gate_path, {}).get("windows_done", [])
        storage.write_json(gate_path, {"windows_done": sorted({*done_list, window.id})})
    for w in warnings:
        log(f"warning: {w}")
    return SyncOutcome(True, "synced", season, week, window.id if window else None, warnings)
