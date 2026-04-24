"""Statistical forecaster — rolling p95 + linear slope projection.

Always available (no heavy deps). Used as fallback when Prophet is unavailable,
series is short, or Prophet raises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pcap.domain.enums import ForecastModel
from pcap.domain.exceptions import ForecastError
from pcap.domain.models import Forecast, MetricPoint
from pcap.forecasting.base import Forecaster, ForecastRequest
from pcap.forecasting.feature_engineering import (
    extract_values,
    linear_slope,
    mape,
    robust_p95,
    rolling_mean,
)


class StatisticalForecaster(Forecaster):
    """Robust, lightweight forecast baseline."""

    name = "statistical"

    def predict(self, request: ForecastRequest) -> Forecast:
        series = request.series
        if len(series.points) < 2:
            raise ForecastError(
                f"need >=2 points for statistical forecast, got {len(series.points)}"
            )

        values = extract_values(series.points)
        base_p95 = robust_p95(values)
        mean_recent = rolling_mean(values, window=12)
        slope_per_step = linear_slope(values)

        step = timedelta(seconds=request.resolution_seconds)
        total_steps = max(1, int((request.horizon_hours * 3600) / request.resolution_seconds))

        anchor_ts = series.points[-1].ts
        if anchor_ts.tzinfo is None:
            anchor_ts = anchor_ts.replace(tzinfo=UTC)

        # Project forward: base level smoothed between recent mean and p95,
        # plus bounded slope component. Cap slope to avoid runaway linear drift.
        slope_capped = max(min(slope_per_step, mean_recent * 0.1), -mean_recent * 0.1)

        base_level = 0.5 * mean_recent + 0.5 * base_p95

        points: list[MetricPoint] = []
        peak = base_level
        peak_at = anchor_ts
        for i in range(1, total_steps + 1):
            predicted = max(0.0, base_level + slope_capped * i)
            ts = anchor_ts + step * i
            points.append(MetricPoint(ts=ts, value=predicted))
            if predicted > peak:
                peak = predicted
                peak_at = ts

        predicted_values = [p.value for p in points]
        p95_predicted = robust_p95(predicted_values)

        # Confidence: inverse of in-sample MAPE of the trivial mean-baseline,
        # dampened toward 0.5 for longer horizons.
        mean_baseline = [mean_recent] * len(values)
        in_sample_mape = mape(values, mean_baseline)
        confidence = max(0.1, min(0.95, 1.0 - in_sample_mape)) * (
            0.6 + 0.4 * (1.0 / max(1.0, request.horizon_hours / 24.0))
        )

        return Forecast(
            workload=series.workload,
            metric=series.metric,
            horizon_hours=request.horizon_hours,
            points=points,
            p95_predicted=p95_predicted,
            peak_predicted=peak,
            peak_at=peak_at,
            breach_at=None,
            confidence_score=round(confidence, 4),
            model_used=ForecastModel.STATISTICAL,
            generated_at=request.now
            if request.now.tzinfo is not None
            else request.now.replace(tzinfo=UTC),
        )


def now_utc() -> datetime:
    return datetime.now(UTC)
