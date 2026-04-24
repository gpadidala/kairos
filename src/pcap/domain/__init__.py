"""Domain layer — Pydantic models, enums, exceptions. No I/O."""

from pcap.domain.enums import (
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from pcap.domain.exceptions import (
    ConfigurationError,
    DecisionError,
    DedupHit,
    ExternalServiceError,
    ForecastError,
    LLMError,
    PCAPError,
)
from pcap.domain.models import (
    Forecast,
    LLMAdvice,
    MetricPoint,
    MetricSeries,
    NotificationResult,
    PRResult,
    ScalingDecision,
    Workload,
)

__all__ = [
    "ConfigurationError",
    "DecisionError",
    "DedupHit",
    "ExternalServiceError",
    "Forecast",
    "ForecastError",
    "LLMAdvice",
    "LLMError",
    "MetricPoint",
    "MetricSeries",
    "NotificationResult",
    "PCAPError",
    "PRResult",
    "Runtime",
    "ScalingAction",
    "ScalingDecision",
    "Severity",
    "Workload",
    "WorkloadKind",
]
