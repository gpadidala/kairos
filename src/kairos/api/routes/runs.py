"""Run trigger endpoint — invokes Pipeline.run_once end-to-end."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from kairos.domain.models import RunResult

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class RunRequest(BaseModel):
    workload: str | None = Field(default=None, description="ns/name filter; None = all")
    dry_run: bool = True


@router.post("", response_model=RunResult, status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(req: RunRequest, request: Request) -> RunResult:
    """
    Build a Pipeline from app.state dependencies and run one cycle. If any
    required dependency is missing (Mimir/discovery), return a stub run.
    """
    pipeline = await _build_pipeline(request)
    if pipeline is None:
        return _stub_run()
    try:
        raw = await pipeline.run_once(workload_filter=req.workload, dry_run=req.dry_run)
    except Exception as exc:
        log.exception("run_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"pipeline failed: {exc}"
        ) from exc
    result: RunResult = raw
    runs_cache: dict[str, RunResult] = getattr(request.app.state, "runs_cache", {})
    runs_cache[result.run_id] = result
    request.app.state.runs_cache = runs_cache
    return result


@router.get("/{run_id}", response_model=RunResult)
async def get_run(run_id: str, request: Request) -> RunResult:
    runs_cache: dict[str, RunResult] = getattr(request.app.state, "runs_cache", {})
    run = runs_cache.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return run


def _stub_run() -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        run_id=str(uuid.uuid4()),
        started_at=now,
        ended_at=now,
        status="succeeded",
        workloads_processed=0,
    )


async def _build_pipeline(request: Request) -> Any:
    """Assemble a Pipeline from app.state. Returns None if deps are missing."""
    state = request.app.state
    settings = state.settings

    try:
        from kairos.collectors.keda_collector import KedaCollector  # noqa: PLC0415
        from kairos.collectors.mimir_client import MimirClient  # noqa: PLC0415
        from kairos.decision.engine import DecisionEngine  # noqa: PLC0415
        from kairos.discovery.workload_discovery import WorkloadDiscovery  # noqa: PLC0415
        from kairos.forecasting.ensemble import EnsembleForecaster  # noqa: PLC0415
        from kairos.orchestrator.pipeline import Pipeline, PipelineDeps  # noqa: PLC0415
        from kairos.storage.audit_store import JSONLogAuditStore  # noqa: PLC0415
    except Exception as exc:
        log.exception("pipeline_import_failed", error=str(exc))
        return None

    # Discovery — required. If k8s API mode but not in-cluster, bail.
    try:
        discovery = WorkloadDiscovery.from_settings(settings.k8s)
    except Exception as exc:
        log.warning("discovery_unavailable", error=str(exc))
        return None

    mimir = MimirClient(settings.mimir)
    keda = KedaCollector(mimir)
    from kairos.forecasting.prophet_forecaster import ProphetForecaster  # noqa: PLC0415

    prophet = ProphetForecaster(
        weekly=settings.forecasting.weekly_seasonality,
        monthly=settings.forecasting.monthly_seasonality,
        yearly_min_days=settings.forecasting.yearly_seasonality_min_days,
        holiday_calendars=settings.forecasting.holiday_calendars,
    )
    forecaster = EnsembleForecaster(
        use_prophet=settings.forecasting.use_prophet_if_available,
        prophet=prophet,
    )
    from kairos.cost.estimator import CostRates  # noqa: PLC0415

    cost_rates = CostRates(
        cpu_per_hour=settings.cost.cpu_per_hour,
        mem_gib_per_hour=settings.cost.mem_gib_per_hour,
        currency=settings.cost.currency,
        hours_per_month=settings.cost.hours_per_month,
    )
    engine = DecisionEngine(settings.decision, settings.features, cost_rates=cost_rates)

    # Audit: prefer SQL store when the DB bootstrapped successfully.
    audit = getattr(state, "sql_audit", None) or JSONLogAuditStore()

    # Build a notifier dispatcher when CloudEvents emission is enabled and a
    # webhook URL is configured. The standard Email/Slack/Teams notifiers stay
    # off the pipeline path here; they're attached separately when configured.
    notifier_dispatcher = None
    if (
        settings.features.emit_cloudevents
        and settings.cloudevents.webhook_url is not None
    ):
        from kairos.notify.cloudevents_notifier import CloudEventsNotifier  # noqa: PLC0415
        from kairos.notify.dispatcher import NotifyDispatcher  # noqa: PLC0415
        from kairos.storage.dedup import DedupStore  # noqa: PLC0415
        from kairos.storage.redis_client import RedisClient  # noqa: PLC0415

        try:
            redis_client = RedisClient.from_settings(settings.redis)
            dedup = DedupStore(
                redis_client,
                ttl_pr=settings.redis.dedup_ttl_pr_seconds,
                ttl_notify=settings.redis.dedup_ttl_notify_seconds,
                ttl_forecast=settings.redis.dedup_ttl_forecast_seconds,
            )
            notifier_dispatcher = NotifyDispatcher(
                [CloudEventsNotifier(settings.cloudevents)], dedup
            )
        except Exception as exc:
            log.warning("cloudevents_notifier_unavailable", error=str(exc))

    deps = PipelineDeps(
        discovery=discovery,
        mimir=mimir,
        keda=keda,
        forecaster=forecaster,
        decision=engine,
        advisor=None,
        pr_creator=getattr(state, "pr_creator", None),
        notifier=notifier_dispatcher,
        audit=audit,
        settings=settings,
        approvals=getattr(state, "approvals", None),
    )
    # Keep a reference so the mimir client gets closed eventually.
    state._pipeline_mimir = mimir
    _ = asyncio  # keep import referenced
    return Pipeline(deps)
