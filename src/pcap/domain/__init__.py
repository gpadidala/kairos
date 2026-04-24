"""Domain layer — Pydantic models, enums, exceptions. No I/O."""

from pcap.domain.enums import (
    ApprovalStatus,
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
    GrafanaAlert,
    KedaActivity,
    LLMAdvice,
    MetricPoint,
    MetricSeries,
    NodePoolActivity,
    NotificationResult,
    PendingApproval,
    PRResult,
    ScalingDecision,
    Workload,
)

__all__ = [
    "ApprovalStatus",
    "ConfigurationError",
    "DecisionError",
    "DedupHit",
    "ExternalServiceError",
    "Forecast",
    "ForecastError",
    "GrafanaAlert",
    "KedaActivity",
    "LLMAdvice",
    "LLMError",
    "MetricPoint",
    "MetricSeries",
    "NodePoolActivity",
    "NotificationResult",
    "PCAPError",
    "PRResult",
    "PendingApproval",
    "Runtime",
    "ScalingAction",
    "ScalingDecision",
    "Severity",
    "Workload",
    "WorkloadKind",
]
