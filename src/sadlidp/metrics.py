"""Accuracy metrics for the Stats page (D20, SPEC §6.8).

Pure functions over paired lists. Each returns None when the input is too small or degenerate, so the
page can say "not enough games yet" instead of showing noise. Bias is predicted − actual (positive =
our number runs high).
"""

from __future__ import annotations

import math
import statistics


def mae(pred: list[float], act: list[float]) -> float | None:
    return sum(abs(p - a) for p, a in zip(pred, act, strict=True)) / len(pred) if pred else None


def rmse(pred: list[float], act: list[float]) -> float | None:
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(pred, act, strict=True)) / len(pred)) if pred else None


def bias(pred: list[float], act: list[float]) -> float | None:
    return sum(p - a for p, a in zip(pred, act, strict=True)) / len(pred) if pred else None


def _corr(x: list[float], y: list[float], method: str, min_n: int) -> float | None:
    if len(x) < min_n:
        return None
    try:
        return statistics.correlation(x, y, method=method)
    except statistics.StatisticsError:  # constant input
        return None


def pearson(x: list[float], y: list[float], min_n: int = 3) -> float | None:
    return _corr(x, y, "linear", min_n)


def spearman(x: list[float], y: list[float], min_n: int = 3) -> float | None:
    return _corr(x, y, "ranked", min_n)


def r2(pred: list[float], act: list[float], min_n: int = 3) -> float | None:
    """Out-of-sample R² = 1 − SSE/SST (negative = worse than guessing the average)."""
    if len(act) < min_n:
        return None
    mean = sum(act) / len(act)
    sst = sum((a - mean) ** 2 for a in act)
    if sst == 0:
        return None
    return 1 - sum((a - p) ** 2 for p, a in zip(pred, act, strict=True)) / sst


def within(pred: list[float], act: list[float], sigma: list[float], k: float) -> float | None:
    """Share of results inside ±k·σ of the prediction."""
    if not pred:
        return None
    return sum(abs(a - p) <= k * s for p, a, s in zip(pred, act, sigma, strict=True)) / len(pred)


def top_k_hits(pred: dict[str, float], actual_all: dict[str, float], k: int = 12) -> tuple[int, int] | None:
    """Of our top-k predicted, how many finished in the actual top k among everyone at the position
    (a player tied with the k-th best score counts as in it)."""
    if len(pred) < k or len(actual_all) < k:
        return None
    ours = sorted(pred, key=lambda pid: (-pred[pid], pid))[:k]
    cut = sorted(actual_all.values(), reverse=True)[k - 1]
    return sum(actual_all.get(pid, float("-inf")) >= cut for pid in ours), k


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def pairwise(mu: dict[str, float], sigma: dict[str, float], act: dict[str, float],
             tag: str = "") -> list[tuple[float, int, str]]:  # fmt: skip
    """All pairs (a, b) of a pool: the stated probability that the higher-μ player outscores the other
    (oriented so p ≥ 0.5), whether he did, and who was favoured (`tag` + his id, so a calibration bucket can
    count distinct favourites); ties in the result are skipped."""
    ids = sorted(mu)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if act[a] == act[b]:
                continue
            spread = math.sqrt(sigma[a] ** 2 + sigma[b] ** 2)
            p = phi((mu[a] - mu[b]) / spread) if spread else (1.0 if mu[a] > mu[b] else 0.5)
            hit = act[a] > act[b]
            out.append((p, int(hit), tag + a) if p >= 0.5 else (1 - p, int(not hit), tag + b))
    return out


def brier(pairs: list[tuple], min_n: int = 30) -> float | None:
    if len(pairs) < min_n:
        return None
    return sum((x[0] - x[1]) ** 2 for x in pairs) / len(pairs)


MIN_FAVOURITES = 20  # a calibration bucket resting on fewer distinct favoured player-weeks is "too few"


def calibration(pairs: list[tuple], edges=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)) -> list[dict]:
    """Said vs happened per odds bucket, with how many distinct favoured player-weeks it rests on (a bucket
    of many pairs can still be one player's one game)."""
    rows = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        sel = [x for x in pairs if lo <= x[0] < hi]
        if sel:
            favs = len({x[2] for x in sel if len(x) > 2})
            rows.append({"lo": lo, "hi": min(hi, 1.0), "n": len(sel), "said": sum(x[0] for x in sel) / len(sel),
                         "happened": sum(x[1] for x in sel) / len(sel), "favourites": favs,
                         "too_few": favs < MIN_FAVOURITES})  # fmt: skip
    return rows


# --- uncertainty (docs/eval-protocol.md: week-clustered SEs, t-quantiles at 9 weeks or fewer) ---------------
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306}


def margin(se: float | None, clusters: int) -> float | None:
    """The ± that goes with a week-clustered SE: t(0.975, df = weeks − 1) at 9 weeks or fewer, 2 beyond."""
    if se is None or clusters < 2:
        return None
    return _T975.get(clusters - 1, 2.0) * se


def mean_se(xs: list[float]) -> tuple[float | None, float | None]:
    """Mean of per-week values and its standard error (None below 2 weeks)."""
    if not xs:
        return None, None
    m = statistics.fmean(xs)
    return m, (statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) >= 2 else None)


def cluster_mean(groups: list[list[float]]) -> tuple[float | None, float | None]:
    """Mean over every value and its SE clustered by group (a week): sqrt(G/(G−1)·Σ_g(S_g − m·n_g)²)/N."""
    groups = [g for g in groups if g]
    n = sum(len(g) for g in groups)
    if not n:
        return None, None
    m = sum(sum(g) for g in groups) / n
    if len(groups) < 2:
        return m, None
    g = len(groups)
    return m, math.sqrt(g / (g - 1) * sum((sum(x) - m * len(x)) ** 2 for x in groups)) / n


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for a share."""
    if n <= 0:
        return None
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def coverage(act: list[float], floor: list[float], ceil: list[float]) -> dict | None:
    """Share of games below the floor (strictly), inside, and above the ceiling (strictly): aim 20/60/20."""
    if not act:
        return None
    below = sum(a < f for a, f in zip(act, floor, strict=True))
    above = sum(a > c for a, c in zip(act, ceil, strict=True))
    n = len(act)
    return {"n": n, "below": below / n, "inside": (n - below - above) / n, "above": above / n}


def spread_ratio(pred: list[float], act: list[float], sigma: list[float]) -> float | None:
    """RMS of (actual − predicted)/σ_pred: 1.00 when the stated spread is honest."""
    z = [(a - p) / s for p, a, s in zip(pred, act, sigma, strict=True) if s]
    return math.sqrt(sum(x * x for x in z) / len(z)) if z else None


def top_k_random(pool: list[str], actual_all: dict[str, float], k: int = 12) -> tuple[float, float] | None:
    """What k players drawn at random from the pool would land in the actual top k (ties at k-th count):
    the expected count k·K/N and its variance (hypergeometric), K = pool players who made the top k."""
    if len(pool) < k or len(actual_all) < k:
        return None
    cut = sorted(actual_all.values(), reverse=True)[k - 1]
    big_k, n = sum(actual_all.get(pid, float("-inf")) >= cut for pid in pool), len(pool)
    q = big_k / n
    return k * q, (k * q * (1 - q) * (n - k) / (n - 1) if n > 1 else 0.0)
