"""One sync run (SPEC.md §3–§7): fetch, freeze, score, blend, tier, and write the week's data.

Fatal: the documented Sleeper /v1 endpoints (raise SleeperError). Everything else degrades: a failed
source keeps its earlier rows (marked stale), and the page says so.

Three groups of players run through the same steps side by side and never mix (positions.GROUPS): the IDPs
with the five IDP sources, the kickers with the kicker sources, and the team defenses with theirs
(sources/registry.py, D25). Each group has its own players, matcher, source values, rank curves, weights and
consensus call, so every DL/LB/DB number is computed from exactly the inputs it had before kickers were
tracked, and every kicker number from what it had before team defenses were.

One consensus, several leagues (D26). Everything up to the frozen records is computed once, in the primary
league's scoring (SADL's). Each league then renders its own view of it: its rosters, its waiver pool and tiers,
its roster panel and suggestions, its logs and its page. The primary league writes under `data/<season>/` as
it always has; an extra league (`[leagues.<key>]`) writes only its own files under
`data/leagues/<key>/<season>/`, shows only the slots its roster starts, and is rendered only where its scoring
equals the primary league's on every key its groups' lines carry. An extra league that fails never touches
the primary league's files, page or banners.
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
from .backfill import imputer_for, source_cfg
from .config import load_alias
from .consensus import consensus
from .impute import load_or_build_priors
from .matching import Matcher, load_nicknames, load_overrides
from .net import Client
from .pool import HIDE_INJURY, PlayerStatus, player_status, rostered_ids
from .positions import (
    DEF,
    GROUPS,
    IDP_SLOTS,
    ROOM_SLOTS,
    SLOTS,
    K,
    also_eligible,
    group_of,
    in_group,
    player_name,
    slot_of,
)
from .rankmap import rank_values
from .roster import panel, starter_slots
from .schedule import ET, NFLVERSE_GAMES, Schedule
from .sigma import within_player_sd
from .sleeper import Sleeper, my_roster, open_spots, position_limits, resolve_league, season_of
from .sources import fp_ecr, registry
from .sources.base import SUSPECT, AdapterResult, FetchContext
from .sources.registry import DEF_SOURCES, K_SOURCES, SIGNALS, SOURCES  # name -> (module, kind); re-exported
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
    leagues: dict[str, dict] = field(default_factory=dict)  # extra leagues (D26): key -> {"status", "warnings"}


def _guard(label: str, fn: Callable, default, warnings: list[str], log: Callable[[str], None]):
    """Run one non-core step; a failure becomes a warning, never a failed sync (SPEC §7)."""
    try:
        return fn()
    except Exception as e:  # isolation by design: the traceback goes to the job log only
        warnings.append(f"{label} failed ({type(e).__name__}); skipped this sync")
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
) -> tuple[dict, dict]:
    """badges.json and tiers_log.json documents for later evaluation (weeklog.py); written by the caller.
    `claimed(pid)` says who has rostered a player ("me", "other" or None)."""
    current = {}
    for slot in tiers_by_slot:  # the page's slots
        for w, b in my_panel[slot]["badges"].items():
            kicks = [k for k in (kickoff_of(w), kickoff_of(b["over"]) if b["over"] else None) if k]
            current[f"{w}|{b['badge']}|{b['over'] or ''}"] = {
                "slot": slot, "w": w, "badge": b["badge"], "over": b["over"], "p": b["p"], "action": b["action"],
                "drop": b["drop"], "reference": b["reference"], "why": b["why"], "mu_w": round(cons[w]["mu"], 3),
                "mu_over": round(cons[b["over"]]["mu"], 3) if b["over"] else 0.0, "sigma_out": sigma_out.get(slot),
                "swing_w": cons[w].get("swing"), "swing_over": cons[b["over"]].get("swing") if b["over"] else None,
                "lock_at": min(kicks).isoformat() if kicks else None,
            }  # fmt: skip
    prev = (storage.read_json(wdir / "badges.json") or {}).get("badges", {})
    badges = {"schema_version": 1, "badges": weeklog.update_badge_log(prev, current, now, claimed)}
    rows = {
        pid: {"slot": slot, "tier": t["tier"], "mu": round(cons[pid]["mu"], 3), "sigma": round(cons[pid]["sigma"], 3),
              **({"flags": list(f)} if (f := flags_of(pid)) else {})}  # injury tags as shown (Q), §6.6
        for slot in tiers_by_slot
        for t in tiers_by_slot[slot]
        for pid in t["players"]
    }  # fmt: skip
    tiers = weeklog.update_tiers_log(storage.read_json(wdir / "tiers_log.json") or {}, rows, now, kickoff_of, prewaiver)
    return badges, tiers


SINCE_TUE_MOVE = 1.5  # a μ move this big since Tuesday is listed (fixed once, about σ_μ's scale; SPEC §6.6)


def since_tuesday(slot: str, prewaiver: dict | None, order: list[str], cons: dict, idp: dict,
                  status_of: Callable[[str], PlayerStatus],
                  claimed: Callable[[str], str | None]) -> list[dict] | None:  # fmt: skip
    """What changed since the pre-waiver page for Tuesday's top 5 at `slot` (SPEC §6.6, 2026-11-17): now Out,
    Doubtful or questionable, claimed, a μ move of SINCE_TUE_MOVE or more, and players new to the top 5.
    Facts only: nothing is re-ranked or discounted."""
    tue = {pid: r for pid, r in ((prewaiver or {}).get("players") or {}).items() if r.get("slot") == slot}
    if not tue:
        return None
    top = sorted(tue, key=lambda pid: (tue[pid].get("tier") or 99, -(tue[pid].get("mu") or 0.0), pid))
    out = []
    for rank, pid in enumerate(top[:5], start=1):
        st, then = status_of(pid), tue[pid]
        item = {"id": pid, "name": player_name(idp.get(pid, {})), "rank": rank, "mu_tue": then.get("mu"),
                "mu": round(cons[pid]["mu"], 2) if pid in cons else None, "changes": []}  # fmt: skip
        who = claimed(pid)
        if who:
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
                    league_dir: Path | None = None, idp: bool = True) -> dict | None:  # fmt: skip
    """Last week's issued suggestions with both players' final scores (D22). Scores come from
    actuals.json and stay provisional until the stat-correction window closes (last kickoff + 4 d 4 h).
    `slots` and `summary` are DL/LB/DB, as always; a week that issued kicker suggestions also has
    `slots["K"]` and their own tally in `summary_k`, and one that issued team-defense suggestions `slots["DEF"]`
    and `summary_def`, never pooled with the IDP one. An extra league (D26) reads its own badges from
    `league_dir` and the shared results from `data_dir`; with `idp` False (a league starting no IDP) its
    `slots` holds only the K and DEF it issued, and `summary` counts nothing."""
    prev = week - 1
    wd = storage.week_dir(data_dir, season, prev)
    badges = (storage.read_json(storage.week_dir(league_dir or data_dir, season, prev) / "badges.json") or {}).get(
        "badges", {}
    )
    act = storage.read_json(wd / "actuals.json")
    if prev < 1 or not badges or not act:
        return None
    stats = act.get("stats") or {}
    settles = sched.settles_at(prev)
    provisional = bool(settles) and datetime.fromisoformat(act["captured_at"]) < settles

    def res(pid: str) -> dict:
        return weeklog.stat_result(stats.get(pid), sc, final=True)

    slots: dict[str, list] = {slot: [] for slot in IDP_SLOTS} if idp else {}
    for room in ROOM_SLOTS:
        if any(e.get("slot") == room and e.get("active") for e in badges.values()):
            slots[room] = []
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
        "summary": tally(IDP_SLOTS if idp else ()),
        **({"summary_k": tally((K,))} if K in slots else {}),
        **({"summary_def": tally((DEF,))} if DEF in slots else {}),
    }


# name -> page label: the five IDP sources, the D/ST signal, the kicker sources, then the team-defense sources
# (plain site names: the footer groups them under "Kicker sources" and "Team-defense sources")
SOURCE_LABELS = registry.labels()
STAT_LABELS = (
    ("idp_tkl_solo", "solo"), ("idp_tkl_ast", "ast"), ("idp_tkl_loss", "TFL"), ("idp_sack", "sack"),
    ("idp_qb_hit", "QB hit"), ("idp_pass_def", "PD"), ("idp_int", "INT"), ("idp_ff", "FF"), ("idp_fum_rec", "FR"),
    ("idp_def_td", "TD"), ("idp_safe", "safety"), ("idp_blk_kick", "blocked kick"),
    # a kicker's scored line ("82.3 FG yards", "2.8 XP", "0.4 missed FG*"; * = derived by the adapter)
    ("fgm_yds", "FG yards"), ("xpm", "XP"), ("fgmiss", "missed FG"), ("xpmiss", "missed XP"),
)  # fmt: skip
# a team defense's line (D25): points allowed (never scored itself: its tier is), then what scores
DEF_STAT_LABELS = (
    ("pts_allow", "pts allowed"), ("sack", "sack"), ("int", "INT"), ("fum_rec", "FR"), ("ff", "FF"),
    ("def_td", "TD"), ("safe", "safety"), ("blk_kick", "blocked kick"),
)  # fmt: skip
SHOWN_UNSCORED = frozenset({"pts_allow"})  # shown although the league scores only its tiers


def warn_label(name: str) -> str:
    """A source's name in a page banner: its label, with "kickers" for a kicker source and "defenses" for a
    team-defense source (the groups share a Sleeper, an ESPN...)."""
    label = SOURCE_LABELS.get(name, name)
    group = registry.group_of_source(name)
    return f"{label} kickers" if group == "k" else f"{label} defenses" if group == "def" else label


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
    stat_labels = DEF_STAT_LABELS if names is not None and set(names) <= set(DEF_SOURCES) else STAT_LABELS
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
            shown = [[lbl, round(scoring.num(stats.get(k)), 2), k in est] for k, lbl in stat_labels
                     if (scoring.num(sc.get(k)) or k in SHOWN_UNSCORED)
                     and scoring.num(stats.get(k)) >= 0.05]  # fmt: skip
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


@dataclass(frozen=True)
class LeagueView:
    """One league's view of the shared consensus (D26): whose roster, which slots its page shows, and where its
    own files go (`base`: `data_dir` for the primary league, `data_dir/leagues/<key>` for an extra one)."""

    key: str | None  # None: the primary league (SADL)
    league: dict  # Sleeper's /league payload
    settings: dict
    user_id: str
    rosters: list[dict]
    slots: tuple[str, ...]  # the page's slots, in page order
    base: Path
    sc: dict  # the league's own scoring_settings (recorded in its league.json)

    @property
    def groups(self) -> set[str]:
        """The source groups its page lists: those of its slots (the fp_dst signal counts as IDP)."""
        return {group_of(s) for s in self.slots} | ({"idp"} if self.key is None else set())

    @property
    def label(self) -> str:
        """What its roster panel is called: "IDPs" where it starts any, else its slots ("K/DEF")."""
        return "IDPs" if any(s in IDP_SLOTS for s in self.slots) else "/".join(self.slots)


# The scoring keys of a group's lines, by prefix or name (D26 scoring check); keys found in this week's lines
# are added to these, so a key no list here names is still compared.
GROUP_KEYS = {
    "k": ("fg", "xp"),
    "def": ("pts_allow", "yds_allow", "sack", "int", "fum_rec", "ff", "safe", "blk_kick", "def_", "st_", "fum_rec_td"),
}


def league_slots(roster_positions: list | None, on: dict[str, bool]) -> tuple[str, ...]:
    """The tracked slots a league starts, in page order (D26): its `roster_positions` (an IDP_FLEX starts all
    three IDP slots), less any group no source is enabled for."""
    rp = {str(p).upper() for p in roster_positions or []}
    if "IDP_FLEX" in rp:
        rp |= set(IDP_SLOTS)
    return tuple(s for s in SLOTS if s in rp and on.get(group_of(s), False))


def scoring_differences(sc_a: dict, sc_b: dict, groups: set[str], lines: list[dict]) -> list[str]:
    """The scoring keys two leagues score differently among those a group's lines can carry: every key of
    `lines` and every key `GROUP_KEYS` names for `groups`. Empty: the shared consensus is in both leagues'
    points (D26)."""
    prefixes = tuple(x for g in groups for x in GROUP_KEYS.get(g, ()))
    keys = {k for line in lines for k in line} | {k for k in {*sc_a, *sc_b} if k.startswith(prefixes)}
    return sorted(k for k in keys if scoring.num(sc_a.get(k)) != scoring.num(sc_b.get(k)))


def extra_league(sleeper: Sleeper, cfg: dict, key: str, table: dict, data_dir: Path, season: int, week: int,
                 on: dict[str, bool], sc: dict, source_rows: dict, live_stats, tracked: dict,
                 ) -> tuple[LeagueView | None, str | None]:  # fmt: skip
    """An extra league's view (D26), or (None, why) when it isn't rendered this sync: its season is over, it
    starts none of the tracked slots, or it scores their lines differently from the primary league (the
    shared consensus would be in the wrong points; a per-league consensus is future work)."""
    base = data_dir / "leagues" / key
    cached = storage.read_json(base / str(season) / "league.json", {})
    league, user_id = resolve_league(sleeper, cfg, season, cached.get("user_id"), table=table)
    settings = league.get("settings") or {}
    if league.get("status") == "complete":
        return None, "league is complete"
    if week > gatemod.last_fantasy_week(settings):
        return None, f"week {week} is after the league's last week"
    slots = league_slots(league.get("roster_positions"), on)
    if not slots:
        return None, "it starts none of the tracked slots"
    groups = {group_of(s) for s in slots}
    lines = [r.get("stats") or {} for name, rows in source_rows.items() if registry.group_of_source(name) in groups
             for r in (rows or {}).values() if isinstance(r, dict)]  # fmt: skip
    lines += [
        ln
        for pid, ln in (live_stats or {}).items()
        if isinstance(ln, dict)
        and group_of(slot_of(tracked.get(str(pid)) or {})) in groups
        and slot_of(tracked.get(str(pid)) or {})
    ]
    differ = scoring_differences(sc, league.get("scoring_settings") or {}, groups, lines)
    if differ:
        return None, f"its scoring differs from the primary league's on {', '.join(differ[:6])}"
    rosters = sleeper.rosters(league["league_id"])
    return LeagueView(key, league, settings, user_id, rosters, slots, base, league.get("scoring_settings") or {}), None


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
    tracked = {pid: p for pid, p in players.items() if slot_of(p)}  # every tracked player: IDPs, kickers, defenses
    by_group = {g: in_group(tracked, g) for g in GROUPS}  # a source sees its own group's players only
    unknown = sched.unknown_teams({p.get("team") for p in by_group["idp"].values()})
    if unknown:
        raise RuntimeError(f"team codes missing from the schedule (alias table needed): {sorted(unknown)}")
    odd = sched.unknown_teams({p.get("team") for p in by_group["k"].values()})
    if odd:  # never fatal: such a kicker simply has no game
        log(f"sync: kicker team codes missing from the schedule: {sorted(odd)}")
    odd = sched.unknown_teams({p.get("team") for p in by_group["def"].values()})
    if odd:  # never fatal: such a team defense simply has no game
        log(f"sync: team-defense codes missing from the schedule: {sorted(odd)}")

    def enabled(name: str) -> bool:
        return bool(((cfg.get("sources") or {}).get(name) or {}).get("enabled", True))

    def group_of_name(name: str) -> str:
        return registry.group_of_source(name)  # the fp_dst signal and any undeclared name: "idp", as always

    def of(by_source: dict, group: str) -> dict:
        """The entries of a per-source dict that belong to one group's sources, in their order."""
        return {name: v for name, v in by_source.items() if group_of_name(name) == group}

    k_on = any(enabled(name) for name in K_SOURCES)  # no kicker source enabled (a replay of an old week): no K
    def_on = any(enabled(name) for name in DEF_SOURCES)  # computed for whichever league shows DEF (D25, D26)
    page_slots = IDP_SLOTS + ((K,) if k_on else ())  # the primary league's page (SADL starts no DEF)
    swing_slots = page_slots  # the slots with a swing model: IDP and K, never DEF (D25)
    on = {"idp": True, "k": k_on, "def": def_on}
    tagged: list[tuple[str | None, str]] = []  # every banner with its group (None: every league's page), D26

    def note(group: str | None, text: str) -> None:
        """A banner: on the primary page unless it is about team defenses (SADL shows none), and kept with its
        group for an extra league's page."""
        tagged.append((group, text))
        if group != "def":
            warnings.append(text)

    def guarded(group: str | None, label: str, fn: Callable, default):
        """`_guard` whose banner, if any, is a `note` of one group."""
        out: list[str] = []
        value = _guard(label, fn, default, out, log)
        for text in out:
            note(group, text)
        return value

    sc = league.get("scoring_settings") or {}
    statuses: dict[str, PlayerStatus] = {}

    def status_of(pid: str) -> PlayerStatus:
        if pid not in statuses:
            statuses[pid] = player_status(players.get(pid, {}), sched, week, now)
        return statuses[pid]

    def kickoff_of(pid: str) -> datetime | None:
        return status_of(pid).kickoff

    # actuals first, so this sync's imputation and logs see last week's stats (SPEC §6.5)
    actuals = guarded(None, "actuals refresh", lambda: refresh_actuals(sleeper, data_dir, season, sched, now,
                                                                       players), [])  # fmt: skip
    # settled weeks saved before kickers were tracked gain their kicker lines once, add-only (SPEC §6.5 Actuals)
    fetched_stats: dict = {}  # settled weeks' stats this sync: the two add-only passes share one request a week
    actuals_k = guarded("k", "kicker results", lambda: kickermod.add_past_actuals(sleeper, data_dir, season, sched,
                                                                                   now, players,
                                                                                   fetched=fetched_stats)
                        if k_on else [], [])  # fmt: skip
    # and those saved before team defenses were tracked gain theirs, the same way (D25)
    actuals_def = guarded("def", "team-defense results",
                          lambda: kickermod.add_past_actuals(sleeper, data_dir, season, sched, now, players,
                                                             group="def", fetched=fetched_stats)
                          if def_on else [], [])  # fmt: skip
    # one matcher (§6.1) and one imputer (§6.2: last season's priors, this season's actuals before the week)
    today = now.astimezone(ET).date().isoformat()
    xwalk = {}
    if enabled(fp_ecr.NAME) or ("fp_k" in K_SOURCES and enabled("fp_k")):
        xwalk = guarded(None, "crosswalk", lambda: crosswalkmod.load(client, cache_dir, today, log), {})
    matcher = _guard("player matching", lambda: build_matcher(cfg, players, xwalk), None, warnings, log)
    matchers = {"idp": matcher}  # one per group: none knows another group's players
    if k_on:
        matchers["k"] = guarded("k", "kicker matching", lambda: build_matcher(cfg, players, xwalk, "k"), None)
    if def_on:
        matchers["def"] = guarded("def", "team-defense matching", lambda: build_matcher(cfg, players, xwalk, "def"),
                                  None)  # fmt: skip
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
    pages: dict = {}  # pages two adapters read, fetched once this run (FantasyPros dst.php: fp_dst and fp_def)

    # signals (D18): stored per team and frozen at each team's kickoff, never blended
    def team_kickoff(team: str) -> datetime | None:
        game = sched.game_for(team, week)
        return game.kickoff if game else None

    def read_signal(name: str, module) -> None:
        path = wdir / "sources" / f"{name}.json"
        ctx = FetchContext(client, sleeper, season, week, now, by_group["idp"], source_cfg(cfg, name), matcher,
                           imputer, pages=pages)  # fmt: skip
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

    # The request order is the IDP sources, then the D/ST signal, then the kicker sources, then the team-defense
    # sources: every IDP request is made exactly as before kickers were tracked, and every kicker request as
    # before team defenses were, so a later group's page that blocks its host costs no earlier request.
    for name, (module, kind) in [*SOURCES.items(), *SIGNALS.items(), *K_SOURCES.items(), *DEF_SOURCES.items()]:
        if not enabled(name):
            continue
        if kind == "signal":
            read_signal(name, module)
            continue
        group = group_of_name(name)
        mine = by_group[group]  # this source's players: the IDPs, the kickers or the team defenses
        path = wdir / "sources" / f"{name}.json"
        existing = storage.load_source(path)
        ctx = FetchContext(client, sleeper, season, week, now, mine, source_cfg(cfg, name), matchers.get(group),
                           imputer if group == "idp" else None, kickoff_of=kickoff_of, pages=pages)  # fmt: skip
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
                "report": res.report, **({"group": group} if group != "idp" else {})}  # fmt: skip
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
                note(group, f"{label}: {lost} of {len(res.rows)} rows were for games already started and "
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
                note(group, f"{name}: {why}" + ("; using earlier rows" if rows else ""))
        info["rows_used"] = len(rows)
        last = info.get("last_success")
        if not last or now - datetime.fromisoformat(last) > max_age:
            if rows:
                note(group, f"{name}: rows older than {max_age}; left out of the consensus")
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
        note("k", "no kicker source has usable rows for this week yet; the kicker tiers are empty until one does")
    if def_on and not any(of(values, "def").values()):
        note("def", "no team-defense source has usable rows for this week yet; the defense tiers are empty until one "
             "does")  # fmt: skip
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
        krec = guarded("k", "kicker source weights",
                       lambda: accuracy.in_force_k(data_dir, season, week, sched, cfg, now), None)  # fmt: skip
        if krec is None:
            krec = accuracy.weights(accuracy.EMPTY, cfg, "k")
        run["weights"] = {**rec, accuracy.K_KEY: krec}
        cons.update(consensus(of(values, "k"), groups, slots, cfg, weights=accuracy.source_weights(krec, cfg, "k"),
                              alpha=float(krec["alpha"])))  # fmt: skip
    if def_on:  # the team defenses: their own sources, weights and split (the "defenses" record, D25)
        drec = guarded("def", "team-defense source weights",
                       lambda: accuracy.in_force_group(data_dir, season, week, sched, cfg, now, "def"),
                       None)  # fmt: skip
        if drec is None:
            drec = accuracy.weights(accuracy.EMPTY, cfg, "def")
        run["weights"] = {**run["weights"], accuracy.DEF_KEY: drec}
        cons.update(consensus(of(values, "def"), groups, slots, cfg,
                              weights=accuracy.source_weights(drec, cfg, "def"),
                              alpha=float(drec["alpha"])))  # fmt: skip
    coef = formmod.coefficients(cfg)
    if coef:  # the form term (SPEC §6.4): his earlier misses this season and his last game's snap share; IDP only
        run["form"] = {**coef, **formmod.apply(cons, slots, formmod.history(data_dir, season, week, sc), coef)}
    swing_coef = swing.coefficients(cfg)
    if swing_coef:  # per-player game-to-game swing from his projected stat mix (SPEC §6.7)
        swing_coef = {**swing_coef, **{k: {s: swing_coef[k][s] for s in swing_slots} for k in ("a", "b")}}
        idp_cons = {pid: c for pid, c in cons.items() if pid in idp_ids}
        run["swing"] = {**swing_coef, **swing.apply(idp_cons, slots, of(source_rows, "idp"), groups, sc, swing_coef)}
        if k_on:  # a kicker's swing comes from `swing.kicker_var`; without it he keeps σ_out,K
            k_cons = {pid: c for pid, c in cons.items() if slots.get(pid) == K}  # never a team defense (D25)
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

    # Everything above is shared by every league (D26); what follows is one league's view of it. The primary
    # league renders first, exactly as it always did; the shared steps it reaches on the way (σ_out, live
    # scores, backfill) run once there, and an extra league reuses their results.
    z = float((cfg.get("tiers") or {}).get("z", 1.0))
    shared: dict = {}

    def page_swing(page_slots: tuple[str, ...], primary: bool) -> dict | None:
        """The swing settings a page records: the primary page's as they are; an extra league's cut to its slots."""
        if not swing_coef or primary:
            return swing_coef
        out = {**swing_coef, **{k: {s: v for s, v in (swing_coef.get(k) or {}).items() if s in page_slots}
                                for k in ("a", "b")}}  # fmt: skip
        if K not in page_slots:
            out.pop("kicker", None)
        return out

    def render(lg: LeagueView, page_warnings: list[str]) -> None:
        """One league's page and logs from the shared consensus: its waiver pool, tiers, roster panel and
        suggestions, written under `lg.base` (`data_dir` for the primary league)."""
        league, settings, user_id, rosters, page_slots = lg.league, lg.settings, lg.user_id, lg.rosters, lg.slots
        lwdir = storage.week_dir(lg.base, season, week)
        # waiver pool and tiers
        rostered = rostered_ids(rosters)
        tiers_by_slot, waiver_order = {}, {}
        for slot in page_slots:
            pool = {
                pid: (c["mu"], c["sigma"])
                for pid, c in cons.items()
                if slot_of(tracked[pid]) == slot and pid not in rostered and status_of(pid).playable
            }
            tiers_by_slot[slot] = build_tiers(pool, z)
            waiver_order[slot] = [pid for t in tiers_by_slot[slot] for pid in t["players"]]

        me = my_roster(rosters, user_id)
        if me is None:
            page_warnings.append(f"your roster wasn't found in the league; My {lg.label} is empty")
        fallback = int((cfg.get("roster") or {}).get("position_limit", 2))
        limits = position_limits(settings, fallback, None if lg.key is None else page_slots)
        unset = [
            slot for slot in IDP_SLOTS if slot in page_slots and not settings.get(f"position_limit_{slot.lower()}")
        ]
        if unset:
            page_warnings.append(f"league has no position limit for {', '.join(unset)}; assuming the config fallback")
        if "sigma_out" not in shared:  # once, on the primary page (its banners concern the IDP cache only)
            sigma_fallback = {s: float((cfg.get("sigma_out") or {}).get(s, 5.0)) for s in SLOTS}
            shared["sigma_out"] = _guard(
                "σ_out estimate",
                lambda: sigma_out_for(sleeper, cfg, data_dir, season, players, sc, page_warnings, week),
                sigma_fallback,
                page_warnings,
                log,
            )
        sigma_out = {
            s: v for s, v in shared["sigma_out"].items() if s in page_slots or s not in SLOTS
        }  # its slots only
        matchups = _guard("matchups", lambda: sleeper.matchups(league["league_id"], week)[0], None, page_warnings, log)
        _guard(
            "transactions",
            lambda: league_tx.sync_legs(sleeper, league["league_id"], lg.base, season, week, now),
            None,
            page_warnings,
            log,
        )  # FAAB context (§6.7): saved each sync, shown as counts only
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
        spots = open_spots(me, league.get("roster_positions"))  # free active roster spots: the kicker add rule
        my_panel = panel(me or {}, players, cons, panel_status, waiver_order, limits, sigma_out, threshold, lineup,
                         spots, page_slots)  # fmt: skip

        mine = {str(pid) for pid in (me or {}).get("players") or []}

        def claimed(pid: str) -> str | None:
            return "me" if pid in mine else "other" if pid in rostered else None

        prewaiver = bool(window and window.kind == "pre-waiver")
        logs = _guard("decision logs", lambda: build_week_logs(lwdir, my_panel, tiers_by_slot, cons, sigma_out,
                                                             kickoff_of, now, prewaiver=prewaiver, claimed=claimed,
                                                             flags_of=lambda pid: status_of(pid).flags),
                      (None, None), page_warnings, log)  # fmt: skip
        badge_doc, tiers_doc = logs
        if "live" not in shared:  # once: every league's scores come from the same request
            live_warnings: list[str] = []
            started = [pid for pid in tracked if status_of(pid).locked]
            live_stats, stats_err = _guard("live stats", lambda: sleeper.stats(season, week), (None, None),
                                           live_warnings, log)  # fmt: skip
            if started and stats_err:  # an exception already has its own banner from _guard
                live_warnings.append(
                    f"Live scores unavailable this sync ({stats_err}); games already played show no score."
                )
            shared["live"] = (live_stats, live_results(live_stats, started, sc, status_of, now), live_warnings)
        live_stats, results, live_warnings = shared["live"]
        page_warnings.extend(live_warnings)

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
                    cards.append(_player_card(pid, tracked[pid], cons[pid], status_of(pid), t["tier"], badges.get(pid),
                                              False))  # fmt: skip
                    shown += 1
                if cards:
                    out_tiers.append({"tier": t["tier"], "anchor_mu": round(t["mu"], 2), "players": cards})
            no_projection = sum(
                1
                for pid, p in tracked.items()
                if slot_of(p) == slot and pid not in cons and pid not in rostered and status_of(pid).playable
            )
            played_ids = sorted(
                (pid for pid in cons if slot_of(tracked[pid]) == slot and pid not in rostered and status_of(pid).locked
                 and was_playable(pid)),
                key=lambda pid: (logged_tier.get(pid) or 999, -cons[pid]["mu"], pid),
            )  # fmt: skip
            played = []
            for pid in played_ids[:depth]:
                card = _player_card(pid, tracked[pid], cons[pid], status_of(pid), logged_tier.get(pid), None, False)
                card["result"] = results.get(pid)
                played.append(card)
            since_tue = (since_tuesday(slot, (tiers_doc or {}).get("prewaiver"), waiver_order[slot], cons, tracked,
                                       status_of, claimed)
                         if not (window and window.kind == "pre-waiver") else None)  # fmt: skip
            positions_out[slot] = {
                "since_tuesday": since_tue,
                "tiers": out_tiers,
                "tier_count": len(tiers_by_slot[slot]),
                "hidden": len(waiver_order[slot]) - shown,
                "no_projection": no_projection,
                "played": played,
                "played_hidden": max(0, len(played_ids) - depth),
            }
        my_out = {}
        for slot in page_slots:
            pnl = my_panel[slot]
            cards = []
            for pid in pnl["players"]:
                c = cons.get(pid)
                tier = place(c["mu"], c["sigma"], tiers_by_slot[slot], z) if c else None
                card = _player_card(
                    pid, players.get(pid, {}), c, panel_status.get(pid, status_of(pid)), tier, None, True
                )
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
            played_sugg.sort(
                key=lambda x: (-((x["player"]["result"] or {}).get("pts") or -1.0), -(x["player"]["mu"] or 0))
            )
            my_out[slot] = {
                "players": cards,
                "count": pnl["count"],
                "limit": pnl["limit"],  # K, DEF: null (no league sets one); `open_spots` is what limits the add
                **({"open_spots": pnl["open_spots"]} if slot in ROOM_SLOTS else {}),
                "starter_locked": pnl["starter_locked"],
                "suggestions": suggestions,
                "byes": bye_lookahead(pnl["players"], pnl["reserve"], players, sched, week),
                "played_suggestions": played_sugg[:6],
                "played_suggestions_hidden": max(0, len(played_sugg) - 6),
                "names": names,
            }

        if "backfilled" not in shared:  # once, after the primary page's panels (as it always ran)
            # the kicker archives after the IDP ones, the team-defense archives last: they take what budget is left
            archives = {**SOURCES, **SIGNALS, **K_SOURCES, **DEF_SOURCES}
            backfill_warnings: list[str] = []
            shared["backfilled"] = _guard("backfill", lambda: backfillmod.run(archives, cfg, data_dir, season,
                                                                             range(1, week), now, client=client,
                                                                             sleeper=sleeper, players=tracked,
                                                                             matcher=matcher, matchers=matchers,
                                                                             priors=priors, log=log, sched=sched,
                                                                             pages=pages),
                                          {}, backfill_warnings, log)  # fmt: skip
            shared["backfill_warnings"] = backfill_warnings
            for name, weeks in shared["backfilled"].items():
                log(f"sync: backfilled {warn_label(name)} weeks {weeks}")  # a note, not a page banner
        page_warnings.extend(shared["backfill_warnings"])

        recap = _guard("last week's results",
                       lambda: last_week_recap(data_dir, season, week, sched, sc, players, league_dir=lg.base,
                                               idp=any(s in IDP_SLOTS for s in page_slots)),
                       None, page_warnings, log)  # fmt: skip

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
            "swing": page_swing(page_slots, lg.key is None),  # the per-player swing settings in force, or null (§6.7)
            "sources": {
                name: footer_info(info)
                for name, info in run["sources"].items()
                if registry.group_of_source(name) in lg.groups
            },  # this page's groups only (D26)
            "warnings": page_warnings,
        }
        if (
            K in page_slots
        ):  # the kicker note's number (SPEC §6.8): how often a 1-point edge wins at a typical kicker's swing
            typical = ((run.get("swing") or {}).get("mean") or {}).get(K) or sigma_out.get(
                K
            )  # their mean, else σ_out,K
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
                registry.sources_for(group_of(slots.get(pid))),  # the sources of his own group
            ),
        )
        storage.write_json(lwdir / "consensus.json", site_data)
        if badge_doc is not None:
            storage.write_json(lwdir / "badges.json", badge_doc)
            storage.write_json(lwdir / "tiers_log.json", tiers_doc)
        storage.write_json(lwdir / "rosters.json", rosters)
        storage.write_json(
            lg.base / str(season) / "league.json",
            {
                "league_id": league["league_id"],
                "user_id": user_id,
                "name": league.get("name"),
                "status": league.get("status"),
                "roster_positions": league.get("roster_positions"),
                "settings": settings,
                "scoring_settings": lg.sc,
            },
        )

    render(LeagueView(None, league, settings, user_id, rosters, page_slots, data_dir, sc), warnings)
    backfilled = shared["backfilled"]
    live_stats = shared["live"][0]
    settles = sched.settles_at(week)
    run.update(
        settles_at=settles.isoformat() if settles else None,
        warnings=warnings,
        backfilled=backfilled,
        actuals_written=actuals,
        **({"kicker_actuals_added": actuals_k} if actuals_k else {}),  # settled weeks that gained kicker lines
        **({"defense_actuals_added": actuals_def} if actuals_def else {}),  # ... and team-defense lines (D25)
        impute_priors=(priors or {}).get("season"),  # None: built-in position rates (§6.2)
        window=window.id if window else "forced",
    )
    storage.write_json(frozen_path, {"schema_version": 1, "players": frozen})
    storage.write_json(wdir / "run.json", run)
    # the week's players as of each one's kickoff (teamless included): what later replays read (SPEC §6.5)
    slim = {pid: {k: p.get(k) for k in SLIM_FIELDS} for pid, p in tracked.items()}
    week_players = (storage.read_json(wdir / "players.json") or {}).get("players", {})
    week_players, _ = storage.merge_rows(week_players, slim, now, kickoff_of)
    storage.write_json(wdir / "players.json", {"schema_version": 1, "players": week_players})
    storage.write_json(season_dir / "schedule.json", sched.to_json())

    # each extra league (D26): its own page from the same consensus; a failure stays with that league
    extra: dict[str, dict] = {}
    for key, table in (cfg.get("leagues") or {}).items():
        if not isinstance(table, dict) or table.get("enabled") is False:
            continue
        try:
            view, why = extra_league(sleeper, cfg, key, table, data_dir, season, week, on, sc, source_rows,
                                     live_stats, tracked)  # fmt: skip
            if view is None:
                extra[key] = {"status": "skipped", "reason": why}
                log(f"sync: league {key} skipped: {why}")
                continue
            page_warnings = [t for g, t in tagged if g is None or g in view.groups]
            render(view, page_warnings)
            extra[key] = {"status": "rendered", "slots": list(view.slots), "warnings": page_warnings}
        except Exception as e:  # a failing extra league never touches the primary league's files or page
            log(traceback.format_exc())
            extra[key] = {"status": "failed", "reason": f"{type(e).__name__}: {e}"}
            log(f"sync: league {key} failed ({type(e).__name__}: {e}); its last page stays")

    results_missing = window is not None and window.kind == "results" and not isinstance(live_stats, dict)
    if window and fetched_ok and any(of(values, "idp").values()) and not results_missing:  # else the next slot retries
        gate_path = season_dir / "gate.json"
        done_list = storage.read_json(gate_path, {}).get("windows_done", [])
        storage.write_json(gate_path, {"windows_done": sorted({*done_list, window.id})})
    for w in warnings:
        log(f"warning: {w}")
    for key, info in extra.items():
        for w in info.get("warnings") or ():
            log(f"warning ({key}): {w}")
    return SyncOutcome(True, "synced", season, week, window.id if window else None, warnings, extra)
