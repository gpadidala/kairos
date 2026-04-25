"""API health + metrics + auth."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from kairos.api.app import create_app
from kairos.config.settings import APISettings, Settings


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_metrics_exposes_prom_format(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "kairos_pipeline_runs_total" in body
    assert "kairos_decisions_total" in body


def test_correlation_id_roundtrip(client: TestClient) -> None:
    r = client.get("/healthz", headers={"X-Correlation-Id": "abc-123"})
    assert r.headers["X-Correlation-Id"] == "abc-123"


def test_auth_required_when_tokens_configured() -> None:
    token = "test-secret"
    digest = hashlib.sha256(token.encode()).hexdigest()
    s = Settings(api=APISettings(token_sha256_list=[digest]))
    c = TestClient(create_app(s))

    # No auth → 401
    r = c.get("/api/v1/workloads")
    assert r.status_code == 401

    # Wrong token → 403
    r = c.get("/api/v1/workloads", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403

    # Correct token → 200
    r = c.get("/api/v1/workloads", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == []

    # Health stays open
    r = c.get("/healthz")
    assert r.status_code == 200
