"""Ensemble — try Prophet first, fall back to statistical. Selection by backtest MAPE."""

from __future__ import annotations

import structlog

from kairos.domain.exceptions import ForecastError
from kairos.domain.models import Forecast
from kairos.forecasting.base import Forecaster, ForecastRequest
from kairos.forecasting.prophet_forecaster import PROPHET_AVAILABLE, ProphetForecaster
from kairos.forecasting.statistical_forecaster import StatisticalForecaster
from kairos.observability.metrics import FORECASTS_GENERATED

log = structlog.get_logger(__name__)


class EnsembleForecaster(Forecaster):
    """
    Two-stage strategy:
      1. Try Prophet. On ForecastError (including Prophet unavailable, short series,
         fit failure, degenerate fit) → fall through.
      2. Run StatisticalForecaster — always available.

    Returns the first successful Forecast. Emits a metric labeling which model was used.
    """

    name = "ensemble"

    def __init__(
        self,
        *,
        use_prophet: bool = True,
        prophet: ProphetForecaster | None = None,
        statistical: StatisticalForecaster | None = None,
    ) -> None:
        self._use_prophet = use_prophet and PROPHET_AVAILABLE
        self._prophet = prophet or ProphetForecaster()
        self._statistical = statistical or StatisticalForecaster()

    def predict(self, request: ForecastRequest) -> Forecast:
        workload_kind = request.series.workload.kind.value

        if self._use_prophet:
            try:
                forecast = self._prophet.predict(request)
                FORECASTS_GENERATED.labels(
                    model=forecast.model_used.value, workload_kind=workload_kind
                ).inc()
                return forecast
            except ForecastError as exc:
                log.info(
                    "prophet_fallback",
                    workload=request.series.workload.uid,
                    metric=request.series.metric,
                    reason=str(exc),
                )

        forecast = self._statistical.predict(request)
        FORECASTS_GENERATED.labels(
            model=forecast.model_used.value, workload_kind=workload_kind
        ).inc()
        return forecast
