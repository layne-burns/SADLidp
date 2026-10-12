"""Imputation of stat keys a projection source doesn't cover (SPEC.md §6.2).

`imputed_k = anchor × r̂_k`: TFL per projected tackle (solo + assisted); QB hits per projected sack for players
with ≥ 2 sacks over last season and this one so far, otherwise per game; defensive TDs, safeties and blocked
kicks per game. `r̂_k = (x_k + m_k·r_prior) / (exposure + m_k)`, with x_k and exposure (the anchor's total) from
this season's Sleeper actuals before the target week. `r_prior` is the player's last-season rate shrunk toward
his granular position's (DE/DT/LB/CB/S), and `m_k` comes from empirical Bayes (method of moments) on last
season's player rates; both are saved in `data/<prev>/impute_priors.json`. Config `impute.m_k.*` overrides m_k.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import storage
from .scoring import num
from .weeklog import played

IMPUTABLE = ("idp_tkl_loss", "idp_qb_hit", "idp_def_td", "idp_safe", "idp_blk_kick")
VERSION = 1
SACK_MIN = 2.0  # QB hits ride on sacks only for players with at least this many (last season + this one)
M_BOUNDS = (1.0, 500.0)
TKL, SACK, GAMES = "tkl", "sack", "games"
# rate key -> (counted stat, anchor). `idp_qb_hit_pg` is the per-game QB-hit rate for players under SACK_MIN.
RATES = {
    "idp_tkl_loss": ("idp_tkl_loss", TKL),
    "idp_qb_hit": ("idp_qb_hit", SACK),
    "idp_qb_hit_pg": ("idp_qb_hit", GAMES),
    "idp_def_td": ("idp_def_td", GAMES),
    "idp_safe": ("idp_safe", GAMES),
    "idp_blk_kick": ("idp_blk_kick", GAMES),
}
# m_k when last season can't identify it (no between-player variance beyond Poisson noise, or no data);
# the first three are 2025's fitted values, rounded
DEFAULT_M = {
    "idp_tkl_loss": 50.0, "idp_qb_hit": 5.0, "idp_qb_hit_pg": 15.0,
    "idp_def_td": 500.0, "idp_safe": 500.0, "idp_blk_kick": 500.0,
}  # fmt: skip
# 2025 position rates in RATES order, used only when no priors file could be built (Imputer with empty priors)
FALLBACK_RATES = {
    "DE": (0.192, 2.31, 0.178, 0.00255, 0.000848, 0.00764),
    "DT": (0.116, 2.26, 0.142, 0.00105, 0.00105, 0.00526),
    "LB": (0.0741, 2.11, 0.0774, 0.00366, 0.000914, 0.00061),
    "CB": (0.0388, 1.6, 0.0203, 0.00535, 0.0, 0.00178),
    "S": (0.0347, 1.66, 0.0499, 0.00429, 0.00123, 0.00307),
}
GRANULAR = {
    "DE": "DE", "DT": "DT", "NT": "DT", "LB": "LB", "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "CB": "CB", "DB": "CB", "S": "S", "FS": "S", "SS": "S", "DL": "DE",
}  # fmt: skip
# Sleeper files most safeties (and many linemen) under the generic DB / DL; their depth-chart slot refines that
DEPTH_REFINE = {"DB": {"SS": "S", "FS": "S", "S": "S"}, "DL": {"NT": "DT", "DT": "DT", "LDT": "DT", "RDT": "DT"}}
FALLBACK_POS = "LB"


def granular(player: dict | None) -> str | None:
    """DE/DT/LB/CB/S from Sleeper's `position`; a generic DB or DL goes by `depth_chart_position` when it names
    a safety or tackle spot, else DB → CB and DL → DE. None for a non-IDP."""
    code = str((player or {}).get("position") or "").upper()
    depth = str((player or {}).get("depth_chart_position") or "").upper()
    return DEPTH_REFINE.get(code, {}).get(depth) or GRANULAR.get(code)


def tackles(line: dict) -> float:
    """Solo + assisted, or the total when a line has only that."""
    if "idp_tkl_solo" in line or "idp_tkl_ast" in line:
        return num(line.get("idp_tkl_solo")) + num(line.get("idp_tkl_ast"))
    return num(line.get("idp_tkl"))


def _counts(lines) -> dict[str, float]:
    """Games, anchors and stat totals over the lines from games a player took part in."""
    out = {GAMES: 0.0, TKL: 0.0, SACK: 0.0, "solo": 0.0, **{k: 0.0 for k in IMPUTABLE}}
    for line in lines:
        if not played(line):
            continue
        out[GAMES] += 1
        out[TKL] += tackles(line)
        out[SACK] += num(line.get("idp_sack"))
        out["solo"] += num(line.get("idp_tkl_solo"))
        for k in IMPUTABLE:
            out[k] += num(line.get(k))
    return out


def _sig(x: float) -> float:
    return float(f"{x:.4g}")


def _fit_m(groups: dict[str, list[tuple[float, float]]], default: float) -> float:
    """Method of moments for a gamma-Poisson rate model with a common m_k = μ_p/τ_p² across positions:
    E[Σ e_i(r_i − μ̂_p)²] = (n_p − 1)·μ_p + (τ_p²)(E_p − Σe_i²/E_p), so the observed excess over Poisson
    noise gives 1/m. Pairs are (count, exposure) with exposure > 0. Clamped to M_BOUNDS; `default` if degenerate."""
    top = bottom = 0.0
    for pairs in groups.values():
        pairs = [(x, e) for x, e in pairs if e > 0]
        if len(pairs) < 2:
            continue
        xs, es = sum(x for x, _ in pairs), sum(e for _, e in pairs)
        mu = xs / es
        q = sum(x * x / e for x, e in pairs) - xs * xs / es
        bottom += q - (len(pairs) - 1) * mu
        top += mu * (es - sum(e * e for _, e in pairs) / es)
    if top <= 0 or bottom <= 0:
        return default
    return min(max(top / bottom, M_BOUNDS[0]), M_BOUNDS[1])


def build_priors(weekly: dict[int, dict[str, dict]], players: dict[str, dict]) -> dict:
    """Last season's position rates, m_k and shrunk player rates (SPEC §6.2) from weekly Sleeper stat lines."""
    lines: dict[str, list[dict]] = {}
    for week_lines in weekly.values():
        for pid, line in week_lines.items():
            if granular(players.get(str(pid))) and played(line):
                lines.setdefault(str(pid), []).append(line)
    counts = {pid: _counts(ls) for pid, ls in lines.items()}
    pos = {pid: granular(players.get(pid)) for pid in counts}

    def members(key: str) -> list[str]:  # QB hits per sack are fitted on the players they apply to
        if key == "idp_qb_hit":
            return [p for p, c in counts.items() if c[SACK] >= SACK_MIN]
        if key == "idp_qb_hit_pg":
            return [p for p, c in counts.items() if c[SACK] < SACK_MIN]
        return list(counts)

    pos_rate: dict[str, dict[str, float]] = {}
    m: dict[str, float] = {}
    for key, (stat, anchor) in RATES.items():
        groups: dict[str, list[tuple[float, float]]] = {}
        for pid in members(key):
            groups.setdefault(pos[pid], []).append((counts[pid][stat], counts[pid][anchor]))
        pairs = [pr for prs in groups.values() for pr in prs]
        x_all, e_all = sum(x for x, _ in pairs), sum(e for _, e in pairs)
        overall = x_all / e_all if e_all else 0.0
        for g in sorted(set(GRANULAR.values())):
            xs, es = sum(x for x, _ in groups.get(g, [])), sum(e for _, e in groups.get(g, []))
            pos_rate.setdefault(g, {})[key] = _sig(xs / es if es else overall)
        m[key] = round(_fit_m(groups, DEFAULT_M[key]), 2)
    player_rate = {}
    for pid, c in counts.items():
        rates = pos_rate[pos[pid]]
        player_rate[pid] = {
            key: _sig((c[stat] + m[key] * rates[key]) / (c[anchor] + m[key])) for key, (stat, anchor) in RATES.items()
        }
    player_counts = {pid: {k: round(v, 2) for k, v in c.items() if v or k == GAMES} for pid, c in counts.items()}
    return {
        "version": VERSION,
        "pos_rate": pos_rate,
        "m": m,
        "player_rate": player_rate,
        "player_counts": player_counts,
    }


