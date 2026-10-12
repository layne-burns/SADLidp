"""Source players to Sleeper ids (SPEC.md §6.1): overrides, Sleeper's id fields, then guarded name matching.

The Sleeper id is canonical. Name steps compare normalized names (`normalize_name`) among Sleeper IDPs only,
with team codes already in Sleeper's vocabulary (the adapter converts them through its alias table first).
Every row's outcome goes into a `MatchReport` so unmatched players are listed, never silently dropped.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .positions import also_eligible, player_name, slot_of

# Outcomes in precedence order (§6.1 steps 1, 2, 4–8); a lower index wins a conflict.
METHODS = (
    "override", "source_id", "name_team_slot", "name_team", "name", "nickname", "unmatched", "no_team", "ambiguous",
)  # fmt: skip
ID_FIELDS = ("espn_id", "fantasy_data_id")  # Sleeper's own id fields, indexed up front
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
UNDECOMPOSED = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ß": "ss"})
DUPLICATE = "Duplicate Player"  # Sleeper's name for a tombstoned duplicate record
MAX_LISTED = 50


def normalize_name(name: str | None) -> str:
    """'Jevón Holland Jr.' -> 'jevon holland', 'T. J. Watt' -> 'tj watt', 'Smith-Njigba' -> 'smith njigba'."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().translate(UNDECOMPOSED)
    text = re.sub(r"[.'’‘ʼ`´]", "", text)  # periods and apostrophes vanish; any other punctuation splits
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    tokens = [t for i, t in enumerate(tokens) if i == 0 or t not in NAME_SUFFIXES]
    merged: list[str] = []  # 'd j' -> 'dj': spaced initials read like 'D.J.'
    for i, t in enumerate(tokens):
        if len(t) == 1 and i and len(tokens[i - 1]) == 1:
            merged[-1] += t
        else:
            merged.append(t)
    return " ".join(merged)


@dataclass(frozen=True)
class Match:
    pid: str | None
    how: str  # one of METHODS

    @property
    def rank(self) -> int:
        """Precedence of the step that produced this match (lower wins a conflict, §6.1)."""
        return METHODS.index(self.how)


def _team(code: str | None) -> str | None:
    return (str(code).strip().upper() or None) if code else None


