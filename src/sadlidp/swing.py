"""Per-player game-to-game swing (SPEC §6.7, added 2026-10-12; docs/methods-review.md step 3).

One σ_out per position says a pass rusher and a tackle machine swing alike. They don't: SADL pays 6 for
a sack and 2 for a solo tackle, so a player's week swings with his projected stat mix. If each projected
stat were an independent Poisson count, his points' variance would be V = Σ_k coef_k² · λ_k (a sack
projected at 0.5 alone adds 18). On Sleeper's 2025 archive that scales well at DL and partly at LB and
DB (`sadlidp backtest run`), so each player's swing is

    swing_i² = a_p + b_p · V_i

with a_p and b_p fitted per position on 2025 (`[swing]`) and λ the weighted mean of the projection
sources' stats for him (the breakdown's parts). START/UPGRADE odds use σ_pred² = σ_μ² + swing² in place
of the position's σ_out; a player no projection source has stats for keeps σ_out. Each card also shows
a floor and a ceiling, μ + lo·swing and μ + hi·swing, where lo and hi are the 20th and 80th percentiles
of (actual − μ)/swing on 2025: one game in five lands below the floor, one in five above the ceiling.
Tiers are unchanged (they use σ_μ, the sources' disagreement).

Kickers (2026-11-17) have no fitted a_p and b_p. A kicker's points are not a sum of counts (a made field goal
scores a tenth of its distance), so V comes from `kicker_var`, a model of his projected kicking line, with the
prior a_K = 0, b_K = 1: swing² = kicker_var. Where `kicker_var` has no answer the kicker gets no swing of his
own and everything falls back to σ_out,K, exactly as for an IDP no projection source has stats for.
"""

from __future__ import annotations

import math

from . import kicker, scoring
from .positions import IDP_SLOTS, SLOTS, K


def coefficients(cfg: dict) -> dict | None:
    """The `[swing]` settings, or None when the per-player swing is off."""
    s = cfg.get("swing") or {}
    if not s.get("enabled"):
        return None
    return {
        "a": {p: float((s.get("a") or {}).get(p, 0.0)) for p in SLOTS},
        "b": {p: float((s.get("b") or {}).get(p, 1.0)) for p in SLOTS},
        "lo": float(s.get("lo", -0.8)),
        "hi": float(s.get("hi", 0.75)),
    }


def poisson_var(stats: dict, sc: dict) -> float:
    """Outcome variance if every projected stat were an independent Poisson count: Σ coef² · λ."""
    return sum(scoring.num(sc.get(k)) ** 2 * scoring.num(v) for k, v in stats.items() if k in sc)


def kicker_var(line: dict, scoring: dict, yards: dict | None = None) -> float | None:
    """The variance of a kicker's league points in one game, given his projected line.

    Returns a number (points squared), or None when the line lacks what the model needs. None is always
    safe: the kicker then has no swing of his own and the page uses the position's typical swing instead.

    Inputs
    - `line`: his projected game as expected values (floats), averaged over his projection sources by their
      share of his number. Any key may be absent; read an absent key as "not given", not as 0.
        "fga", "fgm"               expected field-goal attempts and makes
        "fgm_yds"                  expected total yards of his made field goals
        "fgmiss"                   expected missed field goals
        "xpa", "xpm", "xpmiss"     expected extra-point attempts, makes and misses
        "fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50p"
                                   expected makes in each distance bucket (yards)
        "fgm_0_39"                 ESPN's one bucket for every make under 40 yards
      The sources cut the buckets differently (Sleeper and CBS by ten yards, ESPN 0-39 / 40-49 / 50+). Each
      source's line enters with its share, so no make is counted in two buckets, but the bucket keys present
      need NOT add up to "fgm": Sleeper's own bands often fall short of its "fgm" (2026 week 11: 21 of 26
      kickers by more than 0.15), and a key a source lacks counts 0 for that source (so "fgm_0_39" carries
      only ESPN's share). Treat "fgm" minus the buckets' sum as makes of unknown distance, or use
      "fgm_yds" / "fgm" as their mean distance. Example, Sleeper alone (buckets 1.94, "fgm" 2.36):
        {"fga": 2.72, "fgm": 2.36, "fgm_yds": 82.31, "fgmiss": 0.36, "xpa": 2.86, "xpm": 2.79,
         "xpmiss": 0.07, "fgm_20_29": 0.41, "fgm_30_39": 0.53, "fgm_40_49": 0.59, "fgm_50p": 0.41}
    - `scoring`: the league's points per unit of each stat. For a kicker the non-zero ones are
      {"fgm_yds": 0.1, "fgmiss": -1.0, "xpm": 1.0, "xpmiss": -1.0}; the dict also holds the other positions'
      keys, so read it with `scoring.get(key, 0)`.
    - `yards`: `[kicker.yards_per_make]` from config/sadl.toml, the mean distance in yards of a made field
      goal in each bucket: {"0_19": 19.0, "20_29": 25.4, "30_39": 34.4, "40_49": 44.4, "50p": 53.9}. The key
      is the bucket's key without "fgm_". It has no entry for ESPN's "0_39".

    A kicker's points for the game are
        0.1 · (the distances of his made field goals, summed) − missed field goals
            + extra points − missed extra points.

    Modelling facts you may use (measured on 2024–26 games; none of them is applied for you):
    - the makes in one distance bucket can be treated as a Poisson count, independent of the other buckets;
    - a made kick from d yards scores scoring["fgm_yds"] · d = 0.1 · d points;
    - within a bucket, the distance of a make has a standard deviation of about 2.8 yards around the
      bucket's mean in `yards`;
    - missed field goals, made extra points and missed extra points are counts, worth −1, +1 and −1 each.

    How the result is used: swing² = a_K + b_K · kicker_var(...), with `[swing.a] K = 0` and `[swing.b] K = 1`,
    so the kicker's game-to-game standard deviation is √kicker_var. For scale, kickers' realised game-to-game
    standard deviation is about 5 points (4.8 within kicker-seasons 2022–25). If this function raises, the
    sync treats it as None for that kicker (`kicker_sigma`), so a mistake here never stops a sync; run.json's
    `swing.kickers.no_var` counts the kickers left without a swing.
    """
    # TODO(human)
    return None


