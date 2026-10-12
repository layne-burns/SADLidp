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

from .. import storage, weeklog
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


def build_site(data_dir: Path | str = "data", site_dir: Path | str = "site") -> Path:
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
    nav = [{"season": s, "week": w, "href": page_name(s, w), "latest": (s, w) == latest} for s, w, _ in weeks]
    for season, week, wdir in weeks:
        data = json.loads((wdir / "consensus.json").read_text())
        final = None
        if (season, week) != latest:
            sc = (storage.read_json(Path(data_dir) / str(season) / "league.json") or {}).get("scoring_settings") or {}
            results, provisional = final_results(wdir, sc)
            if results is not None:
                overlay_results(data, results)
                final = {"provisional": provisional}
        data_name = f"data-{season}-{week:02d}.json"
        html = template.render(
            d=data, slots=("DL", "LB", "DB"), nav=nav, here=(season, week), latest=(season, week) == latest,
            final=final, data_href=data_name,
        )  # fmt: skip
        (site / page_name(season, week)).write_text(html)
        (site / data_name).write_text(json.dumps(data, indent=1, sort_keys=True))
        if (season, week) == latest:
            (site / "index.html").write_text(html)
            (site / "data.json").write_text(json.dumps(data, indent=1, sort_keys=True))
    (site / ".nojekyll").write_text("")
    return site / "index.html"
