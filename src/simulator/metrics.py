"""Statistics helpers: exact percentiles, log-bucket and weighted histograms."""

from __future__ import annotations

import math
from typing import Optional, Sequence


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Linear-interpolation percentile (numpy's default method). None if empty."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * p / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


class LogHistogram:
    """Counts in geometric buckets (ratio 1.01, from 1 us), for per-token ITL.

    Holding every inter-token gap of millions of requests is too much memory;
    quantiles from this histogram are within 0.5% of the exact value (the
    bucket's geometric midpoint is reported).
    """

    _BASE = 1e-6
    _LOG_RATIO = math.log(1.01)

    def __init__(self) -> None:
        self._counts: dict[int, int] = {}
        self.count = 0

    def add(self, value: float, count: int = 1) -> None:
        if count <= 0:
            return
        idx = 0 if value <= self._BASE else int(math.log(value / self._BASE) / self._LOG_RATIO)
        self._counts[idx] = self._counts.get(idx, 0) + count
        self.count += count

    def quantile(self, q: float) -> Optional[float]:
        if not self.count:
            return None
        target = max(q * self.count, 1e-12)
        seen = 0
        for idx in sorted(self._counts):
            seen += self._counts[idx]
            if seen >= target:
                return self._BASE * math.exp((idx + 0.5) * self._LOG_RATIO)
        return None  # pragma: no cover - unreachable, seen ends at count


class WeightedBins:
    """Time-weighted distribution of a ratio in [0, 1] (1/1000 resolution)."""

    def __init__(self, bins: int = 1000) -> None:
        self._bins = bins
        self._w = [0.0] * (bins + 1)
        self._sum = 0.0
        self.weight = 0.0

    def add(self, value: float, weight: float) -> None:
        if weight <= 0:
            return
        self._w[min(int(value * self._bins), self._bins)] += weight
        self._sum += value * weight
        self.weight += weight

    def mean(self) -> float:
        return self._sum / self.weight if self.weight else 0.0

    def quantile(self, q: float) -> float:
        if not self.weight:
            return 0.0
        target = q * self.weight
        seen = 0.0
        for i, w in enumerate(self._w):
            seen += w
            if seen >= target - 1e-12 and w > 0:
                return i / self._bins
        return 1.0  # pragma: no cover


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Length of the intersection of [a0, a1] and [b0, b1]."""
    return max(0.0, min(a1, b1) - max(a0, b0))
