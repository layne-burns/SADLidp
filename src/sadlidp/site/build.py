"""Render every synced week into static pages (SPEC §2, D22).

index.html is the latest week; week-<season>-<NN>.html is every week with a consensus.json, all
linked by a week picker. A past week is re-scored with its final stats (actuals.json), so its page
shows every listed player's final score and every suggestion's outcome. Each page has its JSON copy.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from .. import league_tx, stats, storage, weeklog
from ..positions import IDP_SLOTS, SLOTS, K
from ..schedule import ET, Schedule

ACTION_TEXT = {
    "add": "Add; you're under the limit, so no drop is needed",
    "add_drop": "Add, and drop {drop}",
    "swap": "Add, and drop {drop}",  # a kicker for your starting kicker: a straight swap (roster.kicker_action)
    "choose_drop": "Add; your other player can't play this week, so you choose the drop",
}
# A kicker add depends on roster room, not on a position limit (the league sets none for K, SPEC §6.7).
K_ACTION_TEXT = {
    "add": "Add; you have a free roster spot, so no drop is needed",
    "add_unknown": "Add; no drop is needed while you have a free roster spot",
    "choose_drop": "Add; your roster is full, so you choose the drop",
}


def week_dirs(data_dir: Path | str) -> list[tuple[int, int, Path]]:
    """(season, week, dir) for every week with a consensus.json, oldest first."""
    found = []
    for path in Path(data_dir).glob("*/week*/consensus.json"):
        season, week = path.parent.parent.name, path.parent.name
        m = re.fullmatch(r"week(\d+)", week)
        if season.isdigit() and m:
            found.append((int(season), int(m.group(1)), path.parent))
    return sorted(found)


def latest_week_dir(data_dir: Path | str) -> Path | None:
    weeks = week_dirs(data_dir)
    return weeks[-1][2] if weeks else None


def page_name(season: int, week: int) -> str:
    return f"week-{season}-{week:02d}.html"


def final_results(wdir: Path, scoring_settings: dict) -> tuple[dict | None, bool]:
    """Every player's final SADL points from the week's actuals, and whether they're still provisional."""
    act = storage.read_json(wdir / "actuals.json")
    if not act or not scoring_settings:
        return None, False
    stats = act.get("stats") or {}
    results = {pid: weeklog.stat_result(line, scoring_settings, final=True) for pid, line in stats.items()}
    sched = storage.read_json(wdir.parent / "schedule.json")
    settles = Schedule.from_json(sched).settles_at(int(wdir.name[4:])) if sched else None
    if settles is None:  # no schedule saved: the week's run.json says when it settles (newer runs only)
        stamp = (storage.read_json(wdir / "run.json") or {}).get("settles_at")
        settles = datetime.fromisoformat(stamp) if stamp else None
    captured = act.get("captured_at")
    provisional = settles is None or captured is None or datetime.fromisoformat(captured) < settles
    return results, provisional


def overlay_results(data: dict, results: dict) -> None:
    """Put final scores on every card of a past week's page; recompute suggestion outcomes."""

    def res(pid):
        return results.get(pid) or {"pts": None, "state": "dnp"}

    def walk(obj):
        if isinstance(obj, dict):
            if "id" in obj and "mu" in obj and "slot" in obj:
                obj["result"] = res(obj["id"])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data.get("positions"))
    for m in (data.get("my_idps") or {}).values():
        walk(m.get("players"))
        for s in [*(m.get("suggestions") or []), *(m.get("played_suggestions") or [])]:
            walk(s.get("player"))
            over = s.get("over")
            s["over_result"] = res(over) if over else None
            s["outcome"] = weeklog.outcome(s["player"]["result"], over, s["over_result"])


