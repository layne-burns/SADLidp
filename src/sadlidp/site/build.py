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

from .. import stats, storage, weeklog
from ..schedule import ET, Schedule

ACTION_TEXT = {
    "add": "Add; you're under the limit, so no drop is needed",
    "add_drop": "Add, and drop {drop}",
    "choose_drop": "Add; your other player can't play this week, so you choose the drop",
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


def report_card(data: dict, view: dict | None, week: int, provisional: bool) -> dict:
    """The week's report card (SPEC §6.8): your rostered IDPs projected vs scored, the suggestions that paid
    off (from the Stats page's view of the week: the decision log, or the rebuilt page), and the biggest
    beat, shortfall, best and worst call. `data` is the week's page with its final scores overlaid."""
    mine = []
    for slot, m in (data.get("my_idps") or {}).items():
        for p in m.get("players") or []:
            if p.get("reserve") or p.get("taxi"):
                continue
            r = p.get("result") or {}
            pts = r.get("pts") if r.get("state") == "final" else None
            mine.append({"name": p.get("name"), "slot": slot, "mu": p.get("mu"), "pts": pts, "state": r.get("state"),
                         "diff": pts - p["mu"] if pts is not None and p.get("mu") is not None else None})  # fmt: skip
    mine.sort(key=lambda x: ("DL LB DB".split().index(x["slot"]) if x["slot"] in ("DL", "LB", "DB") else 3,
                             -(x["pts"] or -1.0)))  # fmt: skip
    both = [x for x in mine if x["diff"] is not None]
    sugg = (view or {}).get("suggestions") or {}
    calls = [e for e in sugg.get("list") or [] if not e.get("reference") and e.get("outcome") in ("hit", "miss")]

    def margin(e):
        return (e.get("pts") or 0.0) - (e.get("over_pts") or 0.0)

    hits = [e for e in calls if e["outcome"] == "hit"]
    misses = [e for e in calls if e["outcome"] == "miss"]
    tally = [sugg.get(k) or {} for k in ("live", "rebuilt")]
    beat = max(both, key=lambda x: x["diff"], default=None)
    short = min(both, key=lambda x: x["diff"], default=None)
    return {
        "week": week,
        "provisional": provisional,
        "players": mine,
        "projected": sum(x["mu"] for x in both),
        "scored": sum(x["pts"] for x in both),
        "counted": len(both),
        "hits": sum(t.get("hits", 0) for t in tally),
        "decided": sum(t.get("decided", 0) for t in tally),
        "rebuilt": bool((sugg.get("rebuilt") or {}).get("decided")),
        "beat": beat if beat and beat["diff"] > 0 else None,
        "short": short if short and short["diff"] < 0 else None,
        "best_call": max(hits, key=margin, default=None),
        "worst_call": max(misses, key=lambda e: -margin(e), default=None),
    }


def _et(iso: str | None, fmt: str = "%a %b %-d, %-I:%M %p ET") -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(ET).strftime(fmt)


def _tier_label(t, last: int = 0) -> str:
    if t is None:
        return "no projection"
    if t == "above":
        return "above Tier 1"
    if t == "below":
        return f"below Tier {last}" if last else "no waiver tiers this week"
    return f"Tier {t}"


def _action(badge: dict, names: dict) -> str:
    return ACTION_TEXT.get(badge.get("action"), "").format(drop=names.get(badge.get("drop"), "your weaker player"))


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
    dbr = ((ev or {}).get("season_by_slot") or {}).get("DB", {}).get("spearman")

    def scored_page(season: int, wdir: Path) -> tuple[dict, dict | None]:
        data = json.loads((wdir / "consensus.json").read_text())
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
                card = report_card(data, views.get(f"w{week}"), week, final["provisional"])
        else:
            data = json.loads((wdir / "consensus.json").read_text())
            prev = by_week.get((season, week - 1))
            if prev is not None:  # the newest week with stats: last week's card (§6.8)
                prev_data, prev_final = scored_page(season, prev)
                if prev_final:
                    card = report_card(prev_data, views.get(f"w{week - 1}"), week - 1, prev_final["provisional"])
        data_name = f"data-{season}-{week:02d}.json"
        html = template.render(
            d=data, slots=("DL", "LB", "DB"), nav=nav, here=(season, week), latest=(season, week) == latest,
            final=final, data_href=data_name, card=card, dbr=dbr,
        )  # fmt: skip
        (site / page_name(season, week)).write_text(html)
        (site / data_name).write_text(json.dumps(data, indent=1, sort_keys=True))
        if (season, week) == latest:
            (site / "index.html").write_text(html)
            (site / "data.json").write_text(json.dumps(data, indent=1, sort_keys=True))
    league_name = (json.loads((weeks[-1][2] / "consensus.json").read_text()).get("league") or {}).get("name")
    (site / "stats.html").write_text(
        env.get_template("stats.html.j2").render(s=ev, slots=("DL", "LB", "DB"), league_name=league_name)
    )
    public = {k: v for k, v in (ev or {}).items() if not k.startswith("_")} if ev else None
    (site / "stats.json").write_text(json.dumps(public, indent=1, sort_keys=True, default=list))
    (site / ".nojekyll").write_text("")
    return site / "index.html"
