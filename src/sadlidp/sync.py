"""One sync run (SPEC.md §3–§7): fetch, freeze, score, blend, tier, and write the week's data.

Fatal: the documented Sleeper /v1 endpoints (raise SleeperError). Everything else degrades: a failed
source keeps its earlier rows (marked stale), and the page says so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import gate as gatemod
from . import scoring, storage
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
from .tiers import build_tiers, place

SLIM_FIELDS = (
    "full_name", "first_name", "last_name", "position", "fantasy_positions",
    "team", "status", "active", "injury_status", "espn_id",
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
    sleeper: Sleeper, cfg: dict, data_dir: Path, season: int, players: dict, sc: dict, warnings: list
) -> dict:
    """σ_out per position from last season's actuals, cached once per season (SPEC §6.7)."""
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
    return {s: float((est or {}).get(s) or fallback.get(s, 5.0)) for s in IDP_SLOTS}


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
        settled_at = max(kicks) + timedelta(days=4, hours=4)
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


def _player_card(pid, p, cons_row, status: PlayerStatus, tier, badge, mine: bool) -> dict:
    return {
        "id": pid,
        "name": player_name(p),
        "team": p.get("team"),
        "opp": status.opponent,
        "slot": slot_of(p),
        "also": also_eligible(p),
        "mu": round(cons_row["mu"], 2) if cons_row else None,
        "sigma": round(cons_row["sigma"], 2) if cons_row else None,
        "min": round(cons_row["min"], 2) if cons_row and cons_row["min"] is not None else None,
        "max": round(cons_row["max"], 2) if cons_row and cons_row["max"] is not None else None,
        "n": cons_row["n_sources"] if cons_row else 0,
        "tier": tier,
        "flags": status.flags,
        "unavailable": status.reasons,
        "kickoff": status.kickoff.isoformat() if status.kickoff else None,
        "badge": badge,
        "mine": mine,
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
    max_age = timedelta(hours=float((cfg.get("sources") or {}).get("max_source_age_h", 96)))
    for name, (module, kind) in SOURCES.items():
        if not ((cfg.get("sources") or {}).get(name) or {}).get("enabled", True):
            continue
        path = wdir / "sources" / f"{name}.json"
        existing = storage.load_source(path)
        res = module.fetch(sleeper, season, week, now)
        info = res.summary()
        if res.ok:
            merged, counts = storage.merge_rows(existing.get("rows", {}), res.rows, now, kickoff_of)
            storage.save_source(path, res, merged)
            info.update(merge=counts, last_success=res.fetched_at)
            rows = merged
        else:
            info["last_success"] = existing.get("last_success")
            rows = existing.get("rows", {})
            why = f"week {week} not posted yet" if res.status == "not_yet_published" else res.status
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
        values[name] = {
            pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items() if pid in idp
        }
        if kind == "projection":
            zeros = zero_fill(storage.load_source(path), values[name], idp, status_of)
            values[name].update(zeros)
            info["zero_filled"] = len(zeros)
        run["sources"][name] = info

    if not values:
        warnings.append("no source has usable rows for this week yet; the tiers are empty until one does")
    cons = consensus(values, groups, {pid: slot_of(idp[pid]) for pid in idp}, cfg)

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
    sigma_out = sigma_out_for(sleeper, cfg, data_dir, season, players, sc, warnings)
    matchups, _ = sleeper.matchups(league["league_id"], week)
    my_matchup = next((m for m in (matchups or []) if me and m.get("roster_id") == me.get("roster_id")), {})
    lineup = starter_slots(league.get("roster_positions") or [], my_matchup.get("starters"))
    for pid in (me or {}).get("players") or []:
        status_of(pid)
    threshold = float((cfg.get("swap") or {}).get("threshold", 0.6))
    my_panel = panel(me or {}, players, cons, statuses, waiver_order, limits, sigma_out, threshold, lineup)

    # site data: only μ, σ, range, n, position, tier and flags (SPEC §6.4) -- never per-source values
    depth = int((cfg.get("display") or {}).get("depth", 25))
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
        positions_out[slot] = {
            "tiers": out_tiers,
            "tier_count": len(tiers_by_slot[slot]),
            "hidden": len(waiver_order[slot]) - shown,
            "no_projection": no_projection,
        }
    my_out = {}
    for slot in IDP_SLOTS:
        pnl = my_panel[slot]
        cards = []
        for pid in pnl["players"]:
            c = cons.get(pid)
            tier = place(c["mu"], c["sigma"], tiers_by_slot[slot], z) if c else None
            card = _player_card(pid, players.get(pid, {}), c, status_of(pid), tier, None, True)
            card.update(reserve=pid in pnl["reserve"], taxi=pid in pnl["taxi"], role=(
                "starter" if pid == pnl["starter"] else "other" if pid == pnl["other"] else None))  # fmt: skip
            cards.append(card)
        suggestions = [
            {**b, "player": _player_card(w, idp[w], cons[w], status_of(w), None, None, False)}
            for w, b in sorted(pnl["badges"].items(), key=lambda kv: (-cons[kv[0]]["mu"], kv[0]))[:5]
        ]
        my_out[slot] = {
            "players": cards,
            "count": pnl["count"],
            "limit": pnl["limit"],
            "starter_locked": pnl["starter_locked"],
            "suggestions": suggestions,
            "names": {pid: player_name(players.get(pid, {})) for pid in pnl["players"]},
        }

    backfilled = backfill_projections(sleeper, data_dir, season, week, now)
    actuals = refresh_actuals(sleeper, data_dir, season, sched, now, players)
    if backfilled:
        warnings.append(f"backfilled Sleeper projections for weeks {backfilled}")

    run.update(warnings=warnings, actuals_written=actuals, window=window.id if window else "forced")
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
        "sigma_out": sigma_out,
        "sources": run["sources"],
        "warnings": warnings,
    }
    storage.write_json(wdir / "consensus.json", site_data)
    storage.write_json(wdir / "run.json", run)
    storage.write_json(wdir / "rosters.json", rosters)
    storage.write_json(
        wdir / "players_idp.json", {pid: {k: p.get(k) for k in SLIM_FIELDS} for pid, p in idp.items() if p.get("team")}
    )
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
    if window and values:  # with no usable source the window stays open, so the next slot retries
        gate_path = season_dir / "gate.json"
        done_list = storage.read_json(gate_path, {}).get("windows_done", [])
        storage.write_json(gate_path, {"windows_done": sorted({*done_list, window.id})})
    for w in warnings:
        log(f"warning: {w}")
    return SyncOutcome(True, "synced", season, week, window.id if window else None, warnings)
