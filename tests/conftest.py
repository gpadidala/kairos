"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from kairos.api.app import create_app
from kairos.config.settings import Settings, reset_settings_cache
from kairos.domain.enums import Runtime, WorkloadKind
from kairos.domain.models import Workload


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip any stray KAIROS_* env vars so tests see defaults."""
    for k in list(os.environ):
        if k.startswith("KAIROS_"):
            monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture
def sample_workload() -> Workload:
    return Workload(
        name="payments-api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        labels={"app.kubernetes.io/name": "payments-api"},
        annotations={"kairos.io/gitops-path": "apps/payments-api"},
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
        keda_scaledobject="payments-api-scaler",
        gitops_path="apps/payments-api",
    )


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
