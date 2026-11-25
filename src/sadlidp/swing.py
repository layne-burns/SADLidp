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

Kickers (2026-11-17) are different. A kicker's points are not a sum of counts (a made field goal scores a tenth
of its distance), so V comes from `kicker_var`, a compound-Poisson model of his projected kicking line, and
swing² = b_K · kicker_var with b_K = 0.85 measured on real kicker-games (2026-11-21). His floor and ceiling use
his own multipliers (`[swing.kicker]`). Where `kicker_var` has no answer the kicker gets no swing of his own and
everything falls back to σ_out,K, exactly as for an IDP no projection source has stats for.
"""

from __future__ import annotations

import math

from . import kicker, scoring
from .positions import IDP_SLOTS, K

SWING_SLOTS = IDP_SLOTS + (K,)  # the slots with a swing model; a team defense has none, only σ_out,DEF (D25)

_num = scoring.num  # `kicker_var`'s own `scoring` argument hides the module


def coefficients(cfg: dict) -> dict | None:
    """The `[swing]` settings, or None when the per-player swing is off."""
    s = cfg.get("swing") or {}
    if not s.get("enabled"):
        return None
    return {
        "a": {p: float((s.get("a") or {}).get(p, 0.0)) for p in SWING_SLOTS},
        "b": {p: float((s.get("b") or {}).get(p, 1.0)) for p in SWING_SLOTS},
        "lo": float(s.get("lo", -0.8)),
        "hi": float(s.get("hi", 0.75)),
    }


def poisson_var(stats: dict, sc: dict) -> float:
    """Outcome variance if every projected stat were an independent Poisson count: Σ coef² · λ."""
    return sum(scoring.num(sc.get(k)) ** 2 * scoring.num(v) for k, v in stats.items() if k in sc)


# Kick distances behind `kicker_var`, beyond the bucket means in `[kicker.yards_per_make]` (nflverse made field
# goals, 2024–26, n = 2,398; the probe of 2026-11-17)
MAKE_SD_IN_BUCKET = 2.8  # yards: a make's distance around its ten-yard bucket's mean (2.7–3.1 by bucket)
ESPN_0_39 = (30.47, 5.3)  # ESPN's one band under 40 yards: a make's mean distance there and its SD
LEAGUE_BANDS = {"50_59": 53.53, "60p": 62.35}  # the league's own cut of 50+ yards, should a source use it
MAKE_MEAN, MAKE_SD = 39.0, 10.5  # any make, for a line that gives makes but no bucket at all
KICK_KEYS_MODELLED = ("fgm", "fgm_yds", "fgmiss", "xpm", "xpmiss")  # the scoring keys `kicker_var` prices
KICKER_MODEL = "poisson-bands-2026-11-21"  # recorded on each page whose kickers got a swing (the Stats page reads it)


def kicker_var(line: dict, scoring: dict, yards: dict | None = None) -> float | None:
    """The variance of a kicker's league points in one game, given his projected line (points squared).

    His points are 0.1 · (the distances of his made field goals, summed) − missed field goals + extra points
    − missed extra points. Each part is treated as a Poisson count, independent of the others: the makes in
    each distance bucket, the misses, the made and the missed extra points. For independent Poisson counts
    the variance of the total is Σ λ · E[X²], λ the expected count and X the points one event scores:
    - a miss or an extra point scores ±1, so it adds λ;
    - a make from a bucket with mean distance m scores 0.1 · d, d spread about 2.8 yards around m, so it adds
      λ · 0.01 · (m² + 2.8²): long kicks swing more than short ones.

    `line` is his projected game as expected values, averaged over his projection sources by their share of
    his number (`mix`); an absent key means "not given". Keys: "fga", "fgm", "fgm_yds", "fgmiss", "xpa",
    "xpm", "xpmiss", the ten-yard buckets "fgm_0_19" … "fgm_50p" (Sleeper, CBS) and ESPN's "fgm_0_39". The
    buckets give the spread of his kicks and "fgm" their number: Sleeper's own buckets often add up to less
    than its "fgm" (2026 week 11: 21 of 26 kickers short by more than 0.15) while its "fgm" sits at the real
    level, so the buckets are scaled up or down to "fgm". `yards` is `[kicker.yards_per_make]`, each bucket's
    mean distance (the key without "fgm_"); ESPN's "0_39" and the league's "50_59" / "60p" have their own. Each
    source uses one cut, so no make is in two buckets. A value that isn't a number counts as not given.

    Checked on real kicks (2026-11-21): the distance facts hold and each bucket alone is close to Poisson, but a
    game has only so many scoring drives, so a kicker's makes vary less than Poisson (variance/mean 0.87) and
    trade off against his extra points (r −0.21). The real variance is about 0.85 of this model's, which
    `[swing.b] K` applies.

    Returns None when the line gives no field goals and no extra points, or when the league scores a kicking
    stat this model doesn't price (a distance-bucket bonus, say): the kicker then keeps σ_out,K. The sync
    uses swing² = a_K + b_K · kicker_var (`[swing.a]`, `[swing.b]`), and an error raised here is treated as
    None (`kicker_sigma`), so it never stops a sync.
    """
    pts = {k: _num(scoring.get(k, 0)) for k in KICK_KEYS_MODELLED}
    if any(_num(v) for k, v in scoring.items() if k.startswith(("fg", "xp")) and k not in KICK_KEYS_MODELLED):
        return None  # e.g. 3 points a make from 0-39: the bucket mix would matter, and it isn't modelled

    def given(key: str) -> float | None:  # a number ≥ 0, or None when absent, not a number, or NaN
        try:
            v = float(line[key])
        except (KeyError, TypeError, ValueError):
            return None
        return max(0.0, v) if v == v else None

    mean = {**kicker.YARDS_PER_MAKE, **(yards or {}), **LEAGUE_BANDS}
    bands = {b: (float(mean[b]), MAKE_SD_IN_BUCKET) for b in (*kicker.BUCKETS, *LEAGUE_BANDS)} | {"0_39": ESPN_0_39}
    lam = {b: given(f"fgm_{b}") or 0.0 for b in bands}
    in_bands = sum(lam.values())
    fgm = given("fgm")
    fgm = in_bands if fgm is None else fgm
    if in_bands <= 0:  # makes with no bucket: the average make
        made = given("fgm_yds")
        lam, in_bands = {"all": fgm}, fgm
        bands = {"all": (made / fgm if made and fgm else MAKE_MEAN, MAKE_SD)}

    def per_event(m: float, sd: float) -> float:  # E[X²] for one make, X = (points a make) + 0.1 · d, d ≈ m ± sd
        x = pts["fgm"] + pts["fgm_yds"] * m
        return x * x + (pts["fgm_yds"] * sd) ** 2

    var = sum(lam[b] * fgm / in_bands * per_event(*bands[b]) for b in bands) if in_bands > 0 else 0.0
    fga, xpa, xpm = given("fga"), given("xpa"), given("xpm") or 0.0
    miss = given("fgmiss")
    miss = max(0.0, fga - fgm) if miss is None and fga is not None else (miss or 0.0)
    xpmiss = given("xpmiss")
    xpmiss = max(0.0, xpa - xpm) if xpmiss is None and xpa is not None else (xpmiss or 0.0)
    var += pts["fgmiss"] ** 2 * miss + pts["xpm"] ** 2 * xpm + pts["xpmiss"] ** 2 * xpmiss
    return var if var > 0 else None


def kicker_sigma(lam: dict, coef: dict, sc: dict, yards: dict | None = None) -> float | None:
    """A kicker's swing from his projected kicking line, √(a_K + b_K · kicker_var); None when `kicker_var` has
    no answer, raises, or gives something that isn't a positive finite number (never a zero-width range, SPEC
    §6.7): an error there leaves that kicker on σ_out,K instead of failing the whole sync, DL/LB/DB included."""
    try:
        v = kicker_var(lam, sc, yards)
        if v is None:
            return None
        s2 = coef["a"].get(K, 0.0) + coef["b"].get(K, 1.0) * float(v)
    except Exception:
        return None
    return math.sqrt(s2) if 0 < s2 < math.inf else None


def kicker_coefficients(cfg: dict, coef: dict) -> dict:
    """The swing settings the kickers use: `coef` with a kicker's own floor and ceiling multipliers
    (`[swing.kicker]` lo and hi; the IDP ones when absent) and `kicker_model`, the tag of the `kicker_var` in force,
    which the page records so the Stats page can tell a week whose kickers had a swing from one before."""
    k = (cfg.get("swing") or {}).get("kicker") or {}
    return {**coef, "lo": float(k.get("lo", coef["lo"])), "hi": float(k.get("hi", coef["hi"])),
            "kicker_model": KICKER_MODEL}  # fmt: skip


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
