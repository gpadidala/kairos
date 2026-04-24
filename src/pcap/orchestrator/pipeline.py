"""The agentic cycle: discover → collect → forecast → decide → act → audit."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from pcap.collectors.keda_collector import KedaCollector
from pcap.collectors.mimir_client import MimirClient
from pcap.collectors.promql_library import PromQLLibrary, QueryName
from pcap.config.settings import Settings
from pcap.decision.engine import DecisionEngine
from pcap.decision.rules import DecisionInput, _parse_cpu, _parse_mem_bytes
from pcap.discovery.workload_discovery import WorkloadDiscovery
from pcap.domain.enums import ScalingAction
from pcap.domain.models import (
    Forecast,
    MetricSeries,
    NotificationResult,
    PRResult,
    RunResult,
    ScalingDecision,
    Workload,
)
from pcap.forecasting.base import ForecastRequest
from pcap.forecasting.ensemble import EnsembleForecaster
from pcap.gitops.github_client import PRCreator
from pcap.llm.advisor import LLMAdvisor
from pcap.notify.base import NotificationPayload
from pcap.notify.dispatcher import NotifyDispatcher
from pcap.observability.metrics import (
    PIPELINE_DURATION,
    PIPELINE_RUNS_TOTAL,
)
from pcap.storage.approvals import ApprovalStore
from pcap.storage.audit_store import AuditStore

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PipelineDeps:
    """Dependencies injected into Pipeline. Tests substitute stubs freely."""

    discovery: WorkloadDiscovery
    mimir: MimirClient
    keda: KedaCollector
    forecaster: EnsembleForecaster
    decision: DecisionEngine
    advisor: LLMAdvisor | None
    pr_creator: PRCreator | None
    notifier: NotifyDispatcher | None
    audit: AuditStore
    settings: Settings
    approvals: ApprovalStore | None = None
    grafana_dashboard_url: str = ""
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))


class Pipeline:
    """One-cycle orchestrator. Stateless between runs; all state in Redis/Postgres."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def run_once(
        self,
        *,
        workload_filter: str | None = None,
        dry_run: bool | None = None,
    ) -> RunResult:
        run_id = str(uuid.uuid4())
        started = datetime.now(UTC)
        log.info("pipeline_run_started", run_id=run_id, workload_filter=workload_filter)

        run = RunResult(
            run_id=run_id,
            started_at=started,
            status="running",
        )
        await self._deps.audit.record_run(run)

        try:
            workloads = await self._discover(workload_filter)
            sem = self._deps.settings.scheduler.max_concurrent_workloads
            async with _bounded(sem) as limiter:
                tasks = [self._process_with_limiter(limiter, run_id, w, dry_run) for w in workloads]
                per_workload = await asyncio.gather(*tasks, return_exceptions=True)

            for w, outcome in zip(workloads, per_workload, strict=True):
                if isinstance(outcome, BaseException):
                    log.exception(
                        "workload_processing_failed",
                        workload=w.uid,
                        run_id=run_id,
                        error=str(outcome),
                    )
                    continue
                decision, pr, notifs = outcome
                run.decisions.append(decision)
                if pr is not None:
                    run.prs.append(pr)
                run.notifications.extend(notifs)

            run.workloads_processed = len(workloads)
            run.ended_at = datetime.now(UTC)
            any_errors = any(n.delivered is False and not n.dedup_hit for n in run.notifications)
            run.status = "partial" if any_errors else "succeeded"
            PIPELINE_RUNS_TOTAL.labels(status=run.status).inc()
            await self._deps.audit.record_run(run)
            log.info(
                "pipeline_run_completed",
                run_id=run_id,
                status=run.status,
                workloads=run.workloads_processed,
                decisions=len(run.decisions),
                prs=len(run.prs),
                notifications=len(run.notifications),
            )
            return run
        except Exception as exc:
            run.ended_at = datetime.now(UTC)
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            PIPELINE_RUNS_TOTAL.labels(status="failed").inc()
            await self._deps.audit.record_run(run)
            log.exception("pipeline_run_failed", run_id=run_id, error=str(exc))
            raise

    async def _discover(self, filt: str | None) -> list[Workload]:
        start = time.perf_counter()
        all_ = await self._deps.discovery.list()
        PIPELINE_DURATION.labels(phase="discover").observe(time.perf_counter() - start)
        if filt is None:
            return all_
        return [w for w in all_ if filt in (w.uid, f"{w.namespace}/{w.name}")]

    async def _process_with_limiter(
        self,
        limiter: _Limiter,
        run_id: str,
        workload: Workload,
        dry_run: bool | None,
    ) -> tuple[ScalingDecision, PRResult | None, list[NotificationResult]]:
        async with limiter:
            return await self._process_workload(run_id, workload, dry_run)

    async def _process_workload(
        self,
        run_id: str,
        workload: Workload,
        dry_run_override: bool | None,
    ) -> tuple[ScalingDecision, PRResult | None, list[NotificationResult]]:
        s = self._deps.settings
        now = datetime.now(UTC)
        lookback = timedelta(days=s.forecasting.lookback_days)

        # ── Collect ───────────────────────────────────────────────────
        collect_start = time.perf_counter()
        cpu_series, mem_series = await asyncio.gather(
            self._collect(workload, QueryName.CPU_USAGE_CORES, "cpu_usage_cores", now, lookback),
            self._collect(
                workload, QueryName.MEMORY_WORKING_SET, "memory_working_set_bytes", now, lookback
            ),
        )
        keda_snapshot = (
            await self._deps.keda.snapshot(workload) if workload.keda_scaledobject else None
        )
        PIPELINE_DURATION.labels(phase="collect").observe(time.perf_counter() - collect_start)

        # ── Forecast ──────────────────────────────────────────────────
        forecast_start = time.perf_counter()
        cpu_fc, mem_fc = await asyncio.gather(
            asyncio.to_thread(
                self._deps.forecaster.predict,
                ForecastRequest(
                    series=cpu_series,
                    horizon_hours=s.forecasting.horizon_hours,
                    resolution_seconds=s.forecasting.resolution_seconds,
                    now=now,
                ),
            ),
            asyncio.to_thread(
                self._deps.forecaster.predict,
                ForecastRequest(
                    series=mem_series,
                    horizon_hours=s.forecasting.horizon_hours,
                    resolution_seconds=s.forecasting.resolution_seconds,
                    now=now,
                ),
            ),
        )
        PIPELINE_DURATION.labels(phase="forecast").observe(time.perf_counter() - forecast_start)

        # ── Decide ────────────────────────────────────────────────────
        decide_start = time.perf_counter()
        cpu7d = _p95_ratio(
            cpu_series, _parse_cpu(workload.cpu_limit) or _parse_cpu(workload.cpu_request)
        )
        mem7d = _p95_ratio(
            mem_series,
            _parse_mem_bytes(workload.mem_limit) or _parse_mem_bytes(workload.mem_request),
        )

        inp = DecisionInput(
            workload=workload,
            cpu_forecast=cpu_fc,
            mem_forecast=mem_fc,
            cpu_usage_p95_last_7d=cpu7d,
            mem_usage_p95_last_7d=mem7d,
            keda=keda_snapshot,
            settings=s.decision,
            now=now,
        )
        decision = self._deps.decision.decide(inp, correlation_id=run_id)
        PIPELINE_DURATION.labels(phase="decide").observe(time.perf_counter() - decide_start)
        await self._deps.audit.record_decision(run_id, decision)

        # ── Act (PR + notifications) ──────────────────────────────────
        act_start = time.perf_counter()
        pr_result: PRResult | None = None
        notif_results: list[NotificationResult] = []

        dry_run = dry_run_override if dry_run_override is not None else s.features.dry_run

        if decision.action not in (
            ScalingAction.NOOP,
            ScalingAction.HUMAN_APPROVAL_REQUIRED,
            ScalingAction.NODE_POOL_ADVISORY,
        ):
            advice = (
                await self._deps.advisor.explain(decision)
                if self._deps.advisor is not None and s.features.enable_llm
                else None
            )

            # Pre-PR gating: when require_ui_approval is True, enqueue the
            # decision for human review instead of opening a PR immediately.
            if s.features.require_ui_approval and self._deps.approvals is not None:
                await self._deps.approvals.enqueue(decision, advice=advice)
                log.info(
                    "approval_enqueued",
                    workload=decision.workload.uid,
                    action=decision.action.value,
                    run_id=run_id,
                )
            elif self._deps.pr_creator is not None and s.features.enable_pr_creation:
                self._deps.pr_creator._dry_run = dry_run
                pr_result = await self._deps.pr_creator.create_pr_for_decision(
                    decision, advice=advice
                )
                if pr_result is not None:
                    await self._deps.audit.record_pr(run_id, pr_result)

            if self._deps.notifier is not None and s.features.enable_notifications:
                payload = NotificationPayload(
                    decision=decision,
                    advice=advice,
                    pr_url=str(pr_result.url) if pr_result else None,
                    grafana_url=self._deps.grafana_dashboard_url or None,
                )
                notif_results = await self._deps.notifier.fan_out(payload)
                for r in notif_results:
                    await self._deps.audit.record_notification(run_id, r)

        PIPELINE_DURATION.labels(phase="act").observe(time.perf_counter() - act_start)
        return decision, pr_result, notif_results

    async def _collect(
        self,
        workload: Workload,
        query_name: QueryName,
        metric_name: str,
        now: datetime,
        lookback: timedelta,
    ) -> MetricSeries:
        expr = PromQLLibrary.render(
            query_name, namespace=workload.namespace, workload=workload.name
        )
        return await self._deps.mimir.query_range(
            workload,
            metric=metric_name,
            query=expr,
            start=now - lookback,
            end=now,
            step_seconds=self._deps.settings.forecasting.resolution_seconds,
        )


def _p95_ratio(series: MetricSeries, limit: float | None) -> float:
    if limit is None or limit <= 0 or not series.points:
        return 0.0
    sorted_vals = sorted(p.value for p in series.points)
    idx = max(0, int(0.95 * (len(sorted_vals) - 1)))
    p95 = sorted_vals[idx]
    return max(0.0, p95 / limit)


# Small async semaphore ctx wrapper (so lint doesn't complain about Semaphore in dataclass default)
class _Limiter:
    def __init__(self, size: int) -> None:
        self._sem = asyncio.Semaphore(size)

    async def __aenter__(self) -> _Limiter:
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()


class _BoundedCtx:
    def __init__(self, size: int) -> None:
        self._limiter = _Limiter(size)

    async def __aenter__(self) -> _Limiter:
        return self._limiter

    async def __aexit__(self, *exc: object) -> None:
        return None


def _bounded(size: int) -> _BoundedCtx:
    return _BoundedCtx(size)


def _noop_forecast_placeholder(
    w: Workload, metric: str, now: datetime
) -> Forecast:  # pragma: no cover
    """Unused here; kept for future use when a workload has no history."""
    _ = (w, metric, now)
    raise NotImplementedError
