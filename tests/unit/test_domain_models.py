"""Domain model validation + dedup hash stability."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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
    MetricSeries,
    ScalingDecision,
    Workload,
)


def test_workload_validates_cpu_and_memory_quantities(sample_workload: Workload) -> None:
    assert sample_workload.uid == "Deployment/prod/payments-api"
    assert sample_workload.is_excluded is False


def test_workload_rejects_bad_cpu() -> None:
    with pytest.raises(ValidationError):
        Workload(
            name="x",
            namespace="y",
            kind=WorkloadKind.DEPLOYMENT,
            runtime=Runtime.UNKNOWN,
            current_replicas=1,
            cpu_request="five-hundred",
            mem_request="1Gi",
        )


def test_workload_rejects_bad_memory() -> None:
    with pytest.raises(ValidationError):
        Workload(
            name="x",
            namespace="y",
            kind=WorkloadKind.DEPLOYMENT,
            runtime=Runtime.UNKNOWN,
            current_replicas=1,
            cpu_request="500m",
            mem_request="huge",
        )


def test_workload_exclude_annotation(sample_workload: Workload) -> None:
    excluded = sample_workload.model_copy(update={"annotations": {"pcap.io/exclude": "true"}})
    assert excluded.is_excluded is True


def test_metric_series_duration(sample_workload: Workload, fixed_now: datetime) -> None:
    pts = [
        MetricPoint(ts=fixed_now, value=1.0),
        MetricPoint(ts=fixed_now.replace(hour=13), value=2.0),
    ]
    s = MetricSeries(workload=sample_workload, metric="cpu", points=pts, resolution_seconds=60)
    assert s.duration_seconds == 3600


def test_forecast_rejects_non_monotonic_points(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    later = fixed_now.replace(hour=13)
    with pytest.raises(ValidationError):
        Forecast(
            workload=sample_workload,
            metric="cpu",
            horizon_hours=48,
            points=[
                MetricPoint(ts=later, value=1.0),
                MetricPoint(ts=fixed_now, value=2.0),
            ],
            p95_predicted=1.0,
            peak_predicted=2.0,
            peak_at=later,
            confidence_score=0.9,
            model_used=ForecastModel.PROPHET,
            generated_at=fixed_now,
        )


def test_decision_hash_is_stable_and_excludes_time(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    d1 = ScalingDecision(
        workload=sample_workload,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="R-001",
        rationale="headroom breach",
        target_replicas=5,
        target_cpu_request=None,
        target_mem_request=None,
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="run-a",
        generated_at=fixed_now,
    )
    d2 = d1.model_copy(
        update={
            "correlation_id": "run-b",
            "generated_at": datetime(2027, 1, 1, tzinfo=UTC),
        }
    )
    assert d1.decision_hash() == d2.decision_hash()


def test_decision_hash_changes_when_action_changes(
    sample_workload: Workload, fixed_now: datetime
) -> None:
    d1 = ScalingDecision(
        workload=sample_workload,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="R-001",
        rationale="x",
        target_replicas=5,
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="c",
        generated_at=fixed_now,
    )
    d2 = d1.model_copy(update={"action": ScalingAction.HORIZONTAL_DOWN})
    assert d1.decision_hash() != d2.decision_hash()
