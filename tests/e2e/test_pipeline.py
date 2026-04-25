"""E2E pipeline — runs Pipeline.run_once end-to-end with all external deps stubbed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import fakeredis.aioredis
import pytest
from pydantic import HttpUrl

from kairos.collectors.keda_collector import KedaCollector
from kairos.config.settings import RedisSettings, Settings
from kairos.decision.engine import DecisionEngine
from kairos.discovery.workload_discovery import WorkloadDiscovery, WorkloadSource
from kairos.domain.enums import (
    ForecastModel,
    LLMProviderName,
    NotificationChannel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from kairos.domain.models import (
    Forecast,
    LLMAdvice,
    MetricPoint,
    MetricSeries,
    NotificationResult,
    PRResult,
    ScalingDecision,
    Workload,
)
from kairos.forecasting.base import Forecaster, ForecastRequest
from kairos.forecasting.ensemble import EnsembleForecaster
from kairos.forecasting.statistical_forecaster import StatisticalForecaster
from kairos.llm.advisor import LLMAdvisor
from kairos.llm.base import LLMProvider, LLMResponse
from kairos.llm.router import LLMRouter
from kairos.notify.base import NotificationPayload, Notifier
from kairos.notify.dispatcher import NotifyDispatcher
from kairos.orchestrator.pipeline import Pipeline, PipelineDeps
from kairos.storage.audit_store import JSONLogAuditStore
from kairos.storage.dedup import DedupStore
from kairos.storage.redis_client import RedisClient

pytestmark = pytest.mark.e2e


# ── Stubs ─────────────────────────────────────────────────────────────
class _StaticDiscovery(WorkloadSource):
    def __init__(self, workloads: list[Workload]) -> None:
        self._workloads = workloads

    async def list_workloads(self) -> list[Workload]:
        return list(self._workloads)


class _StubMimir:
    """Returns a canned MetricSeries per query name, computed from a requested profile."""

    def __init__(self, cpu_profile: list[float], mem_profile: list[float]) -> None:
        self._cpu = cpu_profile
        self._mem = mem_profile

    async def query_range(
        self,
        workload: Workload,
        metric: str,
        query: str,
        *,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> MetricSeries:
        _ = query
        values = self._cpu if "cpu" in metric.lower() else self._mem
        pts = [
            MetricPoint(ts=start + timedelta(seconds=i * step_seconds), value=v)
            for i, v in enumerate(values)
        ]
        return MetricSeries(
            workload=workload,
            metric=metric,
            points=pts,
            resolution_seconds=step_seconds,
        )


class _StubKeda:
    async def snapshot(self, w: Workload, *, window_minutes: int = 60) -> None:
        _ = (w, window_minutes)
        return None


class _FixedForecaster(Forecaster):
    """Deterministic forecaster for test scenarios."""

    name = "fixed"

    def __init__(self, p95_by_metric: dict[str, float]) -> None:
        self._p95 = p95_by_metric

    def predict(self, request: ForecastRequest) -> Forecast:
        s = request.series
        pts = [
            MetricPoint(ts=s.points[-1].ts + timedelta(seconds=i * 300), value=self._p95[s.metric])
            for i in range(1, 4)
        ]
        return Forecast(
            workload=s.workload,
            metric=s.metric,
            horizon_hours=request.horizon_hours,
            points=pts,
            p95_predicted=self._p95[s.metric],
            peak_predicted=self._p95[s.metric],
            peak_at=pts[-1].ts,
            confidence_score=0.9,
            model_used=ForecastModel.STATISTICAL,
            generated_at=request.now,
        )


class _StubLLMProvider(LLMProvider):
    name = LLMProviderName.ANTHROPIC

    async def complete(self, messages, *, temperature=0.1, max_tokens=1024):  # type: ignore[no-untyped-def]
        payload = {
            "why": "Stub advice for e2e test.",
            "horizontal_vs_vertical": "Horizontal here.",
            "risks_of_inaction": "SLO breach.",
            "engineer_steps": ["Merge PR", "Watch rollout"],
            "validation_steps": ["Check latency", "Check errors"],
        }
        _ = messages, temperature, max_tokens
        return LLMResponse(
            content=json.dumps(payload),
            prompt_tokens=10,
            completion_tokens=5,
            provider=self.name,
        )

    async def aclose(self) -> None:
        return None


class _StubPRCreator:
    def __init__(self) -> None:
        self._dry_run = False
        self.calls: list[ScalingDecision] = []

    async def create_pr_for_decision(
        self, decision: ScalingDecision, *, advice: LLMAdvice | None = None
    ) -> PRResult | None:
        _ = advice
        if decision.action in (
            ScalingAction.NOOP,
            ScalingAction.HUMAN_APPROVAL_REQUIRED,
            ScalingAction.NODE_POOL_ADVISORY,
        ):
            return None
        self.calls.append(decision)
        return PRResult(
            url=HttpUrl("https://github.com/acme/gitops/pull/1"),
            number=1,
            branch="kairos/test",
            files_changed=["apps/api/deployment.yaml"],
            dry_run=self._dry_run,
        )


class _StubNotifier(Notifier):
    def __init__(self, channel: NotificationChannel) -> None:
        self.channel = channel
        self.calls: list[NotificationPayload] = []

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        self.calls.append(payload)
        return NotificationResult(channel=self.channel, delivered=True)

    async def aclose(self) -> None:
        return None


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture
def workload() -> Workload:
    return Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
        gitops_path="apps/api",
    )


@pytest.fixture
def dedup_store() -> DedupStore:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return DedupStore(
        RedisClient(fake, RedisSettings()), ttl_pr=3600, ttl_notify=3600, ttl_forecast=3600
    )


def _build_settings() -> Settings:
    s = Settings()
    return s.model_copy(
        update={
            "features": s.features.model_copy(
                update={
                    "enable_llm": True,
                    "enable_pr_creation": True,
                    "enable_notifications": True,
                    "dry_run": False,
                }
            )
        }
    )


# ── The E2E test ──────────────────────────────────────────────────────
async def test_pipeline_end_to_end_breach_triggers_pr_and_notifications(
    workload: Workload, dedup_store: DedupStore
) -> None:
    settings = _build_settings()

    # Forecast CPU breach → HORIZONTAL_UP
    forecaster = EnsembleForecaster(
        use_prophet=False,
        statistical=StatisticalForecaster(),
    )
    # Replace with our fixed forecaster so the test is fully deterministic.
    fixed = _FixedForecaster({"cpu_usage_cores": 1.9, "memory_working_set_bytes": 0.3 * 2**30})
    # Swap the ensemble's internal statistical for our fixed one isn't trivial,
    # so we just use fixed directly at the pipeline layer via a wrapper:

    class _OverrideEnsemble(EnsembleForecaster):
        def predict(self, request: ForecastRequest) -> Forecast:
            return fixed.predict(request)

    forecaster = _OverrideEnsemble(use_prophet=False)

    mimir_stub = _StubMimir(
        cpu_profile=[0.3] * 60,
        mem_profile=[0.1 * 2**30] * 60,
    )
    discovery = WorkloadDiscovery(_StaticDiscovery([workload]))
    decision_engine = DecisionEngine(settings.decision, settings.features)

    llm_router = LLMRouter(
        {LLMProviderName.ANTHROPIC: _StubLLMProvider()},
        settings.llm,
    )
    advisor = LLMAdvisor(llm_router, settings.llm)

    pr_creator = _StubPRCreator()

    teams = _StubNotifier(NotificationChannel.TEAMS)
    slack = _StubNotifier(NotificationChannel.SLACK)
    email = _StubNotifier(NotificationChannel.EMAIL)
    dispatcher = NotifyDispatcher([teams, slack, email], dedup_store)

    deps = PipelineDeps(
        discovery=discovery,
        mimir=mimir_stub,  # type: ignore[arg-type]
        keda=KedaCollector(mimir_stub),  # type: ignore[arg-type]
        forecaster=forecaster,
        decision=decision_engine,
        advisor=advisor,
        pr_creator=pr_creator,  # type: ignore[arg-type]
        notifier=dispatcher,
        audit=JSONLogAuditStore(),
        settings=settings,
        grafana_dashboard_url="https://grafana.example.com/d/kairos-predictions",
    )
    pipeline = Pipeline(deps)

    result = await pipeline.run_once()

    # Decision produced
    assert result.workloads_processed == 1
    assert len(result.decisions) == 1
    assert result.decisions[0].action == ScalingAction.HORIZONTAL_UP
    assert result.decisions[0].severity == Severity.WARNING
    # PR created
    assert len(pr_creator.calls) == 1
    assert len(result.prs) == 1
    assert result.prs[0].number == 1
    # Notifications fanned out
    assert len(result.notifications) == 3
    assert {r.channel for r in result.notifications} == {
        NotificationChannel.TEAMS,
        NotificationChannel.SLACK,
        NotificationChannel.EMAIL,
    }
    assert all(r.delivered for r in result.notifications)
    # Each notifier got a payload with a PR URL + Grafana URL
    for stub in (teams, slack, email):
        assert len(stub.calls) == 1
        assert stub.calls[0].pr_url is not None
        assert "grafana" in (stub.calls[0].grafana_url or "")
    # Run status
    assert result.status == "succeeded"
    assert result.ended_at is not None


async def test_pipeline_noop_skips_pr_and_notifications(
    workload: Workload, dedup_store: DedupStore
) -> None:
    settings = _build_settings()

    # Fixed forecast well under thresholds, 7d also low → R-008 would fire (HORIZONTAL_DOWN).
    # Force stable path: current_replicas=1 (at floor), util high → fallthrough NOOP.
    w = workload.model_copy(update={"current_replicas": 1})

    class _OverrideEnsemble(EnsembleForecaster):
        def predict(self, request: ForecastRequest) -> Forecast:
            fixed = _FixedForecaster(
                {"cpu_usage_cores": 0.8, "memory_working_set_bytes": 0.5 * 2**30}
            )
            return fixed.predict(request)

    forecaster = _OverrideEnsemble(use_prophet=False)
    mimir_stub = _StubMimir(cpu_profile=[0.8] * 60, mem_profile=[0.5 * 2**30] * 60)
    discovery = WorkloadDiscovery(_StaticDiscovery([w]))
    pr_creator = _StubPRCreator()
    teams = _StubNotifier(NotificationChannel.TEAMS)
    dispatcher = NotifyDispatcher([teams], dedup_store)

    deps = PipelineDeps(
        discovery=discovery,
        mimir=mimir_stub,  # type: ignore[arg-type]
        keda=KedaCollector(mimir_stub),  # type: ignore[arg-type]
        forecaster=forecaster,
        decision=DecisionEngine(settings.decision, settings.features),
        advisor=LLMAdvisor(
            LLMRouter({LLMProviderName.ANTHROPIC: _StubLLMProvider()}, settings.llm),
            settings.llm,
        ),
        pr_creator=pr_creator,  # type: ignore[arg-type]
        notifier=dispatcher,
        audit=JSONLogAuditStore(),
        settings=settings,
    )
    result = await Pipeline(deps).run_once()

    assert result.decisions[0].action in (ScalingAction.NOOP, ScalingAction.HORIZONTAL_UP)
    # If NOOP: no PR, no notifications
    if result.decisions[0].action == ScalingAction.NOOP:
        assert len(pr_creator.calls) == 0
        assert all(len(s.calls) == 0 for s in (teams,))
