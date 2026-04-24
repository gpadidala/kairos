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