def _symmetric(table: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """{'chris': ['christopher', 'christian']} -> chris ↔ each full name (not christopher ↔ christian)."""
    out: dict[str, set[str]] = {}
    for short, fulls in (table or {}).items():
        short = normalize_name(short)
        for full in {normalize_name(f) for f in (fulls if isinstance(fulls, list) else [fulls])} - {"", short}:
            if short:
                out.setdefault(short, set()).add(full)
                out.setdefault(full, set()).add(short)
    return {n: sorted(alts) for n, alts in out.items()}


class Matcher:
    """Indexes the Sleeper players DB once; `match` is then a few dict lookups per source row."""

    def __init__(
        self,
        players: dict[str, dict],
        overrides: dict[str, dict[str, str]] | None = None,
        nicknames: dict[str, list[str]] | None = None,
        crosswalk: dict[str, dict[str, str]] | None = None,
    ):
        self.players = players
        self.crosswalk = crosswalk or {}  # id_field -> {source id: Sleeper id} (crosswalk.load, §6.1 step 3)
        self.nicknames = _symmetric(nicknames)
        self._idp: set[str] = set()
        self._by_name: dict[str, list[str]] = {}
        for pid, p in players.items():
            if slot_of(p) is None or p.get("full_name") == DUPLICATE:
                continue
            self._idp.add(pid)
            first_last = f"{p.get('first_name') or ''} {p.get('last_name') or ''}"
            for key in {normalize_name(player_name(p)), normalize_name(first_last)} - {""}:
                self._by_name.setdefault(key, []).append(pid)
        self._by_id: dict[str, dict[str, list[str]]] = {}
        for field in ID_FIELDS:
            self._id_index(field)
        self._overrides_id: dict[str, dict[str, str]] = {}
        self._overrides_name: dict[str, dict[tuple[str, str | None], str]] = {}
        for source, table in (overrides or {}).items():
            for key, pid in table.items():
                if "|" in key:
                    name, _, team = key.rpartition("|")
                    self._overrides_name.setdefault(source, {})[(normalize_name(name), _team(team))] = str(pid)
                else:
                    self._overrides_id.setdefault(source, {})[key.strip()] = str(pid)

    def _id_index(self, field: str) -> dict[str, list[str]]:
        """Source id -> Sleeper IDP ids carrying it in `field` (built once per field)."""
        if field not in self._by_id:
            index: dict[str, list[str]] = {}
            for pid in self._idp:
                value = self.players[pid].get(field)
                if value not in (None, ""):
                    index.setdefault(str(value), []).append(pid)
            self._by_id[field] = index
        return self._by_id[field]

    def _crosswalk(self, id_field: str, source_id: str) -> str | None:
        """§6.1 step 3: the crosswalk's Sleeper id for `source_id`, only if it is an IDP whose own record lacks
        `id_field` (where Sleeper carries the field, step 2 is authoritative)."""
        pid = self.crosswalk.get(id_field, {}).get(source_id)
        if pid is None or pid not in self._idp or self.players[pid].get(id_field) not in (None, ""):
            return None
        return pid

    def _eligible(self, pid: str, slot: str | None) -> bool:
        p = self.players[pid]
        return slot is None or slot == slot_of(p) or slot in also_eligible(p)

    def _by_names(self, names: list[str], team: str | None, slot: str | None) -> tuple[str | None, int]:
        """Steps 4–6 over the given name keys: (pid, step) on a single candidate, (None, n) otherwise."""
        cands = list(dict.fromkeys(pid for n in names for pid in self._by_name.get(n, [])))
        if not cands:
            return None, 0
        on_team = [pid for pid in cands if _team(self.players[pid].get("team")) == team]
        if slot is not None:
            exact = [pid for pid in on_team if self._eligible(pid, slot)]
            if len(exact) == 1:
                return exact[0], 4
            if len(exact) > 1:
                return None, len(exact)
        if len(on_team) == 1:
            return on_team[0], 5
        if len(on_team) > 1:
            return None, len(on_team)
        if len(cands) == 1 and self._eligible(cands[0], slot):
            return cands[0], 6
        return None, len(cands) if len(cands) > 1 else 0

    def match(
        self,
        source: str,
        *,
        source_id: str | None = None,
        id_field: str | None = None,
        name: str | None = None,
        team: str | None = None,
        slot: str | None = None,
    ) -> Match:
        """One source row -> a Sleeper id, by the first §6.1 step that gives exactly one IDP."""
        sid = str(source_id).strip() if source_id not in (None, "") else None
        team = _team(team)
        norm = normalize_name(name)
        if sid is not None and sid in self._overrides_id.get(source, {}):
            return Match(self._overrides_id[source][sid], "override")
        if norm and (norm, team) in self._overrides_name.get(source, {}):
            return Match(self._overrides_name[source][(norm, team)], "override")
        if sid is not None and id_field:
            hits = self._id_index(id_field).get(sid, [])  # IDPs only: an id landing on a non-IDP is ignored
            if len(hits) == 1:
                return Match(hits[0], "source_id")
        if sid is not None and id_field and (pid := self._crosswalk(id_field, sid)) is not None:
            return Match(pid, "source_id")
        if team is None:
            return Match(None, "no_team")
        if not norm:
            return Match(None, "unmatched")
        pid, step = self._by_names([norm], team, slot)
        if pid is not None:
            return Match(pid, {4: "name_team_slot", 5: "name_team", 6: "name"}[step])
        if step > 1:
            return Match(None, "ambiguous")
        first, _, rest = norm.partition(" ")
        variants = [f"{alt} {rest}".strip() for alt in self.nicknames.get(first, [])]
        pid, step = self._by_names(variants, team, slot) if variants else (None, 0)
        if pid is not None:
            return Match(pid, "nickname")
        return Match(None, "ambiguous" if step > 1 else "unmatched")


class MatchReport:
    """Per-fetch match counts by method, the players left unmatched, and conflicts (§5.1 `report`, §6.1)."""

    def __init__(self):
        self.by_method: Counter[str] = Counter()
        self.unmatched: list[dict] = []
        self.conflicts: list[dict] = []
        self.matched = 0
        self.teamed = 0
        self.teamed_matched = 0

    def add(self, m: Match, *, name: str | None, team: str | None, slot: str | None) -> None:
        self.by_method[m.how] += 1
        self.matched += m.pid is not None
        if _team(team):
            self.teamed += 1
            self.teamed_matched += m.pid is not None
        if m.pid is None and m.how != "no_team":  # no-team rows are only counted: they have no game to project
            self.unmatched.append({"name": name, "team": team, "slot": slot, "how": m.how})

    def conflict(self, pid: str, kept: str, dropped: str) -> None:
        """Two source rows matched one Sleeper player: `kept` won on precedence, `dropped` is reported."""
        self.conflicts.append({"pid": pid, "kept": kept, "dropped": dropped})

    def summary(self) -> dict:
        listed = sorted(self.unmatched, key=lambda r: tuple(str(r[k] or "") for k in ("name", "team", "slot")))
        return {
            "by_method": {how: self.by_method[how] for how in METHODS if self.by_method[how]},
            "unmatched": listed[:MAX_LISTED],
            "conflicts": self.conflicts[:MAX_LISTED],
            "matched": self.matched,
            "teamed": self.teamed,
            "teamed_match_rate": self.teamed_matched / self.teamed if self.teamed else None,
        }


def load_overrides(path: Path | str) -> dict[str, dict[str, str]]:
    """config/player_overrides.toml: `[<source>]` tables of "<source id>" or "<Name>|<TEAM>" = "<sleeper id>"."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {src: {str(k): str(v) for k, v in t.items()} for src, t in data.items() if isinstance(t, dict)}


def load_nicknames(path: Path | str) -> dict[str, list[str]]:
    """config/aliases/nicknames.json ({"cam": ["cameron"], ...}) as a symmetric lookup; missing file -> {}."""
    path = Path(path)
    return _symmetric(json.loads(path.read_text())) if path.exists() else {}
