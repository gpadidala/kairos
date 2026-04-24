"""Build parameterized Grafana dashboards for PCAP.

Two dashboards ship:
- `pcap-platform` — PCAP's self-observability (wired from static JSON file)
- `pcap-predictions` — per-workload forecast overlay + risk window

This module builds the *predictions* dashboard JSON model programmatically
from a ScalingDecision-like context. The static platform dashboard is a JSON
file under `deploy/grafana/dashboards/pcap-platform.json`.
"""

from __future__ import annotations

from typing import Any

from pcap.collectors.promql_library import PromQLLibrary, QueryName


def _panel(
    id_: int,
    title: str,
    targets: list[dict[str, Any]],
    *,
    x: int = 0,
    y: int = 0,
    w: int = 12,
    h: int = 8,
    unit: str = "short",
) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "timeseries",
        "title": title,
        "targets": targets,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"lineInterpolation": "smooth", "fillOpacity": 10},
            },
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }


def _prom_target(datasource: str, expr: str, ref_id: str = "A") -> dict[str, Any]:
    return {
        "datasource": {"type": "prometheus", "uid": datasource},
        "expr": expr,
        "refId": ref_id,
        "legendFormat": "{{pod}}",
    }


def build_predictions_dashboard(
    *,
    datasource_uid: str,
    title: str = "PCAP Predictions",
) -> dict[str, Any]:
    """
    Per-workload dashboard with template variables for namespace + workload.
    Panels: Current CPU, Current Memory, Replica History, Pod Restarts.
    """
    cpu_expr = PromQLLibrary.render(
        QueryName.CPU_USAGE_CORES, namespace="$namespace", workload="$workload"
    )
    mem_expr = PromQLLibrary.render(
        QueryName.MEMORY_WORKING_SET, namespace="$namespace", workload="$workload"
    )
    rep_expr = PromQLLibrary.render(
        QueryName.REPLICAS, namespace="$namespace", workload="$workload"
    )
    restarts_expr = PromQLLibrary.render(
        QueryName.POD_RESTARTS, namespace="$namespace", workload="$workload"
    )

    templating = {
        "list": [
            {
                "name": "namespace",
                "type": "query",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "query": "label_values(kube_deployment_labels, namespace)",
                "refresh": 2,
                "includeAll": False,
                "multi": False,
            },
            {
                "name": "workload",
                "type": "query",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "query": 'label_values(kube_deployment_labels{namespace="$namespace"}, deployment)',
                "refresh": 2,
                "includeAll": False,
                "multi": False,
            },
        ]
    }

    panels = [
        _panel(
            1,
            "CPU usage (cores) — current",
            [_prom_target(datasource_uid, cpu_expr)],
            x=0,
            y=0,
            unit="short",
        ),
        _panel(
            2,
            "Memory working set (bytes) — current",
            [_prom_target(datasource_uid, mem_expr)],
            x=12,
            y=0,
            unit="bytes",
        ),
        _panel(
            3,
            "Replicas (ready)",
            [_prom_target(datasource_uid, rep_expr)],
            x=0,
            y=8,
            unit="short",
        ),
        _panel(
            4,
            "Pod restarts (5m increase)",
            [_prom_target(datasource_uid, restarts_expr)],
            x=12,
            y=8,
            unit="short",
        ),
    ]

    return {
        "uid": "pcap-predictions",
        "title": title,
        "schemaVersion": 39,
        "version": 0,
        "editable": True,
        "tags": ["pcap", "forecast", "capacity"],
        "timezone": "utc",
        "time": {"from": "now-48h", "to": "now+48h"},
        "refresh": "5m",
        "templating": templating,
        "panels": panels,
        "annotations": {
            "list": [
                {
                    "name": "PCAP decisions",
                    "datasource": {"type": "prometheus", "uid": datasource_uid},
                    "enable": True,
                    "iconColor": "rgba(255, 96, 96, 1)",
                    "target": {
                        "expr": "pcap_decisions_total > 0",
                        "queryType": "",
                    },
                }
            ]
        },
    }