class Imputer:
    """Fills the IMPUTABLE keys a source doesn't cover. `history` holds this season's Sleeper stat lines per
    player for weeks before the target week only (the caller's guarantee: no leakage). Empty `priors` (none
    could be built) fall back to FALLBACK_RATES and DEFAULT_M."""

    def __init__(
        self,
        priors: dict,
        history: dict[str, list[dict]],
        players: dict[str, dict],
        m_override: dict[str, float] | None = None,
    ):
        self.priors = priors or {}
        self.history = history
        self.players = players
        self.m = {**DEFAULT_M, **{k: float(v) for k, v in (self.priors.get("m") or {}).items()}}
        self.m.update({k: float(v) for k, v in (m_override or {}).items() if k in RATES})
        self.pos_rate = self.priors.get("pos_rate") or {
            pos: dict(zip(RATES, values, strict=True)) for pos, values in FALLBACK_RATES.items()
        }
        self._season: dict[str, dict[str, float]] = {}

    def season_counts(self, pid: str) -> dict[str, float]:
        if pid not in self._season:
            self._season[pid] = _counts(self.history.get(pid) or [])
        return self._season[pid]

    def prior(self, pid: str, key: str) -> float:
        """His last-season shrunk rate, else his granular position's (unknown position → LB)."""
        own = ((self.priors.get("player_rate") or {}).get(pid) or {}).get(key)
        if own is not None:
            return float(own)
        rates = self.pos_rate.get(granular(self.players.get(pid))) or self.pos_rate.get(FALLBACK_POS) or {}
        return float(rates.get(key, 0.0))

    def rate(self, pid: str, key: str) -> float:
        stat, anchor = RATES[key]
        c, m = self.season_counts(pid), self.m[key]
        return (c[stat] + m * self.prior(pid, key)) / (c[anchor] + m)

    def anchor(self, pid: str, key: str, line: dict) -> tuple[str, float]:
        """(rate key, anchor value from the source's line) for an IMPUTABLE key."""
        if key == "idp_tkl_loss":
            return key, tackles(line)
        if key == "idp_qb_hit":
            last = ((self.priors.get("player_counts") or {}).get(pid) or {}).get(SACK, 0.0)
            if num(last) + self.season_counts(pid)[SACK] >= SACK_MIN:
                return key, num(line.get("idp_sack"))
            return "idp_qb_hit_pg", 1.0
        return key, 1.0

    def fill(self, pid: str, line: dict, covered: set[str]) -> tuple[dict, list[str]]:
        """A copy of `line` with every uncovered IMPUTABLE key set to anchor × r̂, and the keys imputed."""
        out, imputed = dict(line), []
        for key in IMPUTABLE:
            if key in covered:
                continue
            rate_key, anchor = self.anchor(pid, key, line)
            out[key] = round(anchor * self.rate(pid, rate_key), 4)
            imputed.append(key)
        return out, sorted(imputed)


def load_or_build_priors(
    sleeper, data_dir: Path | str, season: int, players: dict, log: Callable = print
) -> dict | None:
    """Last season's priors from `data/<season-1>/impute_priors.json`, built from its 18 weeks of Sleeper
    stats and saved the first time. None (logged) if any week is unavailable."""
    prev = season - 1
    path = Path(data_dir) / str(prev) / "impute_priors.json"
    priors = storage.read_json(path)
    if priors and priors.get("version") == VERSION:
        return priors
    weekly = {}
    for w in range(1, 19):
        payload, err = sleeper.stats(prev, w)
        if not isinstance(payload, dict):
            log(f"imputation priors: {prev} week {w} stats unavailable ({err}); not built this sync")
            return None
        weekly[w] = {str(k): v for k, v in payload.items() if isinstance(v, dict)}
    priors = {**build_priors(weekly, players), "season": prev}
    storage.write_json(path, priors)
    return priors
