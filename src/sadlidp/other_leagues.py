"""Your other Sleeper leagues whose kickers the K tab also covers (SPEC D25, owner decision 2026-11-21).

Kusch's Crooked League starts one K and scores kickers exactly as SADL does, so a kicker's number (μ, ±, swing,
range and breakdown) is the same in both leagues. It is computed once, by the SADL run, and shared. What differs
per league is who is free, who is yours, your lineup and your roster room, and so the moves. This module reads
each such league (`[kicker_leagues.<key>]`) from Sleeper's public endpoints; `sync.run_sync` then builds the
league's kicker panel with the same rules as SADL's (roster.py), and the page shows one combined kicker list,
each kicker tagged with the leagues he is free in.

Isolation: nothing here reads or writes a DL/LB/DB number or file, and a failure here never fails the sync. Every
error becomes the league's `status` and `note` (shown on the K tab); the kicker list is then SADL's alone, as it
was before. A league that scores kickers differently from SADL is not shown at all: the shared numbers would be
wrong for it.

Files, all under the league's own folder (`<key>` = the config key, e.g. `crooked`):
- `data/<season>/<key>/league.json`: id, name, status, roster positions, settings and scoring (per sync);
- `data/<season>/week<NN>/<key>/rosters.json`: each team's roster ids, players, IR and taxi (compact);
- `data/<season>/week<NN>/<key>/transactions.json`: that leg's claims and adds (league_tx.py, the FAAB line);
- `data/<season>/week<NN>/<key>/badges.json`: the league's kicker suggestions, logged like SADL's (weeklog.py).
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import league_tx, scoring, storage
from .pool import rostered_ids
from .positions import K
from .sleeper import my_roster

ROSTER_KEYS = ("roster_id", "owner_id", "co_owners", "players", "reserve", "taxi")


@dataclass
class OtherLeague:
    key: str  # the config key and data folder ("crooked")
    label: str  # the page's tag ("Crooked")
    status: str = "ok"  # ok | not_found | no_kicker | scoring_differs | no_roster | failed
    note: str | None = None
    league: dict = field(default_factory=dict)
    rosters: list = field(default_factory=list)
    me: dict | None = None
    matchups: list | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def rostered(self) -> set[str]:
        return rostered_ids(self.rosters)

    def summary(self) -> dict:
        """The run.json entry: what was read and how it went."""
        return {"label": self.label, "status": self.status, "note": self.note,
                "league_id": self.league.get("league_id"), "name": self.league.get("name")}  # fmt: skip


def configured(cfg: dict) -> dict[str, dict]:
    """`[kicker_leagues.<key>]` tables that are switched on (a replay switches them all off)."""
    return {str(key): c for key, c in (cfg.get("kicker_leagues") or {}).items()
            if isinstance(c, dict) and c.get("enabled", True)}  # fmt: skip


def kicking_scoring(sc: dict) -> dict[str, float]:
    """A league's kicking keys (every `fg…` and `xp…` key) with their values."""
    return {k: scoring.num(v) for k, v in (sc or {}).items() if str(k).startswith(("fg", "xp"))}


def same_kicker_scoring(a: dict, b: dict) -> bool:
    """Whether two leagues give a kicker the same points for every line: each kicking key either league lists
    has the same value in both, a key a league leaves out counting as 0. (SADL lists `fgm_50_59` and `fgm_60p`,
    the Crooked League `fgm_50p`; all are 0 in both, so the two score kickers alike.)"""
    ka, kb = kicking_scoring(a), kicking_scoring(b)
    return all(ka.get(k, 0.0) == kb.get(k, 0.0) for k in {*ka, *kb})


