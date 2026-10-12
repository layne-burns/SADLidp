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
    """Of our top-k predicted, how many finished in the actual top k among everyone at the position."""
    if len(pred) < k or len(actual_all) < k:
        return None
    ours = sorted(pred, key=lambda pid: (-pred[pid], pid))[:k]
    best = set(sorted(actual_all, key=lambda pid: (-actual_all[pid], pid))[:k])
    return sum(pid in best for pid in ours), k


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def pairwise(mu: dict[str, float], sigma: dict[str, float], act: dict[str, float]) -> list[tuple[float, int]]:
    """All pairs (a, b) of a pool: the stated probability that the higher-μ player outscores the other
    (oriented so p ≥ 0.5) and whether he did; ties in the result are skipped."""
    ids = sorted(mu)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if act[a] == act[b]:
                continue
            spread = math.sqrt(sigma[a] ** 2 + sigma[b] ** 2)
            p = phi((mu[a] - mu[b]) / spread) if spread else (1.0 if mu[a] > mu[b] else 0.5)
            hit = act[a] > act[b]
            out.append((p, int(hit)) if p >= 0.5 else (1 - p, int(not hit)))
    return out


def brier(pairs: list[tuple[float, int]], min_n: int = 30) -> float | None:
    if len(pairs) < min_n:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def calibration(pairs: list[tuple[float, int]], edges=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)) -> list[dict]:
    rows = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        sel = [(p, o) for p, o in pairs if lo <= p < hi]
        if sel:
            rows.append({"lo": lo, "hi": min(hi, 1.0), "n": len(sel), "said": sum(p for p, _ in sel) / len(sel),
                         "happened": sum(o for _, o in sel) / len(sel)})  # fmt: skip
    return rows
