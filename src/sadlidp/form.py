"""The form term in μ (SPEC §6.4, added 2026-10-12; docs/methods-review.md step 2).

Two things the sources leave on the table, measured on Sleeper's 2025 archive (`sadlidp backtest run`):
a player's earlier misses persist (a player who has been beating his projection keeps beating it, by
about half of his shrunk average miss), and his last game's defensive snap share says who actually
plays. So each sync adds to a player's consensus μ (his `base`)

    form = β_miss[pos] · n/(n+k) · mean(actual − base over his n earlier games this season)
         + β_snap[pos] · (his share of his team's defensive snaps last game − the position's average)

`base` is the μ the page showed before each earlier game (the frozen record, §6.5), so the misses are
the sources' misses, never the form term's own. The position's average snap share is over its `TOP`
best-projected players this week, the band the coefficients were fitted on (`backtest.fit_form`). A
player with no earlier game this season gets no term; μ never drops below 0. The coefficients live in
`[form]` of the config and are recorded in run.json.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

from . import scoring, storage, weeklog
from .positions import IDP_SLOTS

TOP = 60  # the snap average is over the position's 60 best by base μ (the fit band)


def coefficients(cfg: dict) -> dict | None:
    """The `[form]` settings, or None when the term is off."""
    f = cfg.get("form") or {}
    if not f.get("enabled"):
        return None
    return {
        "k": float(f.get("k", 4)),
        "miss": {s: float((f.get("miss") or {}).get(s, 0.0)) for s in IDP_SLOTS},
        "snap": {s: float((f.get("snap") or {}).get(s, 0.0)) for s in IDP_SLOTS},
    }


def history(data_dir: Path | str, season: int, week: int, sc: dict) -> dict[str, dict]:
    """pid -> {misses: [actual − base, one per earlier game this season whose page had a μ for him],
    lsnap: his share of his team's defensive snaps in his last game, or None, last: that game's week}.
    Reads each earlier week's actuals.json and frozen.json (`base`, else `mu`, SPEC §6.5)."""
    out: dict[str, dict] = {}
    season_dir = Path(data_dir) / str(season)
    weeks = []
    for d in season_dir.glob("week*"):
        m = re.fullmatch(r"week(\d+)", d.name)
        if m and int(m.group(1)) < week and (d / "actuals.json").exists():
            weeks.append((int(m.group(1)), d))
    for w, wd in sorted(weeks):
        frozen = (storage.read_json(wd / "frozen.json") or {}).get("players") or {}
        lines = (storage.read_json(wd / "actuals.json") or {}).get("stats") or {}
        for pid, line in lines.items():
            if not isinstance(line, dict) or not weeklog.played(line):
                continue
            h = out.setdefault(pid, {"misses": [], "lsnap": None, "last": None})
            rec = frozen.get(pid) or {}
            base = rec.get("base", rec.get("mu"))
            if base is not None:
                h["misses"].append(scoring.points(line, sc) - float(base))
            team = scoring.num(line.get("tm_def_snp"))
            if team > 0:
                h["lsnap"], h["last"] = scoring.num(line.get("def_snp")) / team, w
    return out


def apply(cons: dict[str, dict], slot_of: dict[str, str | None], hist: dict[str, dict], coef: dict) -> dict:
    """Add the form term to every player's μ in place: each row keeps its consensus in `base` and gains
    `form` ({pts, miss?: {pts, games, mean, shrunk}, snap?: {pts, share, avg}}) when a term applies.
    Returns what run.json records: the position snap averages and how many players each part touched."""
    k = coef["k"]
    avg: dict[str, float | None] = {}
    for slot in IDP_SLOTS:
        top = sorted((pid for pid in cons if slot_of.get(pid) == slot), key=lambda pid: (-cons[pid]["mu"], pid))[:TOP]
        shares = [hist[pid]["lsnap"] for pid in top if pid in hist and hist[pid]["lsnap"] is not None]
        avg[slot] = statistics.fmean(shares) if shares else None
    counts = {"miss": 0, "snap": 0}
    for pid, c in cons.items():
        slot = slot_of.get(pid)
        h = hist.get(pid) or {}
        base = c["mu"]
        c["base"] = base
        detail: dict[str, dict] = {}
        misses = h.get("misses") or []
        if misses and slot and coef["miss"].get(slot):
            mean = statistics.fmean(misses)
            shrunk = len(misses) / (len(misses) + k) * mean
            detail["miss"] = {"pts": round(coef["miss"][slot] * shrunk, 3), "games": len(misses),
                              "mean": round(mean, 2), "shrunk": round(shrunk, 2)}  # fmt: skip
        share = h.get("lsnap")
        if share is not None and slot and avg.get(slot) is not None and coef["snap"].get(slot):
            detail["snap"] = {"pts": round(coef["snap"][slot] * (share - avg[slot]), 3), "share": round(share, 3),
                              "avg": round(avg[slot], 3), "week": h.get("last")}  # fmt: skip
        if not detail:
            continue
        total = max(sum(d["pts"] for d in detail.values()), -base)  # μ stays ≥ 0
        c["mu"] = base + total
        c["form"] = {"pts": round(total, 3), **detail}
        for name in detail:
            counts[name] += 1
    return {"snap_avg": {s: round(v, 3) if v is not None else None for s, v in avg.items()}, "players": counts}
