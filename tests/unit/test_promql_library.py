"""PromQL library rendering + runtime metric selection."""

from __future__ import annotations

import pytest

from pcap.collectors.promql_library import PromQLLibrary, QueryName
from pcap.domain.enums import Runtime


def test_render_cpu_usage() -> None:
    q = PromQLLibrary.render(
        QueryName.CPU_USAGE_CORES, namespace="prod", workload="api", rate_window="5m"
    )
    assert "container_cpu_usage_seconds_total" in q
    assert 'namespace="prod"' in q
    assert 'pod=~"api-.*"' in q
    assert "[5m]" in q


def test_render_uses_default_rate_window_when_absent() -> None:
    q = PromQLLibrary.render(QueryName.CPU_USAGE_CORES, namespace="prod", workload="api")
    assert "[5m]" in q


def test_render_missing_placeholder_raises() -> None:
    with pytest.raises(KeyError):
        PromQLLibrary.render(QueryName.CPU_USAGE_CORES, namespace="prod")


def test_metrics_for_runtime_jvm_includes_heap() -> None:
    names = PromQLLibrary.metrics_for(Runtime.JVM)
    assert QueryName.CPU_USAGE_CORES in names
    assert QueryName.JVM_HEAP_USED in names
    assert QueryName.PY_RSS not in names


def test_metrics_for_runtime_python() -> None:
    names = PromQLLibrary.metrics_for(Runtime.PYTHON)
    assert QueryName.PY_RSS in names
    assert QueryName.JVM_HEAP_USED not in names


def test_metrics_for_runtime_go() -> None:
    names = PromQLLibrary.metrics_for(Runtime.GO)
    assert QueryName.GO_GOROUTINES in names


def test_metrics_for_runtime_dotnet() -> None:
    names = PromQLLibrary.metrics_for(Runtime.DOTNET)
    assert QueryName.DOTNET_GC_HEAP_SIZE in names


def test_metrics_for_unknown_runtime_only_base() -> None:
    names = PromQLLibrary.metrics_for(Runtime.UNKNOWN)
    assert QueryName.CPU_USAGE_CORES in names
    assert QueryName.JVM_HEAP_USED not in names


def test_keda_query_renders() -> None:
    q = PromQLLibrary.render(
        QueryName.KEDA_METRIC_VALUE, namespace="prod", scaledobject="api-scaler"
    )
    assert "keda_scaler_metrics_value" in q
    assert 'scaledobject="api-scaler"' in q


def test_all_queries_are_unique() -> None:
    names = PromQLLibrary.all_queries()
    assert len(names) == len(set(names))


def test_keda_scaler_errors_total_renders() -> None:
    q = PromQLLibrary.render(
        QueryName.KEDA_SCALER_ERRORS_TOTAL, namespace="prod", scaledobject="api-scaler"
    )
    assert "keda_scaler_errors_total" in q
    assert 'scaledobject="api-scaler"' in q


def test_keda_scaler_latency_renders() -> None:
    q = PromQLLibrary.render(QueryName.KEDA_SCALER_LATENCY_SECONDS, namespace="prod")
    assert "histogram_quantile(0.95" in q
    assert "keda_scaler_metrics_latency_seconds_bucket" in q


def test_keda_internal_loop_latency_no_args() -> None:
    q = PromQLLibrary.render(QueryName.KEDA_INTERNAL_LOOP_LATENCY)
    assert "keda_internal_scale_loop_latency_seconds_bucket" in q


def test_keda_build_info_no_args() -> None:
    q = PromQLLibrary.render(QueryName.KEDA_BUILD_INFO)
    assert q == "keda_build_info"


def test_keda_scaler_health_composite() -> None:
    q = PromQLLibrary.render(
        QueryName.KEDA_SCALER_HEALTH, namespace="prod", scaledobject="api-scaler"
    )
    assert "keda_scaler_active" in q
    assert "keda_scaler_errors_total" in q
    assert "and on(" in q


def test_full_keda_metric_set_present() -> None:
    """Every metric KEDA exposes per the official docs has a query in our library."""
    expected = {
        QueryName.KEDA_METRIC_VALUE,
        QueryName.KEDA_SCALER_ACTIVE,
        QueryName.KEDA_SCALER_ERRORS_TOTAL,
        QueryName.KEDA_SCALER_ERRORS_RATE_5M,
        QueryName.KEDA_SCALER_LATENCY_SECONDS,
        QueryName.KEDA_SCALED_OBJECT_ERRORS_TOTAL,
        QueryName.KEDA_INTERNAL_LOOP_LATENCY,
        QueryName.KEDA_RESOURCE_REGISTERED,
        QueryName.KEDA_BUILD_INFO,
    }
    available = set(PromQLLibrary.all_queries())
    missing = expected - available
    assert not missing, f"missing KEDA queries: {missing}"
