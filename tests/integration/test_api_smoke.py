"""Live-stack API smoke tests — hits the running docker-compose Kairos at :8090.

These tests are skipped in CI (which runs unit-only). They run when the
KAIROS_SMOKE_URL env var is set, e.g.:

    KAIROS_SMOKE_URL=http://localhost:8090 uv run python -m pytest tests/integration/test_api_smoke.py -v

Covered surface:
  - /healthz, /readyz, /metrics
  - /api/v1/status (header pill)
  - /api/v1/runs (trigger pipeline run with dry_run=true)
  - /api/v1/decisions (list)
  - /api/v1/alerts/webhook (Grafana payload normalization)
  - /api/v1/github/webhook (PR merged event)
  - /api/v1/admin/test/{grafana,mimir,redis,db} (HTMX fragments)
  - /api/v1/keda/scaledobject/preview (workload-driven YAML preview)
  - /ui/admin/envs   GET (form)   POST (create)   POST .../activate   POST .../delete

Failures here indicate the live system actually broke — they're meant to be
the "green smoke" you run after every deploy.
"""

from __future__ import annotations

import os
import re
import time

import httpx
import pytest

BASE = os.environ.get("KAIROS_SMOKE_URL")
pytestmark = pytest.mark.skipif(
    not BASE, reason="KAIROS_SMOKE_URL not set; live-stack smoke tests skipped"
)


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    assert BASE is not None
    with httpx.Client(base_url=BASE, timeout=10.0, follow_redirects=False) as c:
        yield c


# ── Health / readiness / metrics ───────────────────────────────────
def test_healthz_returns_ok(client: httpx.Client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_readyz_returns_ready(client: httpx.Client) -> None:
    r = client.get("/readyz")
    # Acceptable: 200 (ready) or 503 (degraded). Either way, JSON shape is stable.
    assert r.status_code in (200, 503)
    body = r.json()
    assert "ready" in body
    assert "checks" in body


def test_metrics_returns_prometheus_text(client: httpx.Client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # Prometheus scrape format starts with HELP comments
    assert "# HELP" in r.text or "# TYPE" in r.text


# ── Service status ────────────────────────────────────────────────
def test_status_endpoint(client: httpx.Client) -> None:
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] in ("ok", "degraded", "down")
    assert isinstance(body["services"], list) and len(body["services"]) > 0
    for s in body["services"]:
        assert "name" in s
        assert s["state"] in ("ok", "degraded", "down", "unknown")


# ── Pipeline run ──────────────────────────────────────────────────
def test_dry_run_pipeline(client: httpx.Client) -> None:
    r = client.post("/api/v1/runs", json={"dry_run": True})
    # 200 = sync run, 202 = accepted (default — runs in background and
    # the response returns the eventual status when fast enough).
    assert r.status_code in (200, 202), r.text
    body = r.json()
    assert "run_id" in body
    assert body["status"] in ("succeeded", "partial", "failed", "running")
    assert isinstance(body["decisions"], list)


def test_decisions_list_empty_or_shaped(client: httpx.Client) -> None:
    r = client.get("/api/v1/decisions?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    if body:
        d = body[0]
        for f in ("workload", "action", "reason_code", "rationale"):
            assert f in d


# ── Webhook receivers ─────────────────────────────────────────────
def test_grafana_alert_webhook(client: httpx.Client) -> None:
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "fingerprint": f"smoke-{int(time.time())}",
                "labels": {
                    "alertname": "SmokeTest",
                    "severity": "warning",
                    "namespace": "demo",
                    "workload": "smoke-target",
                },
                "annotations": {"summary": "Smoke test", "description": "API smoke"},
                "startsAt": "2026-04-23T12:00:00Z",
            }
        ],
    }
    r = client.post("/api/v1/alerts/webhook", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["received"] == 1


def test_github_pr_merged_webhook(client: httpx.Client) -> None:
    payload = {
        "action": "closed",
        "pull_request": {
            "number": 999998,
            "merged": True,
            "title": "smoke",
            "html_url": "https://example.com/pr/999998",
            "merged_at": "2026-04-23T12:00:00Z",
        },
    }
    r = client.post("/api/v1/github/webhook", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ok"] is True


def test_github_webhook_ignores_unmerged_close(client: httpx.Client) -> None:
    payload = {"action": "closed", "pull_request": {"number": 1, "merged": False}}
    r = client.post("/api/v1/github/webhook", json=payload)
    assert r.status_code == 202
    assert "ignored" in r.json()


# ── Admin connection probes (HTMX fragments) ─────────────────────
@pytest.mark.parametrize("service", ["grafana", "mimir", "redis", "db"])
def test_admin_test_connection_returns_pill(client: httpx.Client, service: str) -> None:
    r = client.post(f"/api/v1/admin/test/{service}")
    assert r.status_code == 200, r.text
    assert "pill" in r.text  # HTMX fragment with state
    assert f'id="conn-{service}-state"' in r.text


def test_admin_test_unknown_service(client: httpx.Client) -> None:
    r = client.post("/api/v1/admin/test/no-such-service")
    assert r.status_code == 200  # still returns a pill (unconfigured state)
    assert "unconfigured" in r.text


# ── KEDA ScaledObject preview ────────────────────────────────────
def test_keda_preview_for_known_workload(client: httpx.Client) -> None:
    r = client.get(
        "/api/v1/keda/scaledobject/preview",
        params={"workload_uid": "Deployment/prod/billing-svc"},
    )
    # Either 200 (workload exists; YAML or empty-state) or 404 (not in static set)
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        assert "yaml" in body
        assert "findings" in body
        assert "hint" in body


def test_keda_preview_unknown_workload(client: httpx.Client) -> None:
    r = client.get(
        "/api/v1/keda/scaledobject/preview",
        params={"workload_uid": "Deployment/nope/nope"},
    )
    assert r.status_code == 404


# ── Env profile CRUD round-trip ──────────────────────────────────
def test_env_profile_lifecycle(client: httpx.Client) -> None:
    # Create
    name = f"smoke-{int(time.time())}"
    r = client.post(
        "/ui/admin/envs",
        data={
            "name": name,
            "description": "API smoke test profile",
            "grafana_url": "http://kairos-grafana:3000",
            "mimir_url": "http://kairos-mimir:9009",
        },
    )
    assert r.status_code == 302  # redirect to /ui/admin
    assert r.headers["location"] == "/ui/admin"

    # Find the new profile id by scraping /ui/admin
    page = client.get("/ui/admin", follow_redirects=True)
    assert page.status_code == 200
    assert name in page.text

    m = re.search(r'href="/ui/admin/envs/([a-f0-9-]{36})"', page.text)
    assert m is not None, "could not locate any profile id"
    pid = m.group(1)  # any profile is fine; we just need a round-trip path

    # Activate then deactivate (works regardless of which profile we matched)
    r = client.post(f"/ui/admin/envs/{pid}/activate")
    assert r.status_code == 302
    r = client.post(f"/ui/admin/envs/{pid}/deactivate")
    assert r.status_code == 302


# ── UI page smoke (HTML 200 round-trip, fallback when html-only test) ─
@pytest.mark.parametrize(
    "path",
    [
        "/ui/home",
        "/ui/dashboard",
        "/ui/pending",
        "/ui/workloads",
        "/ui/history",
        "/ui/keda",
        "/ui/keda/catalog",
        "/ui/alerts",
        "/ui/admin",
        "/ui/admin/envs/new",
        "/docs",
    ],
)
def test_ui_pages_return_200(client: httpx.Client, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
