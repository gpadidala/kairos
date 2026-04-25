"""All PromQL queries — no query strings live outside this module.

Every query accepts keyword args; missing args raise KeyError at format time.
Every query is documented in `examples/promql/queries.md`.
"""

from __future__ import annotations

from enum import StrEnum
from string import Template

from kairos.domain.enums import Runtime


class QueryName(StrEnum):
    # Generic (all runtimes)
    CPU_USAGE_CORES = "cpu_usage_cores"
    MEMORY_WORKING_SET = "memory_working_set_bytes"
    REPLICAS = "replicas"
    POD_RESTARTS = "pod_restarts"
    # JVM
    JVM_HEAP_USED = "jvm_heap_used"
    JVM_GC_PAUSE = "jvm_gc_pause_seconds"
    JVM_THREADS = "jvm_threads"
    # Python
    PY_WORKERS = "python_workers"
    PY_RSS = "python_rss"
    # Go
    GO_GOROUTINES = "go_goroutines"
    GO_HEAP_INUSE = "go_memstats_heap_inuse_bytes"
    # .NET
    DOTNET_GC_HEAP_SIZE = "dotnet_gc_heap_size"
    DOTNET_THREADPOOL = "dotnet_threadpool_thread_count"
    # KEDA — full metric set per https://keda.sh/docs/latest/integrations/prometheus/
    KEDA_METRIC_VALUE = "keda_scaler_metrics_value"
    KEDA_SCALER_ACTIVE = "keda_scaler_active"
    KEDA_SCALER_ERRORS_TOTAL = "keda_scaler_errors_total"
    KEDA_SCALER_ERRORS_RATE_5M = "keda_scaler_errors_rate_5m"
    KEDA_SCALER_LATENCY_SECONDS = "keda_scaler_metrics_latency_seconds"
    KEDA_SCALED_OBJECT_ERRORS_TOTAL = "keda_scaled_object_errors_total"
    KEDA_INTERNAL_LOOP_LATENCY = "keda_internal_scale_loop_latency_seconds"
    KEDA_RESOURCE_REGISTERED = "keda_resource_registered_total"
    KEDA_BUILD_INFO = "keda_build_info"
    # KEDA activity + node-pool panels (UI)
    KEDA_REPLICAS_ADDED_24H = "keda_replicas_added_24h"
    KEDA_SCALE_EVENTS_24H = "keda_scale_events_24h"
    KEDA_SCALER_HEALTH = "keda_scaler_health"
    NODE_POOL_SIZE = "node_pool_size"
    NODE_POOL_DELTA_24H = "node_pool_delta_24h"


