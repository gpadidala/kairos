"""Forecasting engine — Prophet with statistical fallback. See ADR-0002."""

from pcap.forecasting.base import Forecaster, ForecastRequest
from pcap.forecasting.ensemble import EnsembleForecaster
from pcap.forecasting.prophet_forecaster import ProphetForecaster
from pcap.forecasting.statistical_forecaster import StatisticalForecaster

__all__ = [
    "EnsembleForecaster",
    "ForecastRequest",
    "Forecaster",
    "ProphetForecaster",
    "StatisticalForecaster",
]