def report_card(data: dict, view: dict | None, week: int, provisional: bool, season: dict | None = None) -> dict:
    """The week's report card (SPEC §6.8): your rostered IDPs projected vs scored, with the position's median
    miss that week for comparison; the suggestions that paid off, counted as decisions (from the Stats page's
    view of the week: the decision log, or the rebuilt page), and the season so far; and the biggest beat,
    shortfall, best and worst call. `data` is the week's page with its final scores overlaid. Every total, beat,
    shortfall and call is DL/LB/DB only; your rostered kickers are listed apart in `kickers`, never summed in."""
    mine, kickers = [], []
    for slot, m in (data.get("my_idps") or {}).items():
        for p in m.get("players") or []:
            if p.get("reserve") or p.get("taxi"):
                continue
            r = p.get("result") or {}
            pts = r.get("pts") if r.get("state") == "final" else None
            row = {"name": p.get("name"), "slot": slot, "mu": p.get("mu"), "pts": pts, "state": r.get("state"),
                   "diff": pts - p["mu"] if pts is not None and p.get("mu") is not None else None}  # fmt: skip
            (kickers if slot == K else mine).append(row)
    mine.sort(key=lambda x: ("DL LB DB".split().index(x["slot"]) if x["slot"] in ("DL", "LB", "DB") else 3,
                             -(x["pts"] or -1.0)))  # fmt: skip
    kickers.sort(key=lambda x: -(x["pts"] or -1.0))
    both = [x for x in mine if x["diff"] is not None]
    sugg = (view or {}).get("suggestions") or {}
    calls = [e for e in sugg.get("list") or []  # the badge that stands for each decision (§6.8)
             if e.get("counted", True) and not e.get("reference") and e.get("outcome") in ("hit", "miss")
             and e.get("slot") != K]  # fmt: skip

    def margin(e):
        return (e.get("pts") or 0.0) - (e.get("over_pts") or 0.0)

    hits = [e for e in calls if e["outcome"] == "hit"]
    misses = [e for e in calls if e["outcome"] == "miss"]
    tally = [sugg.get(k) or {} for k in ("live", "rebuilt")]
    so_far = (season or {}).get("suggestions") or {}  # decisions of the weeks up to this one, live and rebuilt
    beat = max(both, key=lambda x: x["diff"], default=None)
    short = min(both, key=lambda x: x["diff"], default=None)
    return {
        "week": week,
        "provisional": provisional,
        "players": mine,
        "kickers": kickers,
        "projected": sum(x["mu"] for x in both),
        "scored": sum(x["pts"] for x in both),
        "counted": len(both),
        "hits": sum(t.get("hits", 0) for t in tally),
        "decided": sum(t.get("decided", 0) for t in tally),
        "badges": sum((t.get("badges") or {}).get("decided", 0) for t in tally),
        "rebuilt": bool((sugg.get("rebuilt") or {}).get("decided")),
        "so_far": {
            k: {x: (so_far.get(k) or {}).get(x) for x in ("hits", "decided", "net")}
            for k in ("live", "rebuilt")
            if (so_far.get(k) or {}).get("decided")
        },  # fmt: skip
        "median_miss": {slot: x.get("median_miss") for slot, x in ((view or {}).get("by_slot") or {}).items()},
        "beat": beat if beat and beat["diff"] > 0 else None,
        "short": short if short and short["diff"] < 0 else None,
        "best_call": max(hits, key=margin, default=None),
        "worst_call": max(misses, key=lambda e: -margin(e), default=None),
    }


def _et(iso: str | None, fmt: str = "%a %b %-d, %-I:%M %p ET") -> str:
    """An ISO time in ET. `%-d`, `%-I` (no leading zero) are glibc-only, so they are filled in here: the same
    text on every platform."""
    if not iso:
        return "—"
    t = datetime.fromisoformat(iso).astimezone(ET)
    return t.strftime(re.sub(r"%-([a-zA-Z])", lambda m: t.strftime("%" + m.group(1)).lstrip("0") or "0", fmt))


def _tier_label(t, last: int = 0) -> str:
    if t is None:
        return "no projection"
    if t == "above":
        return "above Tier 1"
    if t == "below":
        return f"below Tier {last}" if last else "no waiver tiers this week"
    return f"Tier {t}"


def _action(badge: dict, names: dict, slot: str | None = None, open_spots=None) -> str:
    """What to do about a suggestion. A kicker's add and free-choice drop are worded by roster room
    (`open_spots`: your free roster spots, None when unknown); every IDP text is as it always was."""
    action = badge.get("action")
    text = ACTION_TEXT.get(action, "")
    if slot == K:
        if action == "add" and not (isinstance(open_spots, int) and open_spots > 0):
            action = "add_unknown"
        text = K_ACTION_TEXT.get(action, text)
    return text.format(drop=names.get(badge.get("drop"), "your weaker player"))


def _why(badge: dict, names: dict, slot: str) -> str:
    """Why a suggestion can't be acted on this week (D15 amended)."""
    reasons = []
    if "starter_locked" in (badge.get("why") or []):
        reasons.append(
            f"your {slot} starter's game has started, so nothing added at {slot} can score for you this week"
        )
    if "drop_locked" in (badge.get("why") or []):
        if badge.get("drop"):
            reasons.append(
                f"{names.get(badge['drop'], 'that player')}'s game has started, so the drop can't be made this week"
            )
        else:
            reasons.append(f"every {slot} you could drop has already kicked off, so the drop can't be made this week")
    return "; ".join(reasons)


