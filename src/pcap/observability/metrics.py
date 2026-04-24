"""Prometheus metrics registry — exposes the self-observability counters from §10."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

# Pipeline lifecycle
PIPELINE_RUNS_TOTAL = Counter(
    "pcap_pipeline_runs_total",
    "Total pipeline runs, labeled by status.",
    labelnames=("status",),
    registry=REGISTRY,
)

PIPELINE_DURATION = Histogram(
    "pcap_pipeline_duration_seconds",
    "Pipeline phase duration.",
    labelnames=("phase",),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

# Forecasting
FORECASTS_GENERATED = Counter(
    "pcap_forecasts_generated_total",
    "Forecasts generated.",
    labelnames=("model", "workload_kind"),
    registry=REGISTRY,
)

# Decisions
DECISIONS_TOTAL = Counter(
    "pcap_decisions_total",
    "Scaling decisions emitted.",
    labelnames=("action", "severity"),
    registry=REGISTRY,
)

# Side-effects
PRS_CREATED_TOTAL = Counter(
    "pcap_prs_created_total",
    "GitOps PRs created (or deduped / dry-run).",
    labelnames=("result",),
    registry=REGISTRY,
)

NOTIFICATIONS_SENT = Counter(
    "pcap_notifications_sent_total",
    "Notifications sent per channel.",
    labelnames=("channel", "result"),
    registry=REGISTRY,
)

# LLM
LLM_CALLS_TOTAL = Counter(
    "pcap_llm_calls_total",
    "LLM completion calls.",
    labelnames=("provider", "result"),
    registry=REGISTRY,
)

LLM_TOKENS_TOTAL = Counter(
    "pcap_llm_tokens_total",
    "LLM tokens consumed.",
    labelnames=("provider", "kind"),
    registry=REGISTRY,
)

# External calls
EXTERNAL_CALL_DURATION = Histogram(
    "pcap_external_call_duration_seconds",
    "External service call duration.",
    labelnames=("service", "result"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)

# Resilience
CIRCUIT_BREAKER_STATE = Gauge(
    "pcap_circuit_breaker_state",
    "Breaker state: 0=closed 1=half_open 2=open.",
    labelnames=("service",),
    registry=REGISTRY,
)

# Dedup
DEDUP_HITS_TOTAL = Counter(
    "pcap_dedup_hits_total",
    "Dedup cache hits by kind (pr|notify|forecast).",
    labelnames=("kind",),
    registry=REGISTRY,
)