def resolve(sleeper, ocfg: dict, season: int, user_id: str, cached: dict) -> dict:
    """The league for this season: the configured `league_id`, else the id saved earlier this season, else the
    one league of yours whose name is in `names` (as SADL's own is found, SPEC §4.2). LookupError when none
    or several match."""
    lid = ocfg.get("league_id") or cached.get("league_id")
    if lid:
        return sleeper.league(str(lid))
    names = [str(n).strip() for n in ocfg.get("names") or []]
    wanted = {n.lower() for n in names}
    leagues = sleeper.leagues(user_id, season)
    matched = [lg for lg in leagues if str(lg.get("name") or "").strip().lower() in wanted]
    if len(matched) != 1:
        raise LookupError(f"{len(matched)} of your {season} leagues are named {' or '.join(names) or '(no name set)'}")
    return sleeper.league(matched[0]["league_id"])


def read(sleeper, key: str, ocfg: dict, season: int, week: int, user_id: str, sadl_scoring: dict,
         data_dir: Path | str, now: datetime, log: Callable[[str], None]) -> OtherLeague:  # fmt: skip
    """Read one other league for this sync: the league, its rosters and this week's matchups (your lineup), and
    save its transactions. Never raises: any failure is the result's `status` and `note`."""
    o = OtherLeague(key, str(ocfg.get("label") or key))
    try:
        cached = storage.read_json(Path(data_dir) / str(season) / key / "league.json") or {}
        lg = resolve(sleeper, ocfg, season, user_id, cached)
        o.league = lg or {}
        if str(o.league.get("season") or season) != str(season):
            raise LookupError(f"league {o.league.get('league_id')} is for {o.league.get('season')}, not {season}")
        if K not in (o.league.get("roster_positions") or []):
            o.status, o.note = "no_kicker", "it doesn't start a kicker"
            return o
        if not same_kicker_scoring(o.league.get("scoring_settings") or {}, sadl_scoring):
            o.status, o.note = "scoring_differs", "it scores kickers differently from SADL, so its kickers aren't shown"
            return o
        lid = o.league["league_id"]
        o.rosters = sleeper.rosters(lid) or []
        o.me = my_roster(o.rosters, user_id)
        if o.me is None:
            o.status, o.note = "no_roster", "your roster wasn't found in it"
            return o
        o.matchups, _err = sleeper.matchups(lid, week)  # optional: no lineup known without it
    except LookupError as e:
        o.status, o.note = "not_found", f"league not found ({e})"
        return o
    except Exception as e:  # isolation by design: the traceback goes to the job log only
        log(traceback.format_exc())
        o.status, o.note = "failed", f"couldn't be read this sync ({type(e).__name__})"
        return o
    try:  # the FAAB line's claims and adds; a failure here costs only that line
        league_tx.sync_legs(sleeper, o.league["league_id"], data_dir, season, week, now, sub=key)
    except Exception:
        log(traceback.format_exc())
    return o


def save(o: OtherLeague, data_dir: Path | str, season: int, week: int) -> None:
    """Write the league's own files (module docstring). Nothing is written for a league that wasn't read."""
    if not o.league.get("league_id"):
        return
    lg = o.league
    storage.write_json(Path(data_dir) / str(season) / o.key / "league.json", {
        "league_id": lg.get("league_id"), "name": lg.get("name"), "season": lg.get("season"),
        "status": lg.get("status"), "roster_positions": lg.get("roster_positions"),
        "settings": lg.get("settings") or {}, "scoring_settings": lg.get("scoring_settings") or {},
    })  # fmt: skip
    if o.rosters:
        compact = [{k: r.get(k) for k in ROSTER_KEYS} for r in o.rosters]
        storage.write_json(storage.week_dir(data_dir, season, week) / o.key / "rosters.json", compact)


def free_in(pid: str, sadl_rostered: set[str], others: list[OtherLeague], rostered: dict[str, set[str]]) -> list[str]:
    """The labels of the leagues a kicker is free in: SADL first, then each other league shown this sync
    (`rostered`: each league's rostered ids, by key)."""
    out = ["SADL"] if pid not in sadl_rostered else []
    return out + [o.label for o in others if pid not in rostered.get(o.key, set())]