def page_slots(data: dict) -> tuple[str, ...]:
    """The positions a page shows: the ones its own data holds, in page order. A week synced before kickers
    were tracked (or replayed without a kicker source) has no K, so its page has three tabs and no K text."""
    return tuple(s for s in SLOTS if s in (data.get("positions") or {}) and s in (data.get("my_idps") or {}))


def this_week(data: dict, slots: tuple[str, ...]) -> dict:
    """The page's opening card (SPEC §2): what the position panels already say, gathered across positions.
    `moves`: every suggestion with its slot, the ones you can act on first; `quiet`: the positions with none;
    `heads`: one line per existing flag worth a look (a player of yours who can't play or is questionable, a
    position you can't fill, a starter whose game has started, a bye ahead); `starters`: each position's
    projected starter. Nothing here is computed anew: no number, no odds, no rule."""
    moves, quiet, heads, locked, byes, starters = [], [], [], [], [], []
    for slot in slots:
        m = (data.get("my_idps") or {}).get(slot) or {}
        sugg = m.get("suggestions") or []
        moves += [{"slot": slot, "m": m, "s": s} for s in sugg]
        if not sugg:
            quiet.append(slot)
        players = m.get("players") or []
        for p in players:
            reasons = p.get("unavailable") or []
            if p.get("reserve") or p.get("taxi") or "locked" in reasons:
                continue  # stashed, or already playing: nothing to decide
            who = f"{p.get('name')} ({slot})"
            if reasons == ["bye"]:
                heads.append(f"{who} is on bye.")
            elif reasons:
                heads.append(f"{who} can't play this week: {', '.join(reasons)}.")
            for flag in p.get("flags") or []:
                heads.append(f"{who} is questionable." if flag == "Q" else f"{who}: {flag}.")
        starter = next((p for p in players if p.get("role") == "starter"), None)
        starters.append({"slot": slot, "p": starter})
        if not players:
            heads.append(f"No {slot} rostered.")
        elif starter is None and all(s.get("over") for s in sugg):  # else the move's own line says so
            heads.append(f"You have no playable {slot} this week.")
        if m.get("starter_locked"):
            locked.append(slot)
        byes += [f"Bye ahead: {b['name']} ({b['team']}) has a bye in week {b['week']}." for b in m.get("byes") or []]
    if len(locked) == 1:
        heads.append(f"Your {locked[0]} starter's game has started, so suggestions there are for reference.")
    elif locked:
        names = f"{', '.join(locked[:-1])} and {locked[-1]}"
        heads.append(f"Your {names} starters' games have started, so suggestions there are for reference.")
    moves.sort(key=lambda x: bool(x["s"].get("reference")))  # stable: page order within each kind
    return {"moves": moves, "quiet": quiet, "heads": heads + byes, "starters": starters}


def source_groups(sources: dict | None) -> list[dict]:
    """The Sources table in its groups (a kicker source carries `"group": "k"`; anything else is an IDP source),
    each with how many of its sources have rows on this page (`used`) and the ones that don't (`out`)."""
    groups = []
    for key, title in (("idp", "IDP sources"), ("k", "Kicker sources")):
        rows = [(name, s or {}) for name, s in (sources or {}).items() if (s or {}).get("group", "idp") == key]
        if rows:
            out = [(name, s) for name, s in rows if not s.get("rows_used") or s.get("stale")]
            groups.append({"key": key, "title": title, "rows": rows, "used": len(rows) - len(out), "out": out})
    return groups


K_NOTE_WEEKS = 4  # settled weeks behind the kicker note's measured figure before the page prints it (§6.8)


def kicker_note(ev: dict | None) -> dict | None:
    """The kicker note's measured figure from the Stats evaluation (`k_note`), or None until it rests on
    `K_NOTE_WEEKS` settled weeks whose kicker numbers the page showed before the games (`live`). Weeks graded
    from the sources' archives alone never count here: the page showed no kicker order for them, and those
    archives are unchecked (SPEC §6.5); the Stats page shows them, labelled."""
    note = (ev or {}).get("k_note") or {}
    behind = min(note.get("weeks") or 0, note.get("live") or 0)
    return note if note.get("r") is not None and behind >= K_NOTE_WEEKS else None


