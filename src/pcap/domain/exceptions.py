"""PCAP exception hierarchy. All exceptions inherit from PCAPError."""

from __future__ import annotations


class PCAPError(Exception):
    """Base for every PCAP-raised error."""


class ConfigurationError(PCAPError):
    """Raised when settings/config are invalid or missing."""


class ExternalServiceError(PCAPError):
    """Raised on non-transient external service failures (Mimir, GitHub, Grafana, LLM)."""

    def __init__(self, service: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status = status


class ForecastError(PCAPError):
    """Raised when forecasting cannot produce a usable result (used to trigger fallback)."""


class DecisionError(PCAPError):
    """Raised on internal rule-evaluation failure (should be rare — rules are pure)."""


class LLMError(ExternalServiceError):
    """LLM-specific failure (shape-mismatched output, all providers failed, etc.)."""

    def __init__(self, provider: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"llm[{provider}]", message, status=status)
        self.provider = provider


class DedupHit(PCAPError):
    """Raised (or caught internally) when a dedup key already exists — treated as non-fatal."""

    def __init__(self, kind: str, key: str) -> None:
        super().__init__(f"duplicate {kind}: {key}")
        self.kind = kind
        self.key = key


class CircuitOpenError(ExternalServiceError):
    """Raised when a circuit breaker rejects a call."""


class PolicyViolationError(PCAPError):
    """Raised when an action is blocked by a policy (e.g. auto-PR for StatefulSet)."""
