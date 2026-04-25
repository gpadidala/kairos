"""Grafana client integration tests — folder + dashboard + alert rule upserts."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from kairos.config.settings import GrafanaSettings
from kairos.domain.enums import Runtime, WorkloadKind
from kairos.domain.models import Workload
from kairos.grafana.alert_provisioner import AlertProvisioner
from kairos.grafana.dashboard_builder import build_predictions_dashboard
from kairos.grafana.grafana_client import GrafanaClient
from kairos.resilience.breakers import reset_all_breakers

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_breakers()


@pytest.fixture
def gf_settings() -> GrafanaSettings:
    return GrafanaSettings(url="http://grafana.test/", api_token=SecretStr("glsa_test"))


@pytest.fixture
def gf_client(gf_settings: GrafanaSettings) -> GrafanaClient:
    return GrafanaClient(
        gf_settings,
        client=httpx.AsyncClient(base_url="http://grafana.test"),
    )


@respx.mock
async def test_ensure_folder_returns_existing_uid(gf_client: GrafanaClient) -> None:
    respx.get("http://grafana.test/api/folders").mock(
        return_value=httpx.Response(200, json=[{"uid": "f-123", "title": "KAIROS"}])
    )
    uid = await gf_client.ensure_folder("KAIROS")
    assert uid == "f-123"
    await gf_client.aclose()


@respx.mock
async def test_ensure_folder_creates_when_absent(gf_client: GrafanaClient) -> None:
    respx.get("http://grafana.test/api/folders").mock(return_value=httpx.Response(200, json=[]))
    respx.post("http://grafana.test/api/folders").mock(
        return_value=httpx.Response(200, json={"uid": "new-uid"})
    )
    uid = await gf_client.ensure_folder("KAIROS")
    assert uid == "new-uid"
    await gf_client.aclose()


@respx.mock
async def test_upsert_dashboard(gf_client: GrafanaClient) -> None:
    route = respx.post("http://grafana.test/api/dashboards/db").mock(
        return_value=httpx.Response(200, json={"uid": "dash-1", "version": 2})
    )
    dashboard = build_predictions_dashboard(datasource_uid="mimir-uid")
    result = await gf_client.upsert_dashboard(dashboard, folder_uid="f-1")
    assert result["uid"] == "dash-1"
    body = route.calls.last.request.read().decode()
    assert "kairos-predictions" in body
    assert "f-1" in body
    await gf_client.aclose()


@respx.mock
async def test_upsert_alert_rule_put(gf_client: GrafanaClient) -> None:
    respx.put("http://grafana.test/api/v1/provisioning/alert-rules/uid-abc").mock(
        return_value=httpx.Response(200, json={"uid": "uid-abc"})
    )
    out = await gf_client.upsert_alert_rule({"uid": "uid-abc", "title": "x", "data": []})
    assert out["uid"] == "uid-abc"
    await gf_client.aclose()


@respx.mock
async def test_upsert_alert_rule_falls_back_to_post_on_404(gf_client: GrafanaClient) -> None:
    respx.put("http://grafana.test/api/v1/provisioning/alert-rules/new-uid").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    respx.post("http://grafana.test/api/v1/provisioning/alert-rules").mock(
        return_value=httpx.Response(201, json={"uid": "new-uid"})
    )
    out = await gf_client.upsert_alert_rule({"uid": "new-uid", "title": "x", "data": []})
    assert out["uid"] == "new-uid"
    await gf_client.aclose()


@respx.mock
async def test_alert_provisioner_emits_cpu_rule(
    gf_client: GrafanaClient, gf_settings: GrafanaSettings
) -> None:
    route = respx.put(
        url__startswith="http://grafana.test/api/v1/provisioning/alert-rules/kairos-"
    ).mock(return_value=httpx.Response(200, json={"uid": "ok"}))

    w = Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
    )
    prov = AlertProvisioner(
        gf_client,
        gf_settings,
        folder_uid="kairos-folder",
        datasource_uid="mimir-uid",
        contact_point="kairos-oncall",
        cpu_threshold=0.8,
    )
    await prov.ensure_rules_for(w)

    assert route.called
    body = route.calls.last.request.read().decode()
    assert "KAIROS CPU pressure" in body
    assert "kairos-folder" in body
    assert "mimir-uid" in body
    await gf_client.aclose()
