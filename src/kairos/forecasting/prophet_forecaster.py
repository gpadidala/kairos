"""Prophet-backed forecaster. Optional import; fallback-ready."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from kairos.domain.enums import ForecastModel
from kairos.domain.exceptions import ForecastError
from kairos.domain.models import Forecast, MetricPoint, MetricSeries
from kairos.forecasting.base import Forecaster, ForecastRequest

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd  # type: ignore[import-untyped]


try:
    from prophet import Prophet

    PROPHET_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROPHET_AVAILABLE = False


MIN_DAYS_FOR_PROPHET = 14


class ProphetForecaster(Forecaster):
    """Prophet-based forecaster. Raises ForecastError on any unusable condition."""

    name = "prophet"

    def __init__(self, daily: bool = True, weekly: bool = True) -> None:
        self._daily = daily
        self._weekly = weekly

    def predict(self, request: ForecastRequest) -> Forecast:
        if not PROPHET_AVAILABLE:
            raise ForecastError("prophet not installed")

        series = request.series
        if series.duration_seconds < MIN_DAYS_FOR_PROPHET * 86_400:
            raise ForecastError(
                f"series duration {series.duration_seconds}s "
                f"< minimum {MIN_DAYS_FOR_PROPHET}d for Prophet"
            )

        df = _series_to_prophet_df(series)
        try:
            model = Prophet(
                daily_seasonality=self._daily,
                weekly_seasonality=self._weekly,
                yearly_seasonality=False,
                interval_width=0.8,
            )
            model.fit(df)
        except Exception as exc:
            raise ForecastError(f"prophet fit failed: {exc}") from exc

        total_steps = max(1, int((request.horizon_hours * 3600) / request.resolution_seconds))
        future = model.make_future_dataframe(
            periods=total_steps,
            freq=f"{request.resolution_seconds}s",
            include_history=False,
        )
        forecast_df = model.predict(future)

        points: list[MetricPoint] = []
        peak_value = -float("inf")
        peak_at = series.points[-1].ts

        yhat = forecast_df["yhat"].tolist()
        yhat_lower = forecast_df.get("yhat_lower", forecast_df["yhat"]).tolist()
        yhat_upper = forecast_df.get("yhat_upper", forecast_df["yhat"]).tolist()
        timestamps = forecast_df["ds"].tolist()

        for ts, y in zip(timestamps, yhat, strict=True):
            py_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if py_ts.tzinfo is None:
                py_ts = py_ts.replace(tzinfo=UTC)
            val = max(0.0, float(y))
            points.append(MetricPoint(ts=py_ts, value=val))
            if val > peak_value:
                peak_value = val
                peak_at = py_ts

        predicted_vals = [p.value for p in points]
        p95_predicted = (
            float(sorted(predicted_vals)[int(0.95 * (len(predicted_vals) - 1))])
            if predicted_vals
            else 0.0
        )

        # Confidence from interval width: narrower = more confident.
        interval_widths = [abs(u - lw) for u, lw in zip(yhat_upper, yhat_lower, strict=True)]
        mean_width = sum(interval_widths) / max(1, len(interval_widths))
        mean_signal = sum(yhat) / max(1, len(yhat))
        rel_width = mean_width / max(1e-9, abs(mean_signal))
        # Degenerate if width > 2x signal — flag low confidence.
        if rel_width > 2.0:
            raise ForecastError(f"prophet produced degenerate fit (rel_width={rel_width:.2f})")
        confidence = max(0.1, min(0.95, 1.0 - min(1.0, rel_width / 2.0)))

        return Forecast(
            workload=series.workload,
            metric=series.metric,
            horizon_hours=request.horizon_hours,
            points=points,
            p95_predicted=max(0.0, p95_predicted),
            peak_predicted=max(0.0, peak_value),
            peak_at=peak_at,
            breach_at=None,
            confidence_score=round(confidence, 4),
            model_used=ForecastModel.PROPHET,
            generated_at=request.now
            if request.now.tzinfo is not None
            else request.now.replace(tzinfo=UTC),
        )


def _series_to_prophet_df(series: MetricSeries) -> pd.DataFrame:  # pragma: no cover
    """Local helper — import pandas lazily; requires Prophet to be present."""
    import pandas as pd  # noqa: PLC0415

    rows = [
        {
            "ds": p.ts.replace(tzinfo=None) if p.ts.tzinfo is not None else p.ts,
            "y": p.value,
        }
        for p in series.points
    ]
    return pd.DataFrame(rows)


# Expose for callers that want to precheck horizon step before calling.
def compute_future_step(resolution_seconds: int) -> timedelta:
    return timedelta(seconds=resolution_seconds)
