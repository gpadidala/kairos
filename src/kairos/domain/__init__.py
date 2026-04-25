"""Domain layer — Pydantic models, enums, exceptions. No I/O."""

from kairos.domain.enums import (
    ApprovalStatus,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from kairos.domain.exceptions import (
    ConfigurationError,
    DecisionError,
    DedupHit,
    ExternalServiceError,
    ForecastError,
    KairosError,
    LLMError,
)
from kairos.domain.models import (
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
    "KairosError",
    "KedaActivity",
    "LLMAdvice",
    "LLMError",
    "MetricPoint",
    "MetricSeries",
    "NodePoolActivity",
    "NotificationResult",
    "PRResult",
    "PendingApproval",
    "Runtime",
    "ScalingAction",
    "ScalingDecision",
    "Severity",
    "Workload",
    "WorkloadKind",
]