def build_site(data_dir: Path | str = "data", site_dir: Path | str = "site", cfg: dict | None = None) -> Path:
    weeks = week_dirs(data_dir)
    if not weeks:
        raise FileNotFoundError(f"no consensus.json under {data_dir}")
    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)  # names come from Sleeper
    env.filters.update(et=_et, tier=_tier_label)
    env.globals.update(action=_action, why=_why)
    template = env.get_template("index.html.j2")
    site = Path(site_dir)
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    latest = weeks[-1][:2]
    many = len({s for s, _, _ in weeks}) > 1  # name the season once pages span more than one
    nav = [
        {"season": s, "week": w, "href": page_name(s, w), "latest": (s, w) == latest,
         "label": f"{s} · Week {w}" if many else f"Week {w}"}
        for s, w, _ in weeks
    ]  # fmt: skip
    ev = stats.evaluate(data_dir, latest[0], cfg or {})
    views = {v["key"]: v for v in (ev or {}).get("views") or []}
    dbnote = (ev or {}).get("db_note")
    knote = kicker_note(ev)  # the kicker note's measured figure, once 4 live kicker weeks are settled (§6.8)
    tueq = (ev or {}).get("tuesday_q")
    settings = (storage.read_json(Path(data_dir) / str(latest[0]) / "league.json") or {}).get("settings") or {}
    # the FAAB line counts IDP claims only, as it always has (SPEC §6.8): a kicker claim is not an IDP waiver
    idp_ids = {pid: s for pid, s in stats._positions(Path(data_dir) / str(latest[0])).items() if s in IDP_SLOTS}
    faab = league_tx.faab_summary(data_dir, latest[0], idp_ids, settings)
    to_date = {w: {"suggestions": t} for w, t in ((ev or {}).get("decisions_to_date") or {}).items()}

    def scored_page(season: int, wdir: Path) -> tuple[dict, dict | None]:
        data = json.loads((wdir / "consensus.json").read_text(encoding="utf-8"))
        sc = (storage.read_json(Path(data_dir) / str(season) / "league.json") or {}).get("scoring_settings") or {}
        results, provisional = final_results(wdir, sc)
        if results is None:
            return data, None
        overlay_results(data, results)
        return data, {"provisional": provisional}

    by_week = {(s, w): d for s, w, d in weeks}
    for season, week, wdir in weeks:
        final, card = None, None
        if (season, week) != latest:
            data, final = scored_page(season, wdir)
            if final and season == latest[0]:
                card = report_card(data, views.get(f"w{week}"), week, final["provisional"], to_date.get(week))
        else:
            data = json.loads((wdir / "consensus.json").read_text(encoding="utf-8"))
            prev = by_week.get((season, week - 1))
            if prev is not None:  # the newest week with stats: last week's card (§6.8)
                prev_data, prev_final = scored_page(season, prev)
                if prev_final:
                    card = report_card(prev_data, views.get(f"w{week - 1}"), week - 1, prev_final["provisional"],
                                       to_date.get(week - 1))  # fmt: skip
        data_name = f"data-{season}-{week:02d}.json"
        slots = page_slots(data)  # this page's own positions: no K on a week synced before kickers
        at = [(n["season"], n["week"]) for n in nav].index((season, week))
        html = template.render(
            d=data, slots=slots, idp_slots=tuple(s for s in slots if s in IDP_SLOTS), all_slots=SLOTS,
            now=this_week(data, slots), nav=nav, here=(season, week), latest=(season, week) == latest,
            nav_prev=nav[at - 1] if at else None, nav_next=nav[at + 1] if at + 1 < len(nav) else None,
            final=final, data_href=data_name, card=card, dbnote=dbnote, knote=knote, tueq=tueq, faab=faab,
            source_groups=source_groups(data.get("sources")),
        )  # fmt: skip
        (site / page_name(season, week)).write_text(html, encoding="utf-8")
        (site / data_name).write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        if (season, week) == latest:
            (site / "index.html").write_text(html, encoding="utf-8")
            (site / "data.json").write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    last = json.loads((weeks[-1][2] / "consensus.json").read_text(encoding="utf-8"))
    league_name = (last.get("league") or {}).get("name")
    (site / "stats.html").write_text(
        env.get_template("stats.html.j2").render(s=ev, slots=("DL", "LB", "DB"), league_name=league_name),
        encoding="utf-8",
    )
    public = {k: v for k, v in (ev or {}).items() if not k.startswith("_")} if ev else None
    (site / "stats.json").write_text(json.dumps(public, indent=1, sort_keys=True, default=list), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return site / "index.html"
