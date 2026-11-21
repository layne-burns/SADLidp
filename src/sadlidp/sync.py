"""One sync run (SPEC.md §3–§7): fetch, freeze, score, blend, tier, and write the week's data.

Fatal: the documented Sleeper /v1 endpoints (raise SleeperError). Everything else degrades: a failed
source keeps its earlier rows (marked stale), and the page says so.

Two groups of players run through the same steps side by side and never mix (positions.GROUPS): the IDPs
with the five IDP sources, and the kickers with the kicker sources (sources/registry.py). Each group has its
own players, matcher, source values, rank curves, weights and consensus call, so every DL/LB/DB number is
computed from exactly the inputs it had before kickers were tracked.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import accuracy, league_tx, scoring, storage, swing, weeklog
from . import backfill as backfillmod
from . import crosswalk as crosswalkmod
from . import form as formmod
from . import gate as gatemod
from . import kicker as kickermod
from . import other_leagues as otherlg
from .backfill import imputer_for, source_cfg
from .config import load_alias
from .consensus import consensus
from .impute import load_or_build_priors
from .matching import Matcher, load_nicknames, load_overrides
from .net import Client
from .pool import HIDE_INJURY, PlayerStatus, player_status, rostered_ids
from .positions import GROUPS, IDP_SLOTS, SLOTS, K, also_eligible, in_group, player_name, slot_of
from .rankmap import rank_values
from .roster import panel, starter_slots
from .schedule import ET, NFLVERSE_GAMES, Schedule
from .sigma import within_player_sd
from .sleeper import Sleeper, my_roster, open_spots, position_limits, resolve_league, season_of
from .sources import fp_ecr, registry
from .sources.base import SUSPECT, AdapterResult, FetchContext
from .sources.registry import K_SOURCES, SIGNALS, SOURCES  # name -> (module, kind); re-exported from here
from .tiers import build_tiers, place

SLIM_FIELDS = (
    "full_name", "first_name", "last_name", "position", "fantasy_positions",
    "team", "status", "active", "injury_status", "espn_id", "fantasy_data_id",
)  # fmt: skip


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


def _quiet(fn: Callable, default, log: Callable[[str], None]):
    """Run one step of another league's part (SPEC D25): a failure goes to the job log and costs that part only,
    with no page banner, so SADL's own page is exactly what it would have been."""
    try:
        return fn()
    except Exception:
        log(traceback.format_exc())
        return default


def make_client(cfg: dict) -> Client:
    """The run's HTTP client, spaced per host by `net.host_interval_s` (SPEC §7)."""
    return Client(host_intervals=(cfg.get("net") or {}).get("host_interval_s") or {})


