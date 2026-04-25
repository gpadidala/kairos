"""Forecasting engine — Prophet with statistical fallback. See ADR-0002."""

from kairos.forecasting.base import Forecaster, ForecastRequest
from kairos.forecasting.ensemble import EnsembleForecaster
from kairos.forecasting.prophet_forecaster import ProphetForecaster
from kairos.forecasting.statistical_forecaster import StatisticalForecaster

__all__ = [
    "EnsembleForecaster",
    "ForecastRequest",
    "Forecaster",
    "ProphetForecaster",
    "StatisticalForecaster",
]
