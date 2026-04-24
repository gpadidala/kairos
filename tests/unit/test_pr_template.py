"""PR title + body rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pcap.domain.enums import (
    ForecastModel,
    LLMProviderName,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from pcap.domain.models import (
    Forecast,
    LLMAdvice,
    MetricPoint,
    ScalingDecision,
    Workload,
)
from pcap.gitops.pr_template import render_pr_body, render_pr_title


def _workload() -> Workload:
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


def _decision(w: Workload) -> ScalingDecision:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    fc_cpu = Forecast(
        workload=w,
        metric="cpu_usage_cores",
        horizon_hours=48,
        points=[MetricPoint(ts=now + timedelta(hours=i), value=1.5) for i in range(3)],
        p95_predicted=1.8,
        peak_predicted=1.95,
        peak_at=now + timedelta(hours=2),
        confidence_score=0.85,
        model_used=ForecastModel.PROPHET,
        generated_at=now,
    )
    fc_mem = fc_cpu.model_copy(
        update={
            "metric": "memory_working_set_bytes",
            "p95_predicted": 1.5e9,
            "peak_predicted": 1.6e9,
            "model_used": ForecastModel.PROPHET,
        }
    )
    return ScalingDecision(
        workload=w,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="CPU_HEADROOM_BREACH",
        rationale="CPU forecast approaches limit.",
        target_replicas=5,
        forecasts=[fc_cpu, fc_mem],
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id="run-42",
        generated_at=now,
    )


def test_title_contains_kind_and_action() -> None:
    title = render_pr_title(_decision(_workload()))
    assert title == "[PCAP] Scale Deployment/payments-api in prod: horizontal_up"


def test_body_renders_summary_and_forecasts() -> None:
    d = _decision(_workload())
    body = render_pr_body(d)
    assert "horizontal_up" in body
    assert "CPU_HEADROOM_BREACH" in body
    assert "run-42" in body
    assert "cpu_usage_cores" in body
    assert "memory_working_set_bytes" in body
    assert "Replicas:** 3 → **5**" in body
    assert d.decision_hash() in body


def test_body_includes_advice_when_provided() -> None:
    d = _decision(_workload())
    advice = LLMAdvice(
        why="Traffic ramp expected at 14:00 UTC Thursdays.",
        horizontal_vs_vertical="Horizontal is safer here.",
        risks_of_inaction="Queue backs up, 5xx rises.",
        engineer_steps=["Merge this PR.", "Verify new pods are Ready."],
        validation_steps=["Check p95 latency stays <300ms", "Check error rate <0.1%"],
        provider_used=LLMProviderName.ANTHROPIC,
        tokens_used=512,
    )
    body = render_pr_body(d, advice)
    assert "Horizontal is safer here." in body
    assert "- [ ] Check p95 latency" in body
    assert "anthropic" in body


def test_body_omits_llm_block_when_advice_is_none() -> None:
    body = render_pr_body(_decision(_workload()))
    assert "LLM-generated" not in body
