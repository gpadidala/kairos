"""Forecasting — statistical + ensemble behavior."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from kairos.domain.enums import ForecastModel
from kairos.domain.exceptions import ForecastError
from kairos.domain.models import MetricPoint, MetricSeries, Workload
from kairos.forecasting.base import ForecastRequest
from kairos.forecasting.ensemble import EnsembleForecaster
from kairos.forecasting.feature_engineering import linear_slope, mape, robust_p95, rolling_mean
from kairos.forecasting.statistical_forecaster import StatisticalForecaster


def _series(workload: Workload, values: list[float], *, step: int = 300) -> MetricSeries:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    pts = [
        MetricPoint(ts=start + timedelta(seconds=step * i), value=v) for i, v in enumerate(values)
    ]
    return MetricSeries(
        workload=workload, metric="cpu_usage_cores", points=pts, resolution_seconds=step
    )


def test_feature_helpers() -> None:
    assert robust_p95([]) == 0.0
    assert robust_p95([1, 2, 3, 4, 5]) == pytest.approx(4.8)
    assert linear_slope([1, 2, 3]) == pytest.approx(1.0)
    assert linear_slope([]) == 0.0
    assert rolling_mean([1, 2, 3, 4], window=2) == pytest.approx(3.5)
    assert rolling_mean([]) == 0.0
    assert mape([], []) == 1.0
    assert mape([10.0], [10.0]) == pytest.approx(0.0)
    assert mape([100.0], [90.0]) == pytest.approx(0.1)


def test_statistical_flat_series(sample_workload: Workload, fixed_now: datetime) -> None:
    s = _series(sample_workload, [1.0] * 120)
    f = StatisticalForecaster().predict(
        ForecastRequest(series=s, horizon_hours=48, resolution_seconds=300, now=fixed_now)
    )
    assert f.model_used == ForecastModel.STATISTICAL
    assert f.horizon_hours == 48
    assert len(f.points) == int(48 * 3600 / 300)
    assert 0.9 < f.p95_predicted < 1.1
    assert 0.0 <= f.confidence_score <= 1.0
    assert f.peak_predicted >= f.p95_predicted * 0.99


def test_statistical_growing_series_has_higher_peak(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    s = _series(sample_workload, [0.1 * i for i in range(60)])
    f = StatisticalForecaster().predict(
        ForecastRequest(series=s, horizon_hours=24, resolution_seconds=600, now=fixed_now)
    )
    assert f.peak_predicted > f.points[0].value


def test_statistical_rejects_single_point(sample_workload: Workload, fixed_now: datetime) -> None:
    s = MetricSeries(
        workload=sample_workload,
        metric="cpu",
        points=[MetricPoint(ts=fixed_now, value=1.0)],
        resolution_seconds=300,
    )
    with pytest.raises(ForecastError):
        StatisticalForecaster().predict(
            ForecastRequest(series=s, horizon_hours=24, resolution_seconds=300, now=fixed_now)
        )


def test_ensemble_falls_back_to_statistical_when_prophet_disabled(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    s = _series(sample_workload, [math.sin(i / 10) + 2 for i in range(200)])
    e = EnsembleForecaster(use_prophet=False)
    f = e.predict(
        ForecastRequest(series=s, horizon_hours=12, resolution_seconds=300, now=fixed_now)
    )
    assert f.model_used == ForecastModel.STATISTICAL


def test_ensemble_falls_back_on_short_series(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    # 200 points at 300s = ~17h < 14 days → Prophet rejects, falls back.
    s = _series(sample_workload, [1.0 + 0.01 * i for i in range(200)])
    e = EnsembleForecaster(use_prophet=True)
    f = e.predict(
        ForecastRequest(series=s, horizon_hours=12, resolution_seconds=300, now=fixed_now)
    )
    assert f.model_used == ForecastModel.STATISTICAL


def test_forecast_points_are_monotonically_increasing_in_time(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    s = _series(sample_workload, [1.0] * 50)
    f = StatisticalForecaster().predict(
        ForecastRequest(series=s, horizon_hours=6, resolution_seconds=300, now=fixed_now)
    )
    timestamps = [p.ts for p in f.points]
    assert all(a < b for a, b in pairwise(timestamps))
