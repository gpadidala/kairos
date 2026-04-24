"""Domain enumerations — shared across modules."""

from __future__ import annotations

from enum import StrEnum


class WorkloadKind(StrEnum):
    """Kubernetes workload kinds PCAP manages."""

    DEPLOYMENT = "Deployment"
    STATEFULSET = "StatefulSet"
    DAEMONSET = "DaemonSet"


class Runtime(StrEnum):
    """Application runtime; drives runtime-specific PromQL queries."""

    JVM = "jvm"
    PYTHON = "python"
    GO = "go"
    DOTNET = "dotnet"
    UNKNOWN = "unknown"


class ScalingAction(StrEnum):
    """Discrete scaling actions the decision engine can emit."""

    NOOP = "noop"
    HORIZONTAL_UP = "horizontal_up"
    HORIZONTAL_DOWN = "horizontal_down"
    VERTICAL_UP = "vertical_up"
    VERTICAL_DOWN = "vertical_down"
    NODE_POOL_ADVISORY = "node_pool_advisory"
    KEDA_PRESCALE = "keda_prescale"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"


class Severity(StrEnum):
    """Severity on the generated decision / alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationChannel(StrEnum):
    """Notification sink identifiers."""

    TEAMS = "teams"
    SLACK = "slack"
    EMAIL = "email"


class ForecastModel(StrEnum):
    """Forecasting backend identifier."""

    PROPHET = "prophet"
    STATISTICAL = "statistical"
    ENSEMBLE = "ensemble"


class LLMProviderName(StrEnum):
    """LLM provider identifiers used in config + audit."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"
    CANNED = "canned"
