"""Self-observability — Prometheus metrics + OpenTelemetry tracing."""

from pcap.observability.metrics import (
    CIRCUIT_BREAKER_STATE,
    DECISIONS_TOTAL,
    DEDUP_HITS_TOTAL,
    EXTERNAL_CALL_DURATION,
    FORECASTS_GENERATED,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_TOTAL,
    NOTIFICATIONS_SENT,
    PIPELINE_DURATION,
    PIPELINE_RUNS_TOTAL,
    PRS_CREATED_TOTAL,
    REGISTRY,
)
from pcap.observability.tracing import configure_tracing, get_tracer

__all__ = [
    "CIRCUIT_BREAKER_STATE",
    "DECISIONS_TOTAL",
    "DEDUP_HITS_TOTAL",
    "EXTERNAL_CALL_DURATION",
    "FORECASTS_GENERATED",
    "LLM_CALLS_TOTAL",
    "LLM_TOKENS_TOTAL",
    "NOTIFICATIONS_SENT",
    "PIPELINE_DURATION",
    "PIPELINE_RUNS_TOTAL",
    "PRS_CREATED_TOTAL",
    "REGISTRY",
    "configure_tracing",
    "get_tracer",
]
