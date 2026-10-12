"""Per-position weekly outcome noise σ_out (SPEC.md §6.7).

Until accuracy tracking has 3 evaluated weeks, σ_out,p is last season's within-player SD of weekly
SADL points (players with ≥ 8 games), scored with the current settings.
"""

from __future__ import annotations

import math
import statistics

from . import scoring
from .positions import IDP_SLOTS, slot_of


def within_player_sd(
    weekly_stats: dict[int, dict[str, dict]], players: dict[str, dict], scoring_settings: dict, min_games: int = 8
) -> dict:
    """weekly_stats[week][player_id] = actual stat line. Returns {"DL": sd, ..., "players": {...}}."""
    weekly_points: dict[str, list[float]] = {}
    for lines in weekly_stats.values():
        for pid, st in lines.items():
            if not scoring.num(st.get("gp")) or slot_of(players.get(pid, {})) is None:
                continue
            weekly_points.setdefault(pid, []).append(scoring.points(st, scoring_settings))
    sds: dict[str, list[float]] = {s: [] for s in IDP_SLOTS}
    for pid, pts in weekly_points.items():
        if len(pts) >= min_games:
            sds[slot_of(players[pid])].append(statistics.stdev(pts))
    out: dict = {"players": {s: len(v) for s, v in sds.items()}}
    for s, v in sds.items():
        out[s] = round(math.sqrt(sum(x * x for x in v) / len(v)), 3) if v else None
    return out
