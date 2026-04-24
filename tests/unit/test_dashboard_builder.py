"""Dashboard builder — predictions dashboard shape."""

from __future__ import annotations

import json
from pathlib import Path

from pcap.grafana.dashboard_builder import build_predictions_dashboard


def test_predictions_dashboard_has_required_fields() -> None:
    d = build_predictions_dashboard(datasource_uid="mimir-uid")
    assert d["uid"] == "pcap-predictions"
    assert d["title"] == "PCAP Predictions"
    assert d["schemaVersion"] == 39
    assert d["time"]["from"] == "now-48h"
    assert d["time"]["to"] == "now+48h"


def test_predictions_dashboard_has_template_variables() -> None:
    d = build_predictions_dashboard(datasource_uid="mimir-uid")
    var_names = [v["name"] for v in d["templating"]["list"]]
    assert "namespace" in var_names
    assert "workload" in var_names


def test_predictions_dashboard_has_expected_panels() -> None:
    d = build_predictions_dashboard(datasource_uid="mimir-uid")
    titles = [p["title"] for p in d["panels"]]
    assert any("CPU" in t for t in titles)
    assert any("Memory" in t for t in titles)
    assert any("Replicas" in t for t in titles)
    assert any("restart" in t.lower() for t in titles)


def test_platform_dashboard_json_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "deploy" / "grafana" / "dashboards" / "pcap-platform.json"
    data = json.loads(path.read_text())
    assert data["uid"] == "pcap-platform"
    assert data["schemaVersion"] >= 38
    assert len(data["panels"]) >= 4