def kicker_sigma(lam: dict, coef: dict, sc: dict, yards: dict | None = None) -> float | None:
    """A kicker's swing from his projected kicking line, √(a_K + b_K · kicker_var); None when `kicker_var`
    has no answer, raises, or the result isn't a positive number (never a zero-width range, SPEC §6.7): an error in
    the owner's model leaves that kicker on σ_out,K instead of failing the whole sync, DL/LB/DB included."""
    try:
        v = kicker_var(lam, sc, yards)
        if v is None:
            return None
        s2 = coef["a"].get(K, 0.0) + coef["b"].get(K, 1.0) * float(v)
    except Exception:
        return None
    return math.sqrt(s2) if s2 > 0 else None


def mix(pid: str, source_rows: dict, groups: dict, sc: dict, eff: dict | None = None,
        slot: str | None = None) -> dict | None:  # fmt: skip
    """His projected IDP stats, averaged over the projection sources that have a row with stats for him,
    weighted by his effective source shares (`eff`, SPEC §6.4) or equally; None when no source does. For a
    kicker (`slot` "K") the kicking keys instead (`kicker.line_keys`), a key a source lacks counting 0 there,
    so no make is counted in two buckets; the buckets add up to the makes only as far as each source's do."""
    rows = []
    for name, rows_s in source_rows.items():
        if groups.get(name) != "projection":
            continue
        stats = ((rows_s or {}).get(pid) or {}).get("stats") or {}
        if stats:
            rows.append((name, stats))
    if not rows:
        return None
    weights = {name: float((eff or {}).get(name, 0.0)) for name, _ in rows}
    if sum(weights.values()) <= 0:
        weights = dict.fromkeys(weights, 1.0)
    total = sum(weights.values())
    keys = scoring.idp_keys(sc)
    if slot == K:  # only what some source gives: an absent key means "not given" to `kicker_var`
        lines = [stats for _, stats in rows]
        keys = [k for k in kicker.line_keys(lines) if any(line.get(k) is not None for line in lines)]
    out: dict[str, float] = {}
    for name, stats in rows:
        for k in keys:
            out[k] = out.get(k, 0.0) + weights[name] / total * scoring.num(stats.get(k))
    return out


def sigma(lam: dict, slot: str | None, coef: dict, sc: dict) -> float:
    """His swing from his projected stat mix: √(a_p + b_p · V)."""
    return math.sqrt(max(0.0, coef["a"].get(slot, 0.0) + coef["b"].get(slot, 1.0) * poisson_var(lam, sc)))


def band(mu: float, swing: float, coef: dict) -> tuple[float, float]:
    """(floor, ceiling): μ + lo·swing (never below 0) and μ + hi·swing."""
    return max(0.0, mu + coef["lo"] * swing), mu + coef["hi"] * swing


def apply(cons: dict[str, dict], slot_of: dict[str, str | None], source_rows: dict, groups: dict, sc: dict,
          coef: dict, slots: tuple[str, ...] = IDP_SLOTS, yards: dict | None = None) -> dict:  # fmt: skip
    """Set `swing`, `floor` and `ceiling` on every player a projection source has stats for, in place.
    Returns what run.json records: how many players got one, and the mean swing per position in `slots`.
    A kicker whose `kicker_var` has no answer gets none of the three (counted in `no_var`): he keeps σ_out,K."""
    sums: dict[str, list[float]] = {p: [] for p in slots}
    no_var = 0
    for pid, c in cons.items():
        slot = slot_of.get(pid)
        lam = mix(pid, source_rows, groups, sc, c.get("eff"), slot)
        if lam is None:
            continue
        s = kicker_sigma(lam, coef, sc, yards) if slot == K else sigma(lam, slot, coef, sc)
        if s is None:
            no_var += 1
            continue
        lo, hi = band(c["mu"], s, coef)
        c.update(swing=round(s, 2), floor=round(lo, 1), ceiling=round(hi, 1))
        if slot in sums:
            sums[slot].append(s)
    return {"players": sum(len(v) for v in sums.values()),
            "mean": {p: round(sum(v) / len(v), 2) if v else None for p, v in sums.items()},
            **({"no_var": no_var} if no_var else {})}  # fmt: skip