_QUERIES: dict[QueryName, Template] = {
    QueryName.CPU_USAGE_CORES: Template(
        "sum(rate(container_cpu_usage_seconds_total{"
        'namespace="$namespace",pod=~"$workload-.*",container!="POD",container!=""'
        "}[$rate_window]))"
    ),
    QueryName.MEMORY_WORKING_SET: Template(
        "sum(container_memory_working_set_bytes{"
        'namespace="$namespace",pod=~"$workload-.*",container!="POD",container!=""})'
    ),
    QueryName.REPLICAS: Template(
        "sum(kube_deployment_status_replicas_available{"
        'namespace="$namespace",deployment="$workload"}) '
        "or sum(kube_statefulset_status_replicas_ready{"
        'namespace="$namespace",statefulset="$workload"}) '
        "or sum(kube_daemonset_status_number_ready{"
        'namespace="$namespace",daemonset="$workload"})'
    ),
    QueryName.POD_RESTARTS: Template(
        "sum(increase(kube_pod_container_status_restarts_total{"
        'namespace="$namespace",pod=~"$workload-.*"}[$rate_window]))'
    ),
    # JVM (Micrometer conventions)
    QueryName.JVM_HEAP_USED: Template(
        'sum(jvm_memory_used_bytes{namespace="$namespace",pod=~"$workload-.*",area="heap"})'
    ),
    QueryName.JVM_GC_PAUSE: Template(
        "sum(rate(jvm_gc_pause_seconds_sum{"
        'namespace="$namespace",pod=~"$workload-.*"}[$rate_window]))'
    ),
    QueryName.JVM_THREADS: Template(
        'sum(jvm_threads_live_threads{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    # Python (prometheus_client + gunicorn/uvicorn exporters)
    QueryName.PY_WORKERS: Template(
        'sum(python_active_workers{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    QueryName.PY_RSS: Template(
        'sum(process_resident_memory_bytes{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    # Go
    QueryName.GO_GOROUTINES: Template(
        'sum(go_goroutines{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    QueryName.GO_HEAP_INUSE: Template(
        'sum(go_memstats_heap_inuse_bytes{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    # .NET
    QueryName.DOTNET_GC_HEAP_SIZE: Template(
        'sum(dotnet_total_memory_bytes{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    QueryName.DOTNET_THREADPOOL: Template(
        'sum(dotnet_threadpool_threads_count{namespace="$namespace",pod=~"$workload-.*"})'
    ),
    # KEDA
    QueryName.KEDA_METRIC_VALUE: Template(
        'max(keda_scaler_metrics_value{namespace="$namespace",scaledobject="$scaledobject"})'
    ),
    QueryName.KEDA_SCALER_ACTIVE: Template(
        'max(keda_scaler_active{namespace="$namespace",scaledobject="$scaledobject"})'
    ),
    # Per-scaler error counter — non-zero rate suggests a flapping/broken trigger.
    QueryName.KEDA_SCALER_ERRORS_TOTAL: Template(
        'sum(keda_scaler_errors_total{namespace="$namespace",scaledobject="$scaledobject"})'
    ),
    QueryName.KEDA_SCALER_ERRORS_RATE_5M: Template(
        "sum by (namespace, scaledobject) (rate(keda_scaler_errors_total[5m]))"
    ),
    # Latency of the scaler's metric-fetch step. High latency = pre-scale risk.
    QueryName.KEDA_SCALER_LATENCY_SECONDS: Template(
        "histogram_quantile(0.95, "
        "sum by (le, namespace, scaledobject) ("
        'rate(keda_scaler_metrics_latency_seconds_bucket{namespace="$namespace"}[5m])'
        "))"
    ),
    # ScaledObject-level errors (validation, dependency lookups, status patch failures).
    QueryName.KEDA_SCALED_OBJECT_ERRORS_TOTAL: Template(
        "sum by (namespace, scaledobject) (rate(keda_scaled_object_errors_total[5m]))"
    ),
    # KEDA operator's internal scale-loop latency (p95, controller-wide).
    QueryName.KEDA_INTERNAL_LOOP_LATENCY: Template(
        "histogram_quantile(0.95, "
        "sum by (le) (rate(keda_internal_scale_loop_latency_seconds_bucket[5m]))"
        ")"
    ),
    # Number of KEDA-registered resources by kind — useful for fleet view.
    QueryName.KEDA_RESOURCE_REGISTERED: Template(
        "sum by (resource) (keda_resource_registered_total)"
    ),
    # KEDA operator build info — exposed as a constant gauge of 1.
    QueryName.KEDA_BUILD_INFO: Template("keda_build_info"),
    # Composite "scaler health": 1 when active + zero recent errors, 0 otherwise.
    QueryName.KEDA_SCALER_HEALTH: Template(
        "(max by (namespace, scaledobject) ("
        'keda_scaler_active{namespace="$namespace",scaledobject="$scaledobject"}'
        ") == 1) "
        "and on(namespace, scaledobject) "
        "(sum by (namespace, scaledobject) (rate(keda_scaler_errors_total[5m])) == 0)"
    ),
    # Across all KEDA-managed deployments, 24h replica delta.
    QueryName.KEDA_REPLICAS_ADDED_24H: Template(
        "sum by (namespace, deployment) ("
        "max_over_time(kube_deployment_status_replicas_available[24h]) - "
        "min_over_time(kube_deployment_status_replicas_available[24h])"
        ")"
    ),
    # Count KEDA scale events in the last 24h via scaler_active transitions.
    QueryName.KEDA_SCALE_EVENTS_24H: Template(
        "sum by (namespace, scaledobject) (changes(keda_scaler_active[24h]))"
    ),
    # Per-nodepool current size (AKS labels the nodes with agentpool=<pool>).
    QueryName.NODE_POOL_SIZE: Template('count by (agentpool) (kube_node_info{agentpool!=""})'),
    # Per-nodepool 24h delta (nodes added minus removed).
    QueryName.NODE_POOL_DELTA_24H: Template(
        "sum by (agentpool) ("
        'max_over_time(count by (agentpool) (kube_node_info{agentpool!=""})[24h:5m]) - '
        'min_over_time(count by (agentpool) (kube_node_info{agentpool!=""})[24h:5m])'
        ")"
    ),
}


# Per-runtime metric bundles — drives the collector fetch plan.
_RUNTIME_METRICS: dict[Runtime, tuple[QueryName, ...]] = {
    Runtime.JVM: (QueryName.JVM_HEAP_USED, QueryName.JVM_GC_PAUSE, QueryName.JVM_THREADS),
    Runtime.PYTHON: (QueryName.PY_WORKERS, QueryName.PY_RSS),
    Runtime.GO: (QueryName.GO_GOROUTINES, QueryName.GO_HEAP_INUSE),
    Runtime.DOTNET: (QueryName.DOTNET_GC_HEAP_SIZE, QueryName.DOTNET_THREADPOOL),
    Runtime.UNKNOWN: (),
}

_BASE_METRICS: tuple[QueryName, ...] = (
    QueryName.CPU_USAGE_CORES,
    QueryName.MEMORY_WORKING_SET,
    QueryName.REPLICAS,
    QueryName.POD_RESTARTS,
)


class PromQLLibrary:
    """Immutable registry of all PromQL queries. Render only here."""

    @staticmethod
    def render(name: QueryName, /, **kwargs: str) -> str:
        """Render a named query. Missing placeholder → KeyError."""
        try:
            tmpl = _QUERIES[name]
        except KeyError as exc:  # pragma: no cover
            raise KeyError(f"unknown query: {name}") from exc
        kwargs.setdefault("rate_window", "5m")
        return tmpl.substitute(**kwargs)

    @staticmethod
    def metrics_for(runtime: Runtime) -> tuple[QueryName, ...]:
        """Return base + runtime-specific metrics."""
        return _BASE_METRICS + _RUNTIME_METRICS.get(runtime, ())

    @staticmethod
    def all_queries() -> list[QueryName]:
        return list(_QUERIES.keys())
