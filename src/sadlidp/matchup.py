"""DL matchup signal, shadow-logged only (SPEC §6.4 "DL matchup (shadow)", owner decision 2026-11-17).

A defensive lineman's points depend on the offense he faces. The signal is the opponent offense's QB-hit rate
allowed per dropback, season to date. It comes from nflverse play-by-play: regular-season plays with
`qb_dropback == 1`, spikes left out, and `qb_hit` counted.

For week w and offense o, using weeks before w:

    rate = (hits_o + P·L) / (dropbacks_o + P),    o_hit = 100·(rate − L)  (percentage points)

L is the league rate over the same weeks and P is 4 average team-games of dropbacks (shrinkage). The
shadow term is `[matchup] DL.hit` × o_hit, clipped to ±`clip` points. Its coefficient is 0.30, frozen at
about 0.85× the pooled 2024–2026 estimate. It is written to each DL player's frozen record, and **never
added to μ** until a season-end leave-one-season-out test says so (`docs/status.md` §6). A failed download
means no term that sync.
"""

from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path

PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
P_GAMES = 4.0  # shrinkage: this many average team-games of dropbacks at the league rate


def counts(raw_gz: bytes, season: int, aliases: dict | None = None) -> dict[int, dict[str, list[int]]]:
    """week -> offense -> [dropbacks, qb hits] from one season's nflverse play-by-play (regular season)."""
    out: dict[int, dict[str, list[int]]] = {}
    with gzip.open(io.BytesIO(raw_gz), "rt", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("season_type") != "REG" or row.get("qb_dropback") not in ("1", "1.0"):
                continue
            if row.get("qb_spike") in ("1", "1.0") or not row.get("posteam") or str(row.get("season")) != str(season):
                continue
            team = (aliases or {}).get(row["posteam"], row["posteam"])
            c = out.setdefault(int(float(row["week"])), {}).setdefault(team, [0, 0])
            c[0] += 1
            c[1] += row.get("qb_hit") in ("1", "1.0")
    return out


def o_hit(weekly: dict[int, dict[str, list[int]]], week: int) -> dict[str, float]:
    """offense -> shrunk QB-hit rate allowed over the weeks before `week`, in points above the league rate."""
    tot: dict[str, list[int]] = {}
    for w, teams in weekly.items():
        if w < week:
            for t, (d, h) in teams.items():
                c = tot.setdefault(t, [0, 0])
                c[0] += d
                c[1] += h
    drop = sum(c[0] for c in tot.values())
    if not drop:
        return {}
    league = sum(c[1] for c in tot.values()) / drop
    games = sum(len(teams) for w, teams in weekly.items() if w < week)
    prior = P_GAMES * drop / games if games else 0.0  # P team-games of dropbacks
    return {t: 100 * ((h + prior * league) / (d + prior) - league) for t, (d, h) in tot.items()}


def load(client, season: int, cache_dir: Path | str, day: str, aliases: dict | None = None):
    """This season's per-week counts, downloaded at most once a day (file cache); None on any failure."""
    cache = Path(cache_dir) / f"pbp_{season}_{day}.csv.gz"
    if cache.exists():
        raw = cache.read_bytes()
    else:
        r = client.get(PBP.format(season=season))
        if not r.ok or not r.body:
            return None
        raw = r.body
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    try:
        return counts(raw, season, aliases)
    except (OSError, ValueError, KeyError):
        return None


def shadow(opp: str | None, rates: dict[str, float], coef: float, clip: float) -> dict | None:
    """The shadow term for a DL facing `opp` (an offense code), or None without a rate for it."""
    if not opp or opp.lstrip("@") not in rates:
        return None
    team = opp.lstrip("@")
    pts = max(-clip, min(clip, coef * rates[team]))
    return {"pts": round(pts, 3), "o_hit": round(rates[team], 3), "opp": team}
