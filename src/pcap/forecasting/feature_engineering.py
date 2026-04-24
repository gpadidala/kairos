"""Lightweight feature helpers used by both forecasters."""

from __future__ import annotations

import statistics
from collections.abc import Sequence

import numpy as np

from pcap.domain.models import MetricPoint


def extract_values(points: Sequence[MetricPoint]) -> list[float]:
    return [p.value for p in points]


def robust_p95(values: Sequence[float]) -> float:
    """Numpy percentile, empty-safe."""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=float), 95))


def linear_slope(values: Sequence[float]) -> float:
    """Least-squares slope (units / index). Zero if <2 points."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = np.arange(n, dtype=float)
    ys = np.asarray(values, dtype=float)
    slope, _ = np.polyfit(xs, ys, 1)
    return float(slope)


def rolling_mean(values: Sequence[float], window: int = 12) -> float:
    """Mean of the last `window` values (or all if shorter)."""
    if not values:
        return 0.0
    w = min(window, len(values))
    return float(statistics.fmean(values[-w:]))


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean absolute percentage error; 0 when empty; safe against zeros."""
    if not actual or not predicted or len(actual) != len(predicted):
        return 1.0
    errs: list[float] = []
    for a, p in zip(actual, predicted, strict=True):
        denom = abs(a) if abs(a) > 1e-9 else 1e-9
        errs.append(abs(a - p) / denom)
    return float(min(1.0, sum(errs) / len(errs)))
