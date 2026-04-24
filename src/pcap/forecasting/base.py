"""Forecaster abstract interface + request DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from pcap.domain.models import Forecast, MetricSeries


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """Inputs to `Forecaster.predict()`."""

    series: MetricSeries
    horizon_hours: int
    resolution_seconds: int
    now: datetime


class Forecaster(ABC):
    """Abstract strategy — fits on a series and returns a Forecast."""

    name: str = "abstract"

    @abstractmethod
    def predict(self, request: ForecastRequest) -> Forecast:
        """Produce a forecast. Implementations must raise ForecastError on fit failure."""
