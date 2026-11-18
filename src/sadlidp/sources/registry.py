"""The source registry (SPEC.md §5): every source, the group it belongs to, its kind and its page label.

Two groups, kept apart end to end (`positions.GROUPS`): the five IDP sources and the six kicker sources. A
source is fetched with its own group's players and matcher, blended and weighted with its own group's sources
only, and measured against its own group's results. `SOURCES` (the IDP sources) keeps its name and shape
(name -> (module, kind)); `K_SOURCES` has the same shape.

Every source is DECLARED below, so names, kinds and labels are known everywhere. A kicker source is REGISTERED
(fetched, blended, shown) once its module exists and its name is listed in `K_MODULES`.

    To register a kicker source: write `sources/<name>.py` (with `NAME = "<name>"`, `fetch(ctx)`, and `HOST` or
    `URL` for the backfill runner's per-host budget) and uncomment its line in `K_MODULES`. Nothing else.

A kicker adapter gets `ctx.players` = the kickers only, `ctx.matcher` = a matcher over kickers only,
`ctx.imputer` = None (never impute a kicker line), and `ctx.cfg` = its `[sources.<name>]` table plus
`ctx.cfg["kicker"]` = the `[kicker]` table (`yards_per_make`). Lists and universes are keyed "K".
"""

from __future__ import annotations

from importlib import import_module

from . import espn_proj, fantasydata, fp_dst, fp_ecr, rotoballer, sleeper_proj

# name -> (group, kind, page label), in page order within each group. The footer groups the two sets, so a
# kicker source carries the plain site name.
DECLARED: dict[str, tuple[str, str, str]] = {
    "sleeper_proj": ("idp", "projection", "Sleeper"),
    "espn_proj": ("idp", "projection", "ESPN"),
    "fp_ecr": ("idp", "rank", "FantasyPros"),
    "rotoballer": ("idp", "rank", "RotoBaller"),
    "fantasydata": ("idp", "rank", "FantasyData"),
    "sleeper_k": ("k", "projection", "Sleeper"),
    "espn_k": ("k", "projection", "ESPN"),
    "cbs_k": ("k", "projection", "CBS"),
    "fp_k": ("k", "rank", "FantasyPros"),
    "fantasydata_k": ("k", "rank", "FantasyData"),
    "rotoballer_k": ("k", "rank", "RotoBaller"),
}
SIGNAL_LABELS = {"fp_dst": "FantasyPros D/ST"}

K_MODULES = (
    "sleeper_k",
    "espn_k",
    "cbs_k",
    "fp_k",
    "fantasydata_k",
    "rotoballer_k",
)  # one line per kicker module that exists


def _load(name: str):
    module = import_module(f"{__package__}.{name}")
    if getattr(module, "NAME", None) != name or not callable(getattr(module, "fetch", None)):
        raise ImportError(f"sources/{name}.py must define NAME = {name!r} and fetch(ctx)")
    if name not in DECLARED:
        raise ImportError(f"{name} is not declared in sources/registry.py")
    return module


SOURCES = {
    sleeper_proj.NAME: (sleeper_proj, "projection"),
    espn_proj.NAME: (espn_proj, "projection"),
    fp_ecr.NAME: (fp_ecr, "rank"),
    rotoballer.NAME: (rotoballer, "rank"),
    fantasydata.NAME: (fantasydata, "rank"),
}
K_SOURCES = {name: (_load(name), DECLARED[name][1]) for name in DECLARED if name in K_MODULES}
SIGNALS = {fp_dst.NAME: (fp_dst, "signal")}  # stored and backfilled, never blended (D18)
BY_GROUP = {"idp": SOURCES, "k": K_SOURCES}


def sources_for(group: str) -> dict[str, tuple]:
    """The registered sources of a group, as name -> (module, kind)."""
    return BY_GROUP[group]


def group_of_source(name: str) -> str:
    """ "k" for a declared kicker source; "idp" for everything else (an unknown name is an IDP source, as
    every source was before kickers)."""
    return DECLARED.get(name, ("idp",))[0]


def names_for(group: str) -> tuple[str, ...]:
    """Every declared source name of a group, registered or not (for telling the groups' files and config
    entries apart)."""
    return tuple(name for name, (g, _kind, _label) in DECLARED.items() if g == group)


def of_group(by_source: dict, group: str) -> dict:
    """The entries of a per-source dict that belong to one group, in their order."""
    return {name: v for name, v in by_source.items() if group_of_source(name) == group}


def projection_count(group: str) -> int:
    """How many projection sources a group declares (the two-source "ls" rule needs exactly two, §6.5)."""
    return sum(1 for g, kind, _label in DECLARED.values() if g == group and kind == "projection")


def labels() -> dict[str, str]:
    """name -> page label: the IDP sources, the D/ST signal, then the kicker sources (the order other modules
    sort source files by; the IDP part is what it always was)."""
    idp = {name: label for name, (g, _kind, label) in DECLARED.items() if g == "idp"}
    kick = {name: label for name, (g, _kind, label) in DECLARED.items() if g == "k"}
    return {**idp, **SIGNAL_LABELS, **kick}
