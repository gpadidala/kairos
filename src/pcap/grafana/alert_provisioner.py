"""Unified alerting provisioner — emits one rule per workload per metric."""

from __future__ import annotations

import hashlib
from typing import Any

from pcap.collectors.promql_library import PromQLLibrary, QueryName
from pcap.config.settings import GrafanaSettings
from pcap.domain.models import Workload
from pcap.grafana.grafana_client import GrafanaClient


def _rule_uid(workload: Workload, metric: str) -> str:
    raw = f"{workload.uid}|{metric}".encode()
    return "pcap-" + hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]


def build_cpu_rule(
    *,
    workload: Workload,
    datasource_uid: str,
    folder_uid: str,
    threshold: float,
    contact_point: str,
) -> dict[str, Any]:
    expr = PromQLLibrary.render(
        QueryName.CPU_USAGE_CORES, namespace=workload.namespace, workload=workload.name
    )
    uid = _rule_uid(workload, "cpu")
    return {
        "uid": uid,
        "title": f"PCAP CPU pressure: {workload.uid}",
        "ruleGroup": f"pcap-{workload.namespace}",
        "folderUID": folder_uid,
        "noDataState": "OK",
        "execErrState": "OK",
        "for": "10m",
        "condition": "C",
        "data": [
            {
                "refId": "A",
                "datasourceUid": datasource_uid,
                "relativeTimeRange": {"from": 600, "to": 0},
                "model": {"expr": expr, "refId": "A"},
            },
            {
                "refId": "C",
                "datasourceUid": "__expr__",
                "relativeTimeRange": {"from": 0, "to": 0},
                "model": {
                    "type": "threshold",
                    "expression": "A",
                    "refId": "C",
                    "conditions": [
                        {
                            "evaluator": {"type": "gt", "params": [threshold]},
                            "operator": {"type": "and"},
                            "type": "query",
                        }
                    ],
                },
            },
        ],
        "labels": {
            "pcap": "true",
            "workload": workload.name,
            "namespace": workload.namespace,
            "metric": "cpu",
        },
        "annotations": {
            "summary": f"CPU forecast breach for {workload.uid}",
            "runbook_url": "https://example.com/runbooks/pcap-cpu",
        },
        "notification_settings": {"contact_point": contact_point} if contact_point else None,
    }


class AlertProvisioner:
    """Upserts per-workload alert rules in Grafana."""

    def __init__(
        self,
        client: GrafanaClient,
        settings: GrafanaSettings,
        *,
        folder_uid: str,
        datasource_uid: str,
        contact_point: str = "",
        cpu_threshold: float = 0.8,
    ) -> None:
        self._client = client
        self._settings = settings
        self._folder_uid = folder_uid
        self._datasource_uid = datasource_uid
        self._contact_point = contact_point
        self._cpu_threshold = cpu_threshold

    async def ensure_rules_for(self, workload: Workload) -> list[dict[str, Any]]:
        cpu = build_cpu_rule(
            workload=workload,
            datasource_uid=self._datasource_uid,
            folder_uid=self._folder_uid,
            threshold=self._cpu_threshold,
            contact_point=self._contact_point,
        )
        # Drop the None notification_settings if empty — Grafana rejects nulls
        if not cpu.get("notification_settings"):
            cpu.pop("notification_settings", None)
        return [await self._client.upsert_alert_rule(cpu)]
