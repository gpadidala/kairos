"""GitHub PR creation — end-to-end with respx fixture."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import httpx
import pytest
import respx
from pydantic import SecretStr

from pcap.config.settings import GitHubSettings, RedisSettings
from pcap.domain.enums import (
    ForecastModel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from pcap.domain.models import (
    Forecast,
    MetricPoint,
    ScalingDecision,
    Workload,
)
from pcap.gitops.github_client import GitHubClient, PRCreator
from pcap.gitops.repo_layout import RepoLayout
from pcap.resilience.breakers import reset_all_breakers
from pcap.storage.dedup import DedupStore
from pcap.storage.redis_client import RedisClient

pytestmark = pytest.mark.integration


DEPLOYMENT_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments-api
  namespace: prod
spec:
  replicas: 3
  selector:
    matchLabels:
      app: payments-api
  template:
    spec:
      containers:
        - name: payments-api
          image: eclipse-temurin:21-jre
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
            limits:
              cpu: "2"
              memory: 2Gi
"""


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_breakers()


@pytest.fixture
def workload() -> Workload:
    return Workload(
        name="payments-api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
        gitops_path="apps/payments-api",
    )


@pytest.fixture
def decision(workload: Workload) -> ScalingDecision:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    fc_cpu = Forecast(
        workload=workload,
        metric="cpu",
        horizon_hours=48,
        points=[MetricPoint(ts=now + timedelta(hours=i), value=1.5) for i in range(3)],
        p95_predicted=1.8,
        peak_predicted=1.95,
        peak_at=now + timedelta(hours=2),
        confidence_score=0.85,
        model_used=ForecastModel.PROPHET,
        generated_at=now,
    )
    fc_mem = fc_cpu.model_copy(update={"metric": "mem"})
    return ScalingDecision(
        workload=workload,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="CPU_HEADROOM_BREACH",
        rationale="CPU trending up.",
        target_replicas=5,
        forecasts=[fc_cpu, fc_mem],
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="c-1",
        generated_at=now,
    )


@pytest.fixture
async def dedup() -> DedupStore:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return DedupStore(
        RedisClient(fake, RedisSettings()),
        ttl_pr=3600,
        ttl_notify=1800,
        ttl_forecast=3600,
    )


@pytest.fixture
def gh_settings() -> GitHubSettings:
    return GitHubSettings(
        token=SecretStr("ghs_test"),
        repo="acme/gitops",
        base_branch="main",
        labels=["pcap", "autoscaling"],
        reviewers=["platform-team"],
    )


@pytest.fixture
def gh_client(gh_settings: GitHubSettings) -> GitHubClient:
    http = httpx.AsyncClient(base_url="https://api.github.com")
    return GitHubClient(gh_settings, client=http)


@respx.mock
async def test_dry_run_skips_github_calls(
    decision: ScalingDecision,
    dedup: DedupStore,
    gh_client: GitHubClient,
    gh_settings: GitHubSettings,
) -> None:
    creator = PRCreator(gh_client, dedup, RepoLayout(), github_settings=gh_settings, dry_run=True)
    result = await creator.create_pr_for_decision(decision)
    assert result is not None
    assert result.dry_run is True
    assert result.dedup_hit is False
    assert result.branch.startswith("pcap/prod-payments-api-")
    # No respx routes configured — would fail if any HTTP call was made
    await gh_client.aclose()


@respx.mock
async def test_noop_decision_returns_none(
    decision: ScalingDecision,
    dedup: DedupStore,
    gh_client: GitHubClient,
    gh_settings: GitHubSettings,
) -> None:
    noop = decision.model_copy(update={"action": ScalingAction.NOOP, "target_replicas": None})
    creator = PRCreator(gh_client, dedup, RepoLayout(), github_settings=gh_settings, dry_run=False)
    result = await creator.create_pr_for_decision(noop)
    assert result is None
    await gh_client.aclose()


@respx.mock
async def test_dedup_hit_skips_side_effects(
    decision: ScalingDecision,
    dedup: DedupStore,
    gh_client: GitHubClient,
    gh_settings: GitHubSettings,
) -> None:
    # Simulate prior PR for same decision_hash
    await dedup.first_sight_pr(decision)

    creator = PRCreator(gh_client, dedup, RepoLayout(), github_settings=gh_settings, dry_run=False)
    result = await creator.create_pr_for_decision(decision)
    assert result is not None
    assert result.dedup_hit is True
    assert result.branch == "dedup"
    await gh_client.aclose()


@respx.mock
async def test_end_to_end_creates_pr(
    decision: ScalingDecision,
    dedup: DedupStore,
    gh_client: GitHubClient,
    gh_settings: GitHubSettings,
) -> None:
    base = "https://api.github.com/repos/acme/gitops"
    respx.get(f"{base}/branches/main").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "basesha"}})
    )
    respx.post(f"{base}/git/refs").mock(return_value=httpx.Response(201, json={}))

    encoded = base64.b64encode(DEPLOYMENT_YAML.encode()).decode()
    respx.get(f"{base}/contents/apps/payments-api/deployment.yaml").mock(
        return_value=httpx.Response(200, json={"content": encoded, "sha": "blobsha"})
    )
    # values.yaml and statefulset.yaml 404
    respx.get(f"{base}/contents/apps/payments-api/values.yaml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    respx.get(f"{base}/contents/apps/payments-api/statefulset.yaml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    put_route = respx.put(f"{base}/contents/apps/payments-api/deployment.yaml").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{base}/pulls").mock(
        return_value=httpx.Response(
            201,
            json={"number": 42, "html_url": "https://github.com/acme/gitops/pull/42"},
        )
    )
    respx.post(f"{base}/issues/42/labels").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{base}/pulls/42/requested_reviewers").mock(
        return_value=httpx.Response(201, json={})
    )

    creator = PRCreator(gh_client, dedup, RepoLayout(), github_settings=gh_settings, dry_run=False)
    result = await creator.create_pr_for_decision(decision)

    assert result is not None
    assert result.dry_run is False
    assert result.dedup_hit is False
    assert result.number == 42
    assert "apps/payments-api/deployment.yaml" in result.files_changed

    # Inspect the PUT payload to confirm the manifest was edited
    put_body = put_route.calls.last.request.read().decode()
    assert put_body  # body written
    await gh_client.aclose()
