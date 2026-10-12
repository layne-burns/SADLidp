"""Render the latest week's consensus.json into a static page (index.html + data.json)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from ..schedule import ET

ACTION_TEXT = {
    "add": "Add; you're under the limit, so no drop is needed",
    "add_drop": "Add, and drop {drop}",
    "choose_drop": "Add; your other player can't play this week, so you choose the drop",
}


def latest_week_dir(data_dir: Path | str) -> Path | None:
    found = []
    for path in Path(data_dir).glob("*/week*/consensus.json"):
        season, week = path.parent.parent.name, path.parent.name
        m = re.fullmatch(r"week(\d+)", week)
        if season.isdigit() and m:
            found.append((int(season), int(m.group(1)), path.parent))
    return max(found)[2] if found else None


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


def build_site(data_dir: Path | str = "data", site_dir: Path | str = "site") -> Path:
    wdir = latest_week_dir(data_dir)
    if wdir is None:
        raise FileNotFoundError(f"no consensus.json under {data_dir}")
    data = json.loads((wdir / "consensus.json").read_text())
    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)  # names come from Sleeper
    env.filters.update(et=_et, tier=_tier_label)
    env.globals.update(action=_action)
    html = env.get_template("index.html.j2").render(d=data, slots=("DL", "LB", "DB"))
    site = Path(site_dir)
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    (site / "index.html").write_text(html)
    (site / "data.json").write_text(json.dumps(data, indent=1, sort_keys=True))
    (site / ".nojekyll").write_text("")
    return site / "index.html"
