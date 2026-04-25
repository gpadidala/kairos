"""KEDA ScaledObject metrics reader. Pulls scaler values from Mimir."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from kairos.collectors.mimir_client import MimirClient
from kairos.collectors.promql_library import PromQLLibrary, QueryName
from kairos.domain.models import MetricSeries, Workload

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class KedaLagSnapshot:
    """Recent trend of KEDA scaler metrics for one workload."""

    workload: Workload
    scaledobject: str
    current_value: float
    mean_last_hour: float
    trend_slope: float  # units per minute — positive = growing queue/lag


class KedaCollector:
    """Reads KEDA scaler values from Mimir and computes lag trend."""

    def __init__(self, mimir: MimirClient) -> None:
        self._mimir = mimir

    async def snapshot(
        self, workload: Workload, *, window_minutes: int = 60
    ) -> KedaLagSnapshot | None:
        if not workload.keda_scaledobject:
            return None

        end = datetime.now(UTC)
        start = end - timedelta(minutes=window_minutes)

        query = PromQLLibrary.render(
            QueryName.KEDA_METRIC_VALUE,
            namespace=workload.namespace,
            scaledobject=workload.keda_scaledobject,
        )
        series: MetricSeries = await self._mimir.query_range(
            workload,
            metric="keda_metric_value",
            query=query,
            start=start,
            end=end,
            step_seconds=60,
        )
        if len(series.points) < 2:
            return None

        values = [p.value for p in series.points]
        current = values[-1]
        mean = sum(values) / len(values)
        # Simple slope: (last - first) / minutes
        slope = (values[-1] - values[0]) / max(1.0, window_minutes)

        return KedaLagSnapshot(
            workload=workload,
            scaledobject=workload.keda_scaledobject,
            current_value=current,
            mean_last_hour=mean,
            trend_slope=slope,
        )