def build_matcher(cfg: dict, players: dict, crosswalk: dict | None = None, group: str = "idp") -> Matcher:
    """One §6.1 matcher per run and group: the players DB, `player_overrides.toml`, the nickname table and the
    crosswalk. It knows its own group's players only (the IDPs, or the kickers for `group="k"`)."""
    root = Path(cfg.get("_root", "config"))
    return Matcher(players, load_overrides(root / "player_overrides.toml"),
                   load_nicknames(root / "aliases" / "nicknames.json"), crosswalk, slots=GROUPS[group])  # fmt: skip


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
    "residuals"`) and 3 settled weeks exist; otherwise last season's within-player SD, cached per season.
    A slot the cache lacks (K, in a cache built before kickers) takes its `[sigma_out]` config value; the
    cache is never rebuilt for it."""
    fallback = cfg.get("sigma_out") or {}
    residual = {}
    if (cfg.get("sigma_out") or {}).get("method") == "residuals":
        from .stats import residual_sigma_out

        residual = residual_sigma_out(data_dir, season, cfg, week) or {}
        if all(s in residual for s in IDP_SLOTS):
            return {**{s: float(fallback.get(s, 5.0)) for s in SLOTS if s not in residual}, **residual}
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
    return {s: float(residual.get(s) or (est or {}).get(s) or fallback.get(s, 5.0)) for s in SLOTS}


def refresh_actuals(
    sleeper: Sleeper, data_dir: Path, season: int, sched: Schedule, now: datetime, players: dict
) -> list[int]:
    """Write the tracked players' actual stat lines for past weeks: once, then again until 4 days after the
    week (stat corrections). `players` = the whole players DB; a line is kept for anyone with a slot."""
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


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def expected_by(spec: str | None, sched: Schedule, week: int) -> datetime | None:
    """A source's usual posting time for `week` (§5.2 Late sources): the last "<Day> HH:MM" ET at or before
    the week's first kickoff (e.g. "Thu 12:00" is the morning of a Thursday-night game, or the Thursday before
    a Sunday-first week). None without a setting or a schedule."""
    kicks = [g.kickoff for g in sched.week_games(week) if g.kickoff]
    if not spec or not kicks:
        return None
    try:
        day, hhmm = spec.split()
        wd, (h, m) = DAYS.index(day[:3].lower()), (int(x) for x in hhmm.split(":"))
    except ValueError:
        return None
    first = min(kicks).astimezone(ET)
    at = first.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(days=(first.weekday() - wd) % 7)
    return at if at <= first else at - timedelta(days=7)


def observed_after(source: dict, published_at: str | None = None) -> datetime | None:
    """§5.3: a player is observed by a source this week only if his kickoff is after this instant, max(the
    source's first successful fetch of the week, its `published_at`). None: the file records no fetch."""
    first = (source.get("fetches") or [source.get("last_success")])[0]
    stamps = [datetime.fromisoformat(t) for t in (first, source.get("published_at"), published_at) if t]
    return max(stamps) if stamps else None


def zero_fill(
    source: dict, valued: dict, idp: dict, status_of: Callable[[str], PlayerStatus], published_at: str | None = None
) -> dict[str, float]:  # `idp`: the players of the source's own group
    """§6.4 absence rule under §5.3: a complete source that lists neither a row nor a placeholder for an
    active player it observed (his kickoff after `observed_after`) projects him at 0. If the universe names
    `zero_fill_ids`, only those players can be filled. Everyone else stays missing. A file with no recorded
    fetch observes the players whose game is still to come."""
    complete = {slot: u for slot, u in (source.get("universe") or {}).items() if (u or {}).get("complete")}
    listed = {slot: set(u["zero_fill_ids"]) for slot, u in complete.items() if u.get("zero_fill_ids") is not None}
    placeholders = set(source.get("no_projection_ids") or [])
    since = observed_after(source, published_at)
    out = {}
    for pid, p in idp.items():
        slot = slot_of(p)
        if pid in valued or pid in placeholders or slot not in complete or not p.get("active"):
            continue
        if slot in listed and pid not in listed[slot]:
            continue
        st = status_of(pid)
        if st.kickoff is not None and (not st.locked if since is None else st.kickoff > since):
            out[pid] = 0.0
    return out


def carry_unread_lists(existing: dict, res: AdapterResult) -> None:
    """List-scoped merge (§5.2): a rank source's list that wasn't read this fetch keeps its stored ranks and
    depth, so only the lists the fetch read can delete a player."""
    read = set(res.universe)
    unread = {s: u for s, u in (existing.get("universe") or {}).items() if s not in read}
    if not unread:
        return
    for pid, row in (existing.get("rows") or {}).items():
        kept = {s: r for s, r in (row.get("ranks") or {}).items() if s in unread}
        if not kept:
            continue
        new = res.rows.get(pid)
        if new is None:
            content = {k: v for k, v in row.items() if k not in ("fetched_at", "kickoff", "late")}
            res.rows[pid] = {**content, **{k: {s: v for s, v in (row.get(k) or {}).items() if s in unread}
                                           for k in ("ranks", "ave", "std")}}  # fmt: skip
        else:
            for k in ("ranks", "ave", "std"):
                new[k] = {**{s: v for s, v in (row.get(k) or {}).items() if s in unread}, **(new.get(k) or {})}
    res.universe.update(unread)
    stored = existing.get("lists") or {}
    res.lists.update({s: stored[s] for s in unread if s in stored})


def projection_means(values: dict[str, dict[str, float]], groups: dict[str, str]) -> dict[str, float]:
    """P_i: the projection group's mean per player (equal weights, as the consensus uses them, §6.4)."""
    sums: dict[str, list[float]] = {}
    for name, vals in values.items():
        if groups.get(name) == "projection":
            for pid, v in vals.items():
                sums.setdefault(pid, []).append(v)
    return {pid: sum(vs) / len(vs) for pid, vs in sums.items()}


def badge_rows(my_panel: dict, slots, cons: dict, sigma_out: dict,
               kickoff_of: Callable[[str], datetime | None]) -> dict:  # fmt: skip
    """This sync's suggestions as badge-log rows, keyed "w|badge|over" (weeklog.update_badge_log)."""
    current = {}
    for slot in slots:
        for w, b in my_panel[slot]["badges"].items():
            kicks = [k for k in (kickoff_of(w), kickoff_of(b["over"]) if b["over"] else None) if k]
            current[f"{w}|{b['badge']}|{b['over'] or ''}"] = {
                "slot": slot, "w": w, "badge": b["badge"], "over": b["over"], "p": b["p"], "action": b["action"],
                "drop": b["drop"], "reference": b["reference"], "why": b["why"], "mu_w": round(cons[w]["mu"], 3),
                "mu_over": round(cons[b["over"]]["mu"], 3) if b["over"] else 0.0, "sigma_out": sigma_out.get(slot),
                "swing_w": cons[w].get("swing"), "swing_over": cons[b["over"]].get("swing") if b["over"] else None,
                "lock_at": min(kicks).isoformat() if kicks else None,
            }  # fmt: skip
    return current


def badge_log(wdir: Path, current: dict, now: datetime, claimed: Callable[[str], str | None]) -> dict:
    """The badges.json document: the folder's earlier log updated with this sync's rows."""
    prev = (storage.read_json(wdir / "badges.json") or {}).get("badges", {})
    return {"schema_version": 1, "badges": weeklog.update_badge_log(prev, current, now, claimed)}


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
    flags_of: Callable[[str], list] = lambda pid: [],
    extra: Callable[[str], dict] = lambda pid: {},
) -> tuple[dict, dict]:
    """badges.json and tiers_log.json documents for later evaluation (weeklog.py); written by the caller.
    `claimed(pid)` says who has rostered a player ("me", "other" or None). `extra(pid)` adds fields to a tier
    row (a kicker's `free_in` when another league is on the page, SPEC D25; nothing for an IDP)."""
    badges = badge_log(wdir, badge_rows(my_panel, tiers_by_slot, cons, sigma_out, kickoff_of), now, claimed)
    rows = {
        pid: {"slot": slot, "tier": t["tier"], "mu": round(cons[pid]["mu"], 3), "sigma": round(cons[pid]["sigma"], 3),
              **({"flags": list(f)} if (f := flags_of(pid)) else {}), **extra(pid)}  # injury tags as shown (Q), §6.6
        for slot in tiers_by_slot
        for t in tiers_by_slot[slot]
        for pid in t["players"]
    }  # fmt: skip
    tiers = weeklog.update_tiers_log(storage.read_json(wdir / "tiers_log.json") or {}, rows, now, kickoff_of, prewaiver)
    return badges, tiers


SINCE_TUE_MOVE = 1.5  # a μ move this big since Tuesday is listed (fixed once, about σ_μ's scale; SPEC §6.6)


def since_tuesday(slot: str, prewaiver: dict | None, order: list[str], cons: dict, idp: dict,
                  status_of: Callable[[str], PlayerStatus], claimed: Callable[[str], str | None],
                  claims: Callable[[str, dict], list[str]] | None = None) -> list[dict] | None:  # fmt: skip
    """What changed since the pre-waiver page for Tuesday's top 5 at `slot` (SPEC §6.6, 2026-11-17): now Out,
    Doubtful or questionable, claimed, a μ move of SINCE_TUE_MOVE or more, and players new to the top 5.
    Facts only: nothing is re-ranked or discounted. `claims(pid, tuesday_row)`, when given, words the claims
    itself (the combined kicker list names the league, SPEC D25)."""
    tue = {pid: r for pid, r in ((prewaiver or {}).get("players") or {}).items() if r.get("slot") == slot}
    if not tue:
        return None
    top = sorted(tue, key=lambda pid: (tue[pid].get("tier") or 99, -(tue[pid].get("mu") or 0.0), pid))
    out = []
    for rank, pid in enumerate(top[:5], start=1):
        st, then = status_of(pid), tue[pid]
        item = {"id": pid, "name": player_name(idp.get(pid, {})), "rank": rank, "mu_tue": then.get("mu"),
                "mu": round(cons[pid]["mu"], 2) if pid in cons else None, "changes": []}  # fmt: skip
        if claims is not None:
            item["changes"] += claims(pid, then)
        elif who := claimed(pid):
            item["changes"].append("you claimed him" if who == "me" else "claimed by another team")
        injury = [r for r in st.reasons if r in HIDE_INJURY]
        if injury:
            item["changes"].append(f"now {injury[0]}")
        elif "Q" in st.flags and "Q" not in (then.get("flags") or []):
            item["changes"].append("now questionable")
        if item["mu"] is not None and item["mu_tue"] is not None and abs(item["mu"] - item["mu_tue"]) >= SINCE_TUE_MOVE:
            item["changes"].append(f"μ {item['mu_tue']:.1f} → {item['mu']:.1f}")
        if item["changes"]:
            out.append(item)
    for pid in order[:5]:
        if pid not in top[:5]:
            was = top.index(pid) + 1 if pid in top else None
            out.append({"id": pid, "name": player_name(idp.get(pid, {})), "rank": None, "mu": round(cons[pid]["mu"], 2),
                        "changes": [f"new in the top 5 (Tuesday #{was})" if was else "new in the top 5"]})  # fmt: skip
    return out


def bye_lookahead(roster_ids: list[str], reserve: set | list, players: dict, sched: Schedule, week: int,
                  ahead: int = 3) -> list[dict]:  # fmt: skip
    """Your rostered players at a position (IR left out) with a bye in the next `ahead` weeks (schedule facts)."""
    out = []
    for pid in roster_ids:
        p = players.get(pid) or {}
        team = p.get("team")
        if pid in reserve or not team:
            continue
        for w in range(week + 1, week + 1 + ahead):
            if sched.week_games(w) and sched.game_for(team, w) is None:
                out.append({"id": pid, "name": player_name(p), "team": team, "week": w})
                break
    return out


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


def last_week_recap(data_dir: Path, season: int, week: int, sched: Schedule, sc: dict, players: dict,
                    sub: str | None = None) -> dict | None:  # fmt: skip
    """Last week's issued suggestions with both players' final scores (D22). Scores come from
    actuals.json and stay provisional until the stat-correction window closes (last kickoff + 4 d 4 h).
    `slots` and `summary` are DL/LB/DB, as always; a week that issued kicker suggestions also has
    `slots["K"]` and their own tally in `summary_k`, never pooled with the IDP one. `sub`: another league's
    folder, whose badge log holds kicker suggestions only (SPEC D25)."""
    prev = week - 1
    wd = storage.week_dir(data_dir, season, prev)
    badges = (storage.read_json((wd / sub if sub else wd) / "badges.json") or {}).get("badges", {})
    act = storage.read_json(wd / "actuals.json")
    if prev < 1 or not badges or not act:
        return None
    stats = act.get("stats") or {}
    settles = sched.settles_at(prev)
    provisional = bool(settles) and datetime.fromisoformat(act["captured_at"]) < settles

    def res(pid: str) -> dict:
        return weeklog.stat_result(stats.get(pid), sc, final=True)

    slots: dict[str, list] = {slot: [] for slot in IDP_SLOTS}
    if any(e.get("slot") == K and e.get("active") for e in badges.values()):
        slots[K] = []
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

    def tally(names) -> dict:
        counted = [x for s in names for x in slots[s] if not x["reference"] and x["outcome"] in ("hit", "miss", "push")]
        return {"hits": sum(1 for x in counted if x["outcome"] == "hit"), "decided": len(counted)}

    return {
        "week": prev,
        "provisional": provisional,
        "settles": settles.isoformat() if settles else None,
        "slots": slots,
        "summary": tally(IDP_SLOTS),
        **({"summary_k": tally((K,))} if K in slots else {}),
    }


# name -> page label: the five IDP sources, the D/ST signal, then the kicker sources (plain site names: the
# footer groups them under "Kicker sources")
SOURCE_LABELS = registry.labels()
STAT_LABELS = (
    ("idp_tkl_solo", "solo"), ("idp_tkl_ast", "ast"), ("idp_tkl_loss", "TFL"), ("idp_sack", "sack"),
    ("idp_qb_hit", "QB hit"), ("idp_pass_def", "PD"), ("idp_int", "INT"), ("idp_ff", "FF"), ("idp_fum_rec", "FR"),
    ("idp_def_td", "TD"), ("idp_safe", "safety"), ("idp_blk_kick", "blocked kick"),
    # a kicker's scored line ("82.3 FG yards", "2.8 XP", "0.4 missed FG*"; * = derived by the adapter)
    ("fgm_yds", "FG yards"), ("xpm", "XP"), ("fgmiss", "missed FG"), ("xpmiss", "missed XP"),
)  # fmt: skip


def warn_label(name: str) -> str:
    """A source's name in a page banner: its label, with "kickers" for a kicker source (both groups have a
    Sleeper, an ESPN...)."""
    label = SOURCE_LABELS.get(name, name)
    return f"{label} kickers" if registry.group_of_source(name) == "k" else label


def breakdown(pid: str, cons_row: dict | None, source_rows: dict, values: dict, groups: dict, info: dict,
              sc: dict, kickoff: datetime | None = None, now: datetime | None = None,
              placeholders: dict | None = None, ranks: dict | None = None, names=None) -> dict:  # fmt: skip
    """Per-source parts of a player's number for the page (D19): SADL points, the projected stats behind
    them (each [label, value, imputed], §6.2) or the rank behind them (rank sources: list, rank, censored), the
    share of μ, or why an enabled source has nothing for him; plus the form term's parts when it applies.
    `names` = the sources of the player's group, in page order (the IDP sources unless given)."""
    eff = (cons_row or {}).get("eff") or {}
    frozen_pts = (cons_row or {}).get("parts") or {}  # a locked player: each source's points at his kickoff
    parts = []
    for name in SOURCES if names is None else names:
        if name not in info:
            continue  # disabled
        label = SOURCE_LABELS.get(name, name)
        src = info[name] or {}
        since = src.get("observed_after")
        pts = frozen_pts[name] if name in frozen_pts else values.get(name, {}).get(pid)
        if pts is not None:
            row = (source_rows.get(name) or {}).get(pid)
            stats, est = (row or {}).get("stats") or {}, set((row or {}).get("imputed") or ())
            shown = [[lbl, round(scoring.num(stats.get(k)), 2), k in est] for k, lbl in STAT_LABELS
                     if scoring.num(sc.get(k)) and scoring.num(stats.get(k)) >= 0.05]  # fmt: skip
            part = {"source": label, "kind": groups.get(name), "pts": round(pts, 2), "share": eff.get(name),
                    "stats": shown, "late": bool(row and row.get("late")),
                    "zero": row is None and pid in values.get(name, {})}  # fmt: skip
            if groups.get(name) == "rank":
                rk = ((cons_row or {}).get("ranks") or {}).get(name) or (ranks or {}).get(name, {}).get(pid) or {}
                part.update(stats=[], zero=False, rank=rk.get("rank"), list=rk.get("list"),
                            censored=bool(rk.get("censored")))  # fmt: skip
            parts.append(part)
        elif not src.get("last_success") or not src.get("rows_used"):
            read = src.get("status") in ("ok", "not_yet_published", SUSPECT)
            parts.append({"source": label, "missing": "nothing posted for this week yet" if read
                          else "couldn't be read this sync"})  # fmt: skip
        elif src.get("stale"):
            parts.append({"source": label, "missing": "not used this week (its data is too old)"})
        elif pid in (placeholders or {}).get(name, ()):
            parts.append({"source": label, "missing": "no projection for him"})
        elif (
            kickoff is not None
            and since
            and kickoff <= datetime.fromisoformat(since)
            and (now is None or kickoff <= now)
        ):
            parts.append({"source": label, "missing": "first read this week after his game started"})  # §5.3
        elif groups.get(name) == "rank":
            parts.append({"source": label, "missing": "not on its rankings, and not projected near their end"})
        else:
            parts.append({"source": label, "missing": "no projection for him"})
    est = any(st[2] for part in parts for st in part.get("stats") or ())
    out = {"parts": parts, "mu": round(cons_row["mu"], 2) if cons_row else None, "est": est}
    if cons_row and cons_row.get("form"):  # the form term (§6.4): the consensus it was added to, and its parts
        base = cons_row["base"] if cons_row.get("base") is not None else cons_row["mu"] - cons_row["form"]["pts"]
        out.update(base=round(base, 2), form=cons_row["form"])
    return out


def footer_info(info: dict) -> dict:
    """A source's run.json entry as the page footer shows it: the adapter's report shrinks to its match
    rate and the unmatched players, counted and listed (SPEC §6.1)."""
    out = {k: v for k, v in info.items() if k != "report"}
    match = (info.get("report") or {}).get("match")
    if match:
        by = match.get("by_method") or {}
        out["match"] = {
            "rate": match.get("teamed_match_rate"),
            "unmatched": by.get("unmatched", 0) + by.get("ambiguous", 0),
            "names": [
                " ".join(str(u[k]) for k in ("name", "team", "slot") if u.get(k)) for u in match.get("unmatched") or []
            ],
        }
    return out


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
        "swing": cons_row.get("swing") if cons_row else None,  # his game-to-game swing and 1-in-5 band (§6.7)
        "floor": cons_row.get("floor") if cons_row else None,
        "ceiling": cons_row.get("ceiling") if cons_row else None,
        "result": None,
    }


def panel_statuses(pids, status_of: Callable[[str], PlayerStatus], frozen: dict,
                   cons: dict) -> dict[str, PlayerStatus]:  # fmt: skip
    """Your rostered players' statuses for the panel: a locked player is judged on his status at kickoff, so a
    later injury tag can't empty the slot (from his frozen record; with none, only `locked` is kept)."""
    out: dict[str, PlayerStatus] = {}
    for pid in pids:
        st, rec = status_of(pid), frozen.get(pid)
        out[pid] = st
        if st.locked and rec is not None:
            reasons = [*rec.get("reasons", []), "locked"]
            out[pid] = PlayerStatus(reasons, list(rec.get("flags", [])), st.kickoff, st.opponent)
        elif st.locked and pid in cons:  # projected before kickoff but no record (first week): ignore later tags
            out[pid] = PlayerStatus(["locked"], list(st.flags), st.kickoff, st.opponent)
    return out


def my_slot_out(slot: str, pnl: dict, players: dict, tracked: dict, cons: dict,
                status_of: Callable[[str], PlayerStatus], panel_status: dict, results: dict, tiers: list, z: float,
                badge_doc: dict | None, now: datetime, sched: Schedule, week: int) -> dict:  # fmt: skip
    """One position's Yours block for the page (SPEC §6.7): your players' cards with their tier against `tiers`,
    the suggested moves (one per player of yours compared against), this week's issued suggestions that have
    played (from `badge_doc`), byes ahead and the names the moves use. `pnl` is roster.panel's entry."""
    cards = []
    for pid in pnl["players"]:
        c = cons.get(pid)
        tier = place(c["mu"], c["sigma"], tiers, z) if c else None
        card = _player_card(pid, players.get(pid, {}), c, panel_status.get(pid, status_of(pid)), tier, None, True)
        card["result"] = results.get(pid)
        card.update(reserve=pid in pnl["reserve"], taxi=pid in pnl["taxi"], role=(
            "starter" if pid == pnl["starter"] else "other" if pid == pnl["other"] else None))  # fmt: skip
        cards.append(card)
    # several badges over the same player of yours are one move (§6.7, 2026-11-17): the likeliest stands for it
    moves: dict[tuple, list[tuple[str, dict]]] = {}
    for w, b in pnl["badges"].items():
        moves.setdefault((b["badge"], b.get("over"), b["reference"]), []).append((w, b))
    reps = []
    for members in moves.values():
        members.sort(key=lambda wb: (-(wb[1].get("p") or 0.0), -cons[wb[0]]["mu"], wb[0]))
        (w, b), rest = members[0], members[1:]
        reps.append((w, {**b, "others": [player_name(tracked[x]) for x, _ in rest]}))
    suggestions = [
        {**b, "player": _player_card(w, tracked[w], cons[w], status_of(w), None, None, False)}
        for w, b in sorted(reps, key=lambda kv: (kv[1]["reference"], -cons[kv[0]]["mu"], kv[0]))[:5]
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
    return {
        "players": cards,
        "count": pnl["count"],
        "limit": pnl["limit"],  # K: null (the league sets none); `open_spots` is what limits a kicker add
        **({"open_spots": pnl["open_spots"]} if slot == K else {}),
        "starter_locked": pnl["starter_locked"],
        "suggestions": suggestions,
        "byes": bye_lookahead(pnl["players"], pnl["reserve"], players, sched, week),
        "played_suggestions": played_sugg[:6],
        "played_suggestions_hidden": max(0, len(played_sugg) - 6),
        "names": names,
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
    client = client or make_client(cfg)
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
    tracked = {pid: p for pid, p in players.items() if slot_of(p)}  # every tracked player: the IDPs and the kickers
    by_group = {g: in_group(tracked, g) for g in GROUPS}  # a source sees its own group's players only
    unknown = sched.unknown_teams({p.get("team") for p in by_group["idp"].values()})
    if unknown:
        raise RuntimeError(f"team codes missing from the schedule (alias table needed): {sorted(unknown)}")
    odd = sched.unknown_teams({p.get("team") for p in by_group["k"].values()})
    if odd:  # never fatal: such a kicker simply has no game
        log(f"sync: kicker team codes missing from the schedule: {sorted(odd)}")

    def enabled(name: str) -> bool:
        return bool(((cfg.get("sources") or {}).get(name) or {}).get("enabled", True))

    def group_of_name(name: str) -> str:
        return "k" if name in K_SOURCES else "idp"

    def of(by_source: dict, group: str) -> dict:
        """The entries of a per-source dict that belong to one group's sources, in their order."""
        return {name: v for name, v in by_source.items() if group_of_name(name) == group}

    k_on = any(enabled(name) for name in K_SOURCES)  # no kicker source enabled (a replay of an old week): no K
    page_slots = IDP_SLOTS + ((K,) if k_on else ())

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
    # settled weeks saved before kickers were tracked gain their kicker lines once, add-only (SPEC §6.5 Actuals)
    actuals_k = _guard("kicker results", lambda: kickermod.add_past_actuals(sleeper, data_dir, season, sched, now,
                                                                           players) if k_on else [],
                       [], warnings, log)  # fmt: skip
    # one matcher (§6.1) and one imputer (§6.2: last season's priors, this season's actuals before the week)
    today = now.astimezone(ET).date().isoformat()
    xwalk = {}
    if enabled(fp_ecr.NAME) or ("fp_k" in K_SOURCES and enabled("fp_k")):
        xwalk = _guard("crosswalk", lambda: crosswalkmod.load(client, cache_dir, today, log), {}, warnings, log)
    matcher = _guard("player matching", lambda: build_matcher(cfg, players, xwalk), None, warnings, log)
    matchers = {"idp": matcher}  # one per group: neither knows the other group's players
    if k_on:
        matchers["k"] = _guard("kicker matching", lambda: build_matcher(cfg, players, xwalk, "k"), None, warnings, log)
    priors = _guard("imputation priors", lambda: load_or_build_priors(sleeper, data_dir, season, players, log), None,
                    warnings, log)  # fmt: skip
    imputer = imputer_for(cfg, priors, data_dir, season, week, players)

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
    placeholders: dict[str, set[str]] = {}  # per source: players it explicitly lists with no projection
    zero_filled: dict[str, set[str]] = {}
    max_age = timedelta(hours=float((cfg.get("sources") or {}).get("max_source_age_h", 96)))
    shrink = float((cfg.get("sources") or {}).get("shrink_guard", 0.6))
    fetched_ok: set[str] = set()
    rank_sources: dict[str, tuple] = {}  # name -> (rows, universe, observed_after, whole lists)
    rank_detail: dict[str, dict] = {}  # name -> pid -> {list, rank, censored?}

    # signals (D18): stored per team and frozen at each team's kickoff, never blended
    def team_kickoff(team: str) -> datetime | None:
        game = sched.game_for(team, week)
        return game.kickoff if game else None

    def read_signal(name: str, module) -> None:
        path = wdir / "sources" / f"{name}.json"
        ctx = FetchContext(client, sleeper, season, week, now, by_group["idp"], source_cfg(cfg, name), matcher,
                           imputer)  # fmt: skip
        try:
            res = module.fetch(ctx)
        except Exception as e:  # a broken signal is a footer line, never a failed sync or a banner
            log(traceback.format_exc())
            res = AdapterResult(name=name, kind="signal", status="failed", week=week, fetched_at=now.isoformat(),
                                error=f"{type(e).__name__} in fetch")  # fmt: skip
        existing = storage.load_source(path)
        info = {**res.summary(), "label": SOURCE_LABELS.get(name, name), "signal": True}
        if res.ok:
            merged, counts = storage.merge_rows(existing.get("rows", {}), res.rows, now, team_kickoff)
            storage.save_source(path, res, merged)
            info.update(merge=counts, last_success=res.fetched_at, rows_used=len(merged))
        else:
            info.update(last_success=existing.get("last_success"), rows_used=len(existing.get("rows") or {}))
        run["sources"][name] = info

    # The request order is the IDP sources, then the D/ST signal, then the kicker sources: every IDP request is
    # made exactly as before kickers were tracked, so a kicker page that blocks its host costs no IDP request.
    for name, (module, kind) in [*SOURCES.items(), *SIGNALS.items(), *K_SOURCES.items()]:
        if not enabled(name):
            continue
        if kind == "signal":
            read_signal(name, module)
            continue
        group = group_of_name(name)
        mine = by_group[group]  # this source's players: the IDPs, or the kickers
        path = wdir / "sources" / f"{name}.json"
        existing = storage.load_source(path)
        ctx = FetchContext(client, sleeper, season, week, now, mine, source_cfg(cfg, name), matchers.get(group),
                           imputer if group == "idp" else None, kickoff_of=kickoff_of)  # fmt: skip
        try:
            res = module.fetch(ctx)
        except Exception as e:  # a broken adapter is a failed source, never a failed sync (SPEC §7)
            log(traceback.format_exc())
            res = AdapterResult(name=name, kind=kind, status="failed", week=week, fetched_at=now.isoformat(),
                                error=f"{type(e).__name__} in fetch")  # fmt: skip
        if res.ok and kind == "rank":
            carry_unread_lists(existing, res)
        before = len(existing.get("rows") or {})
        if res.ok and before and len(res.rows) < shrink * before:  # SPEC §7: a shrunken fetch isn't merged
            res.status, res.error = SUSPECT, f"{len(res.rows)} rows against {before} before"
        info = {**res.summary(), "label": SOURCE_LABELS.get(name, name), "published_at": res.published_at,
                "report": res.report, **({"group": "k"} if group == "k" else {})}  # fmt: skip
        late_ok = bool(((cfg.get("sources") or {}).get(name) or {}).get("late_rows"))
        if res.ok:
            if group == "idp":
                fetched_ok.add(name)  # the gate's "this window ran" needs an IDP source, as before
            merged, counts = storage.merge_rows(existing.get("rows", {}), res.rows, now, kickoff_of, late_ok)
            storage.save_source(path, res, merged)
            info.update(merge=counts, last_success=res.fetched_at)
            lost = counts["skipped_after_kickoff"]
            if kind != "signal" and lost > 0.5 * len(res.rows) and lost > counts["frozen"]:  # SPEC §7 (2026-11-17)
                label = warn_label(name)
                warnings.append(f"{label}: {lost} of {len(res.rows)} rows were for games already started and "
                                "can't be used; this week's numbers lean on the other sources")  # fmt: skip
            rows = merged
        else:
            info["last_success"] = existing.get("last_success")
            rows = existing.get("rows", {})
            due = expected_by(((cfg.get("sources") or {}).get(name) or {}).get("expected_by"), sched, week)
            if due is not None:
                info["expected_by"] = due.isoformat()
            if res.status == "not_yet_published" and due is not None and now < due:
                info["pending"] = True  # not due yet: a footer note, never a banner (§5.2 Late sources)
            else:
                late = res.status == "not_yet_published" and due is not None
                info["late"] = late
                why = {"not_yet_published": f"week {week} not posted yet",
                       SUSPECT: "this fetch looked incomplete and was skipped"}.get(res.status, res.status)  # fmt: skip
                if late:
                    posted = f"{due:%a} {due.hour % 12 or 12}:{due:%M %p}"  # "%-I" is glibc-only (portable, same text)
                    why = f"week {week} is late (usually posted by {posted} ET)"
                why += f" ({res.error})" if res.error and not late else ""
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
        late_rows[name] = {pid: r for pid, r in rows.items() if r.get("late") and pid in mine}
        source_rows[name] = rows
        if kind == "rank":  # mapped to points once the projection group is known (§6.4)
            stored = storage.load_source(path)
            since = observed_after(stored, res.published_at)
            rank_sources[name] = (rows, stored.get("universe") or {}, since, stored.get("lists") or {})
            info.update(observed_after=since.isoformat() if since else None)
            run["sources"][name] = info
            continue
        values[name] = {
            pid: scoring.projected_points(r.get("stats") or {}, sc) for pid, r in rows.items() if pid in mine
        }
        if kind == "projection":  # zero-fill only players the source observed this week (§5.3)
            stored = storage.load_source(path)
            since = observed_after(stored, res.published_at)
            zeros = zero_fill(stored, values[name], mine, status_of, res.published_at)
            values[name].update(zeros)
            zero_filled[name] = set(zeros)
            placeholders[name] = set(stored.get("no_projection_ids") or [])
            info.update(zero_filled=len(zeros), observed_after=since.isoformat() if since else None)
        run["sources"][name] = info

    # zero-fills alone don't make a number for a player another source explicitly lists with no projection
    # (e.g. a retired player Sleeper still carries as a placeholder and ESPN no longer lists): he stays
    # "no projection", as before ESPN joined (§6.4)
    p_means: dict[str, dict[str, float]] = {}
    for g in GROUPS:  # each group on its own sources
        ph, zf, vals_g = of(placeholders, g), of(zero_filled, g), of(values, g)
        listed_empty = set().union(*ph.values()) if ph else set()
        only_zero = {pid for z in zf.values() for pid in z if pid in listed_empty
                     and all(pid in zf.get(n, ()) for n in vals_g if pid in vals_g[n])}  # fmt: skip
        for vals in vals_g.values():
            for pid in only_zero:
                vals.pop(pid, None)
        p_means[g] = projection_means(vals_g, groups)
    # rank sources: rank -> SADL points on the projection group's values, unlisted observed players censored
    m_censor = int((cfg.get("censor") or {}).get("M", 10))
    for name, (rows, universe, since, members) in rank_sources.items():
        mine = by_group[group_of_name(name)]

        def observed(pid: str, since=since, mine=mine) -> bool:
            k = status_of(pid).kickoff
            return (
                k is not None
                and bool(mine.get(pid, {}).get("active"))
                and (not status_of(pid).locked if since is None else k > since)
            )

        ranked = {pid: r for pid, r in rows.items() if pid in mine}
        values[name], rank_detail[name] = rank_values(
            ranked, p_means[group_of_name(name)], lambda pid, mine=mine: slot_of(mine.get(pid, {})), universe,
            observed, m_censor, members,
        )  # fmt: skip
        censored = sum(1 for d in rank_detail[name].values() if d.get("censored"))
        run["sources"][name].update(censored=censored, mapped=len(values[name]) - censored)
    if not any(of(values, "idp").values()):
        warnings.append("no source has usable rows for this week yet; the tiers are empty until one does")
    if k_on and not any(of(values, "k").values()):
        warnings.append("no kicker source has usable rows for this week yet; the kicker tiers are empty until one does")
    slots = {pid: slot_of(tracked[pid]) for pid in tracked}
    rec = _guard("source weights", lambda: accuracy.in_force(data_dir, season, week, sched, cfg, now), None,
                 warnings, log)  # fmt: skip
    if rec is None:  # a failure falls back to the priors for this sync and records nothing
        rec = accuracy.weights({"from_weeks": [], "sources": {}, "alpha_hat_raw": None, "alpha_n": 0}, cfg)
    run["weights"] = rec
    cons = consensus(of(values, "idp"), groups, slots, cfg, weights=accuracy.source_weights(rec, cfg),
                     alpha=float(rec["alpha"]))  # fmt: skip
    idp_ids = set(cons)
    if k_on:  # the kickers: their own sources, weights and split (the "kickers" record of weights.json, §6.5)
        krec = _guard("kicker source weights", lambda: accuracy.in_force_k(data_dir, season, week, sched, cfg, now),
                      None, warnings, log)  # fmt: skip
        if krec is None:
            krec = accuracy.weights(accuracy.EMPTY, cfg, "k")
        run["weights"] = {**rec, accuracy.K_KEY: krec}
        cons.update(consensus(of(values, "k"), groups, slots, cfg, weights=accuracy.source_weights(krec, cfg, "k"),
                              alpha=float(krec["alpha"])))  # fmt: skip
    coef = formmod.coefficients(cfg)
    if coef:  # the form term (SPEC §6.4): his earlier misses this season and his last game's snap share; IDP only
        run["form"] = {**coef, **formmod.apply(cons, slots, formmod.history(data_dir, season, week, sc), coef)}
    swing_coef = swing.coefficients(cfg)
    if swing_coef:  # per-player game-to-game swing from his projected stat mix (SPEC §6.7)
        swing_coef = {**swing_coef, **{k: {s: swing_coef[k][s] for s in page_slots} for k in ("a", "b")}}
        idp_cons = {pid: c for pid, c in cons.items() if pid in idp_ids}
        run["swing"] = {**swing_coef, **swing.apply(idp_cons, slots, of(source_rows, "idp"), groups, sc, swing_coef)}
        if k_on:  # a kicker's swing comes from `swing.kicker_var`; without it he keeps σ_out,K
            k_cons = {pid: c for pid, c in cons.items() if pid not in idp_ids}
            k_coef = swing.kicker_coefficients(cfg, swing_coef)  # his own floor/ceiling multipliers, the model tag
            ks = swing.apply(k_cons, slots, of(source_rows, "k"), groups, sc, k_coef, (K,),
                             kickermod.yards_per_make(cfg))  # fmt: skip
            run["swing"]["mean"][K] = ks["mean"][K]
            k_rec = {"lo": k_coef["lo"], "hi": k_coef["hi"], "model": k_coef["kicker_model"]}
            run["swing"]["kickers"] = {"players": ks["players"], "no_var": ks.get("no_var", 0), **k_rec}
            swing_coef = {**swing_coef, "kicker": k_rec}  # the page records it; the Stats page reads `model`
    late = {pid for name in values for pid, r in late_rows.get(name, {}).items() if r.get("late")}
    for pid in late & cons.keys():
        cons[pid]["late"] = True  # projection first captured after his kickoff (SPEC §6.5)

    # frozen week record: each player's values at his last sync before kickoff (SPEC §6.5, D15). A locked
    # player keeps exactly what he showed, whatever happens to other players or sources afterwards.
    frozen_path = wdir / "frozen.json"
    records = {
        pid: {
            "mu": round(c["mu"], 4),
            "base": round(c.get("base", c["mu"]), 4),  # the consensus before the form term
            **({"form": c["form"]} if c.get("form") else {}),
            **{k: c[k] for k in ("swing", "floor", "ceiling") if k in c},  # his game-to-game swing (§6.7)
            "sigma": round(c["sigma"], 4),
            "min": c["min"],
            "max": c["max"],
            "n_sources": c["n_sources"],
            "eff": c.get("eff") or {},
            "parts": {name: round(v[pid], 3) for name, v in values.items() if pid in v},
            "ranks": {name: d[pid] for name, d in rank_detail.items() if pid in d},
            "reasons": list(status_of(pid).reasons),
            "flags": list(status_of(pid).flags),
        }
        for pid, c in cons.items()
    }
    frozen, _ = storage.merge_rows((storage.read_json(frozen_path) or {}).get("players", {}), records, now, kickoff_of)
    for pid, rec in frozen.items():
        stored = rec.get("kickoff")
        if pid in tracked and (status_of(pid).locked or (stored and datetime.fromisoformat(stored) <= now)):
            cons[pid] = {k: rec.get(k) for k in ("mu", "base", "form", "sigma", "min", "max", "n_sources", "eff",
                                                 "parts", "ranks", "swing", "floor", "ceiling")}  # fmt: skip

    # your other leagues whose kickers the K tab also covers (SPEC D25): read after SADL's own fetches and
    # before the tiers. The kicker numbers above are shared (the leagues score kickers alike); only who is free,
    # who is yours and your lineup and room differ. A league that can't be read is a note on the K tab.
    others = [otherlg.read(sleeper, key, ocfg, season, week, user_id, sc, data_dir, now, log)
              for key, ocfg in (otherlg.configured(cfg).items() if k_on else ())]  # fmt: skip
    live = [o for o in others if o.ok]  # the leagues shown with numbers this sync
    o_rostered = {o.key: o.rostered() for o in live}
    o_mine = {o.key: {str(pid) for pid in (o.me or {}).get("players") or []} for o in live}

    # waiver pool and tiers. The kicker list is every kicker free in SADL or in another league on the page (SPEC
    # D25), and its tiers are counted over that whole list; SADL's own suggestions still weigh only the kickers
    # free in SADL (`waiver_order`, in the same order as before). Without another league both are SADL's pool.
    rostered = rostered_ids(rosters)

    def available(pid: str, slot: str) -> bool:
        """On the page's list: free in SADL, or (a kicker) free in another league shown this sync."""
        return pid not in rostered or (slot == K and any(pid not in r for r in o_rostered.values()))

    def free_of(pid: str) -> list[str]:
        """The leagues a kicker is free in, SADL first (the combined list's tags)."""
        return otherlg.free_in(pid, rostered, live, o_rostered)

    z = float((cfg.get("tiers") or {}).get("z", 1.0))
    tiers_by_slot, waiver_order, shown_order = {}, {}, {}
    for slot in page_slots:
        pool = {
            pid: (c["mu"], c["sigma"])
            for pid, c in cons.items()
            if slot_of(tracked[pid]) == slot and available(pid, slot) and status_of(pid).playable
        }
        tiers_by_slot[slot] = build_tiers(pool, z)
        shown_order[slot] = [pid for t in tiers_by_slot[slot] for pid in t["players"]]
        waiver_order[slot] = [pid for pid in shown_order[slot] if pid not in rostered]  # SADL's suggestions

    me = my_roster(rosters, user_id)
    if me is None:
        warnings.append("your roster wasn't found in the league; My IDPs is empty")
    limits = position_limits(settings, int((cfg.get("roster") or {}).get("position_limit", 2)))
    unset = [slot for slot in IDP_SLOTS if not settings.get(f"position_limit_{slot.lower()}")]
    if unset:
        warnings.append(f"league has no position limit for {', '.join(unset)}; assuming the config fallback")
    sigma_fallback = {s: float((cfg.get("sigma_out") or {}).get(s, 5.0)) for s in SLOTS}
    sigma_out = _guard(
        "σ_out estimate",
        lambda: sigma_out_for(sleeper, cfg, data_dir, season, players, sc, warnings, week),
        sigma_fallback,
        warnings,
        log,
    )
    sigma_out = {s: v for s, v in sigma_out.items() if s in page_slots or s not in SLOTS}  # no K on a page without K
    matchups = _guard("matchups", lambda: sleeper.matchups(league["league_id"], week)[0], None, warnings, log)
    _guard(
        "transactions",
        lambda: league_tx.sync_legs(sleeper, league["league_id"], data_dir, season, week, now),
        None,
        warnings,
        log,
    )  # FAAB context (§6.7): saved each sync, shown as counts only
    my_matchup = next((m for m in (matchups or []) if me and m.get("roster_id") == me.get("roster_id")), {})
    lineup = starter_slots(league.get("roster_positions") or [], my_matchup.get("starters"))
    # a locked rostered player is judged on his status at kickoff (a later injury tag can't empty the slot)
    panel_status = panel_statuses((me or {}).get("players") or [], status_of, frozen, cons)
    threshold = float((cfg.get("swap") or {}).get("threshold", 0.6))
    spots = open_spots(me, league.get("roster_positions"))  # free active roster spots: the kicker add rule
    my_panel = panel(me or {}, players, cons, panel_status, waiver_order, limits, sigma_out, threshold, lineup,
                     spots, page_slots)  # fmt: skip

    mine = {str(pid) for pid in (me or {}).get("players") or []}

    def claimed(pid: str) -> str | None:
        return "me" if pid in mine else "other" if pid in rostered else None

    # each other league's kicker panel (SPEC D25): SADL's rules (roster.py) on that league's roster, lineup and
    # room, against the kickers free there, with the shared numbers and σ_out,K
    o_panels: dict[str, dict] = {}
    o_status: dict[str, dict[str, PlayerStatus]] = {}
    for o in live:
        try:  # a failure here costs that league's panel only (its kickers keep their tags on the list)
            o_me = o.me or {}
            o_match = next((m for m in (o.matchups or []) if m.get("roster_id") == o_me.get("roster_id")), {})
            o_lineup = starter_slots(o.league.get("roster_positions") or [], o_match.get("starters"))
            o_status[o.key] = panel_statuses(o_me.get("players") or [], status_of, frozen, cons)
            o_free = [pid for pid in shown_order[K] if pid not in o_rostered[o.key]]
            o_panels[o.key] = panel(o_me, players, cons, o_status[o.key], {K: o_free},
                                    position_limits(o.league.get("settings") or {}, 2), sigma_out, threshold,
                                    o_lineup, open_spots(o_me, o.league.get("roster_positions")), (K,))  # fmt: skip
        except Exception as e:  # isolation by design: the traceback goes to the job log only
            log(traceback.format_exc())
            o.status, o.note = "failed", f"its panel couldn't be built this sync ({type(e).__name__})"

    def k_claims(pid: str, then: dict) -> list[str]:
        """Claims since Tuesday on the combined kicker list, by league: only where he was free on Tuesday."""
        sets = {"SADL": (rostered, mine), **{o.label: (o_rostered[o.key], o_mine[o.key]) for o in live}}
        out = []
        for label in then.get("free_in") or []:
            taken, yours = sets.get(label, (set(), set()))
            if pid in yours:
                out.append(f"you claimed him in {label}")
            elif pid in taken:
                out.append(f"claimed in {label}")
        return out

    def league_tags(pid: str) -> list[dict]:
        """A combined-list card's leagues: each league he is free in, with that league's badge for him."""
        free = free_of(pid)
        tags = [{"label": "SADL", "badge": my_panel[K]["badges"].get(pid)}] if "SADL" in free else []

        def badge(o) -> dict | None:
            return ((o_panels.get(o.key) or {}).get(K) or {}).get("badges", {}).get(pid)

        return tags + [{"label": o.label, "badge": badge(o)} for o in live if o.label in free]

    logs = _guard("decision logs", lambda: build_week_logs(wdir, my_panel, tiers_by_slot, cons, sigma_out, kickoff_of,
                                                         now, prewaiver=bool(window and window.kind == "pre-waiver"),
                                                         claimed=claimed, flags_of=lambda pid: status_of(pid).flags,
                                                         extra=lambda pid: ({"free_in": free_of(pid)}
                                                                            if live and slots.get(pid) == K else {})),
                  (None, None), warnings, log)  # fmt: skip
    badge_doc, tiers_doc = logs
    o_badges: dict[str, dict | None] = {}
    for o in (o for o in live if o.key in o_panels):  # each league's own suggestion log; a failure costs that log only

        def o_claimed(pid: str, o=o) -> str | None:
            return "me" if pid in o_mine[o.key] else "other" if pid in o_rostered[o.key] else None

        o_badges[o.key] = _quiet(
            lambda o=o, c=o_claimed: badge_log(
                wdir / o.key, badge_rows(o_panels[o.key], (K,), cons, sigma_out, kickoff_of), now, c
            ),
            None,
            log,
        )
    started = [pid for pid in tracked if status_of(pid).locked]
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
    for slot in page_slots:
        badges = my_panel[slot]["badges"]
        shown, out_tiers = 0, []
        for t in tiers_by_slot[slot]:
            cards = []
            for pid in t["players"]:
                if shown >= depth:
                    break
                card = _player_card(pid, tracked[pid], cons[pid], status_of(pid), t["tier"], badges.get(pid), False)
                if slot == K and live:  # the combined list: where he is free, and each league's badge (SPEC D25)
                    card["leagues"] = league_tags(pid)
                cards.append(card)
                shown += 1
            if cards:
                out_tiers.append({"tier": t["tier"], "anchor_mu": round(t["mu"], 2), "players": cards})
        no_projection = sum(
            1
            for pid, p in tracked.items()
            if slot_of(p) == slot and pid not in cons and available(pid, slot) and status_of(pid).playable
        )
        played_ids = sorted(
            (pid for pid in cons if slot_of(tracked[pid]) == slot and available(pid, slot) and status_of(pid).locked
             and was_playable(pid)),
            key=lambda pid: (logged_tier.get(pid) or 999, -cons[pid]["mu"], pid),
        )  # fmt: skip
        played = []
        for pid in played_ids[:depth]:
            card = _player_card(pid, tracked[pid], cons[pid], status_of(pid), logged_tier.get(pid), None, False)
            card["result"] = results.get(pid)
            if slot == K and live:
                card["leagues"] = [{"label": label, "badge": None} for label in free_of(pid)]
            played.append(card)
        prewaiver = (tiers_doc or {}).get("prewaiver")
        tue_k = [r for r in ((prewaiver or {}).get("players") or {}).values() if r.get("slot") == K]
        # the kicker list changed kind if Tuesday's was SADL's alone and today's is combined, or the other way
        # round: "changed since Tuesday" would then compare different lists, so it is left out for K
        same_kind = slot != K or not tue_k or all(("free_in" in r) == bool(live) for r in tue_k)
        since_tue = (since_tuesday(slot, prewaiver, shown_order[slot], cons, tracked, status_of, claimed,
                                   k_claims if slot == K and live else None)
                     if not (window and window.kind == "pre-waiver") and same_kind else None)  # fmt: skip
        positions_out[slot] = {
            "since_tuesday": since_tue,
            "tiers": out_tiers,
            "tier_count": len(tiers_by_slot[slot]),
            "hidden": len(shown_order[slot]) - shown,
            "no_projection": no_projection,
            "played": played,
            "played_hidden": max(0, len(played_ids) - depth),
        }
    my_out = {slot: my_slot_out(slot, my_panel[slot], players, tracked, cons, status_of, panel_status, results,
                                tiers_by_slot[slot], z, badge_doc, now, sched, week)
              for slot in page_slots}  # fmt: skip
    k_leagues_out = {}
    for o in others:  # each other league's kicker block (SPEC D25): its Yours, its moves and last week's results
        entry = {"label": o.label, "name": o.league.get("name"), "status": o.status, "note": o.note}
        my = _quiet(lambda o=o: my_slot_out(K, o_panels[o.key][K], players, tracked, cons, status_of, o_status[o.key],
                                            results, tiers_by_slot[K], z, o_badges.get(o.key), now, sched, week),
                    None, log) if o.ok and o.key in o_panels else None  # fmt: skip
        if o.ok and my is None:
            o.status, o.note = "failed", "its panel couldn't be built this sync"
        entry.update(status=o.status, note=o.note)
        if my is not None:
            o_settings = o.league.get("settings") or {}
            waiver_type = {0: "rolling", 1: "reverse standings", 2: "FAAB"}.get(o_settings.get("waiver_type"), "?")
            entry.update(waiver_type=waiver_type, my=my)
        entry["last_week"] = _quiet(lambda o=o: last_week_recap(data_dir, season, week, sched, sc, players, o.key),
                                    None, log)  # fmt: skip
        k_leagues_out[o.key] = entry

    archives = {**SOURCES, **SIGNALS, **K_SOURCES}  # the kicker archives last: they take what budget is left
    backfilled = _guard("backfill", lambda: backfillmod.run(archives, cfg, data_dir, season, range(1, week), now,
                                                           client=client, sleeper=sleeper, players=tracked,
                                                           matcher=matcher, matchers=matchers, priors=priors,
                                                           log=log, sched=sched),
                        {}, warnings, log)  # fmt: skip
    for name, weeks in backfilled.items():
        log(f"sync: backfilled {warn_label(name)} weeks {weeks}")  # a note, not a page banner

    recap = _guard("last week's results", lambda: last_week_recap(data_dir, season, week, sched, sc, players), None,
                   warnings, log)  # fmt: skip

    settles = sched.settles_at(week)
    run.update(
        settles_at=settles.isoformat() if settles else None,
        warnings=warnings,
        backfilled=backfilled,
        actuals_written=actuals,
        **({"kicker_actuals_added": actuals_k} if actuals_k else {}),  # settled weeks that gained kicker lines
        **({"kicker_leagues": {o.key: o.summary() for o in others}} if others else {}),  # SPEC D25
        impute_priors=(priors or {}).get("season"),  # None: built-in position rates (§6.2)
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
        "swing": swing_coef,  # the per-player swing settings in force, or null (§6.7)
        "sources": {name: footer_info(info) for name, info in run["sources"].items()},
        "warnings": warnings,
        **({"k_leagues": k_leagues_out} if k_leagues_out else {}),  # your other leagues' kickers (SPEC D25)
    }
    if k_on:  # the kicker note's number (SPEC §6.8): how often a 1-point edge wins at a typical kicker's swing
        typical = ((run.get("swing") or {}).get("mean") or {}).get(K) or sigma_out.get(K)  # their mean, else σ_out,K
        edge = kickermod.one_point_edge(typical)
        site_data["kicker"] = {"sigma_out": sigma_out.get(K), "swing": typical,
                               "one_point_edge": round(edge, 3) if edge else None}  # fmt: skip
    attach_breakdowns(
        site_data,
        lambda pid: breakdown(
            pid,
            cons.get(pid),
            source_rows,
            values,
            groups,
            run["sources"],
            sc,
            kickoff_of(pid),
            now,
            placeholders,
            rank_detail,
            K_SOURCES if slots.get(pid) == K else SOURCES,  # the sources of his own group
        ),
    )
    storage.write_json(wdir / "consensus.json", site_data)
    storage.write_json(frozen_path, {"schema_version": 1, "players": frozen})
    if badge_doc is not None:
        storage.write_json(wdir / "badges.json", badge_doc)
        storage.write_json(wdir / "tiers_log.json", tiers_doc)
    storage.write_json(wdir / "run.json", run)
    storage.write_json(wdir / "rosters.json", rosters)
    # the week's players as of each one's kickoff (teamless included): what later replays read (SPEC §6.5)
    slim = {pid: {k: p.get(k) for k in SLIM_FIELDS} for pid, p in tracked.items()}
    week_players = (storage.read_json(wdir / "players.json") or {}).get("players", {})
    week_players, _ = storage.merge_rows(week_players, slim, now, kickoff_of)
    storage.write_json(wdir / "players.json", {"schema_version": 1, "players": week_players})
    storage.write_json(season_dir / "schedule.json", sched.to_json())
    for o in others:  # each other league's own folder (other_leagues.py); nothing of SADL's is written there
        _quiet(lambda o=o: otherlg.save(o, data_dir, season, week), None, log)
        if o_badges.get(o.key) is not None:
            storage.write_json(wdir / o.key / "badges.json", o_badges[o.key])
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
    if window and fetched_ok and any(of(values, "idp").values()) and not results_missing:  # else the next slot retries
        gate_path = season_dir / "gate.json"
        done_list = storage.read_json(gate_path, {}).get("windows_done", [])
        storage.write_json(gate_path, {"windows_done": sorted({*done_list, window.id})})
    for w in warnings:
        log(f"warning: {w}")
    return SyncOutcome(True, "synced", season, week, window.id if window else None, warnings)
