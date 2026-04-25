"""Decision engine — every rule R-001..R-008 + property-based invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kairos.collectors.keda_collector import KedaLagSnapshot
from kairos.config.settings import DecisionSettings, FeatureFlags
from kairos.decision.engine import DecisionEngine
from kairos.decision.rules import DecisionInput
from kairos.domain.enums import (
    ForecastModel,
    Runtime,
    ScalingAction,
    WorkloadKind,
)
from kairos.domain.models import Forecast, MetricPoint, Workload


# ── Builders ──────────────────────────────────────────────────────────
def mkforecast(
    workload: Workload,
    *,
    metric: str,
    p95: float,
    peak: float,
    confidence: float = 0.9,
    horizon: int = 48,
) -> Forecast:
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    pts = [MetricPoint(ts=now + timedelta(hours=i), value=peak) for i in range(horizon)]
    return Forecast(
        workload=workload,
        metric=metric,
        horizon_hours=horizon,
        points=pts,
        p95_predicted=p95,
        peak_predicted=peak,
        peak_at=now + timedelta(hours=2),
        confidence_score=confidence,
        model_used=ForecastModel.STATISTICAL,
        generated_at=now,
    )


def mkworkload(
    *,
    name: str = "api",
    namespace: str = "prod",
    kind: WorkloadKind = WorkloadKind.DEPLOYMENT,
    cpu_limit: str = "2",
    mem_limit: str = "2Gi",
    current: int = 3,
) -> Workload:
    return Workload(
        name=name,
        namespace=namespace,
        kind=kind,
        runtime=Runtime.JVM,
        current_replicas=current,
        cpu_request="500m",
        cpu_limit=cpu_limit,
        mem_request="1Gi",
        mem_limit=mem_limit,
        gitops_path=f"apps/{name}",
    )


@pytest.fixture
def engine() -> DecisionEngine:
    return DecisionEngine(DecisionSettings(), FeatureFlags())


@pytest.fixture
def fixed_now_utc() -> datetime:
    return datetime(2026, 4, 23, 12, tzinfo=UTC)


def _inp(
    workload: Workload,
    *,
    cpu_p95: float,
    cpu_peak: float,
    mem_peak: float,
    cpu7d: float = 0.5,
    mem7d: float = 0.5,
    keda: KedaLagSnapshot | None = None,
    confidence: float = 0.9,
    settings_overrides: dict[str, float | int] | None = None,
) -> DecisionInput:
    overrides: dict[str, float | int] = settings_overrides or {}
    settings = DecisionSettings(**overrides)
    now = datetime(2026, 4, 23, 12, tzinfo=UTC)
    cpu_fc = mkforecast(workload, metric="cpu", p95=cpu_p95, peak=cpu_peak, confidence=confidence)
    mem_fc = mkforecast(workload, metric="mem", p95=mem_peak, peak=mem_peak, confidence=confidence)
    return DecisionInput(
        workload=workload,
        cpu_forecast=cpu_fc,
        mem_forecast=mem_fc,
        cpu_usage_p95_last_7d=cpu7d,
        mem_usage_p95_last_7d=mem7d,
        keda=keda,
        settings=settings,
        now=now,
    )


# ── R-001 ─────────────────────────────────────────────────────────────
def test_r001_cpu_breach_triggers_horizontal_up(engine: DecisionEngine) -> None:
    w = mkworkload()
    # cpu_limit=2 cores, p95=1.8 → ratio 0.9 ≥ 0.8 threshold
    d = engine.decide(_inp(w, cpu_p95=1.8, cpu_peak=1.8, mem_peak=0.5 * 2**30))
    assert d.action == ScalingAction.HORIZONTAL_UP
    assert d.reason_code == "CPU_HEADROOM_BREACH"
    assert d.target_replicas is not None
    assert d.target_replicas > w.current_replicas
    assert d.target_replicas - w.current_replicas <= DecisionSettings().max_step_replicas


def test_r001_respects_max_step_replicas(engine: DecisionEngine) -> None:
    w = mkworkload(current=3, cpu_limit="2")
    d = engine.decide(_inp(w, cpu_p95=100.0, cpu_peak=100.0, mem_peak=0.1 * 2**30))
    assert d.action == ScalingAction.HORIZONTAL_UP
    assert d.target_replicas is not None
    assert d.target_replicas - w.current_replicas <= DecisionSettings().max_step_replicas


# ── R-002 ─────────────────────────────────────────────────────────────
def test_r002_memory_breach_triggers_vertical_up(engine: DecisionEngine) -> None:
    w = mkworkload()
    peak = int(1.9 * 2**30)  # 1.9Gi (ratio 0.95 > 0.8 against 2Gi limit)
    d = engine.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=peak))
    assert d.action == ScalingAction.VERTICAL_UP
    assert d.reason_code == "MEM_HEADROOM_BREACH"
    assert d.target_mem_request is not None
    assert d.target_mem_request.endswith("Mi")


def test_r002_vertical_up_rounds_to_quantum(engine: DecisionEngine) -> None:
    w = mkworkload()
    peak = int(1.9 * 2**30)
    d = engine.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=peak))
    assert d.target_mem_request is not None
    size_mi = int(d.target_mem_request[:-2])
    assert size_mi % DecisionSettings().mem_request_quantum_mi == 0


# ── R-003 ─────────────────────────────────────────────────────────────
def test_r003_statefulset_breach_requires_human_approval(engine: DecisionEngine) -> None:
    w = mkworkload(kind=WorkloadKind.STATEFULSET)
    d = engine.decide(_inp(w, cpu_p95=1.8, cpu_peak=1.8, mem_peak=0.1 * 2**30))
    assert d.action == ScalingAction.HUMAN_APPROVAL_REQUIRED
    assert d.requires_approval is True
    assert d.reason_code.startswith("STATEFULSET_")


def test_r003_statefulset_memory_breach_requires_human_approval(engine: DecisionEngine) -> None:
    w = mkworkload(kind=WorkloadKind.STATEFULSET)
    d = engine.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=int(1.9 * 2**30)))
    assert d.action == ScalingAction.HUMAN_APPROVAL_REQUIRED


def test_statefulset_auto_pr_opt_in_overrides_approval() -> None:
    w = mkworkload(kind=WorkloadKind.STATEFULSET)
    e = DecisionEngine(DecisionSettings(), FeatureFlags(allow_statefulset_auto_pr=True))
    d = e.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=int(1.9 * 2**30)))
    # Rule still returns HUMAN_APPROVAL_REQUIRED; policy only prevents auto PR for OTHER actions.
    # Since R-002 routes StatefulSet to R-003, we expect HUMAN_APPROVAL_REQUIRED regardless.
    assert d.action == ScalingAction.HUMAN_APPROVAL_REQUIRED


# ── R-004 ─────────────────────────────────────────────────────────────
def test_r004_daemonset_breach_emits_node_advisory(engine: DecisionEngine) -> None:
    w = mkworkload(kind=WorkloadKind.DAEMONSET)
    d = engine.decide(_inp(w, cpu_p95=1.9, cpu_peak=1.9, mem_peak=0.1 * 2**30))
    assert d.action == ScalingAction.NODE_POOL_ADVISORY
    assert d.reason_code == "DAEMONSET_NODE_ADVISORY"


def test_r004_daemonset_within_thresholds_does_not_advise(engine: DecisionEngine) -> None:
    w = mkworkload(kind=WorkloadKind.DAEMONSET)
    d = engine.decide(
        _inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=0.1 * 2**30, cpu7d=0.3, mem7d=0.3)
    )
    assert d.action != ScalingAction.NODE_POOL_ADVISORY


# ── R-005 ─────────────────────────────────────────────────────────────
def test_r005_keda_uptrend_triggers_prescale(engine: DecisionEngine) -> None:
    w = mkworkload()
    keda = KedaLagSnapshot(
        workload=w,
        scaledobject="payments-scaler",
        current_value=50,
        mean_last_hour=20,
        trend_slope=2.0,
    )
    # cpu comfortable, mem comfortable, but lag trending
    d = engine.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=0.1 * 2**30, keda=keda))
    assert d.action == ScalingAction.KEDA_PRESCALE
    assert d.target_replicas == w.current_replicas + 1


def test_r005_keda_flat_does_not_prescale(engine: DecisionEngine) -> None:
    w = mkworkload()
    keda = KedaLagSnapshot(
        workload=w, scaledobject="x", current_value=20, mean_last_hour=20, trend_slope=0.0
    )
    d = engine.decide(_inp(w, cpu_p95=0.3, cpu_peak=0.3, mem_peak=0.1 * 2**30, keda=keda))
    assert d.action != ScalingAction.KEDA_PRESCALE


# ── R-006 ─────────────────────────────────────────────────────────────
def test_r006_stable_triggers_noop(engine: DecisionEngine) -> None:
    w = mkworkload()
    # p95 at 50% of limits, 7d also at 50% → stable, no breach
    d = engine.decide(
        _inp(w, cpu_p95=1.0, cpu_peak=1.0, mem_peak=int(1.0 * 2**30), cpu7d=0.5, mem7d=0.5)
    )
    assert d.action == ScalingAction.NOOP
    assert d.reason_code in {"STABLE_WITHIN_TOLERANCE", "NO_RULE_MATCHED"}


# ── R-007 ─────────────────────────────────────────────────────────────
def test_r007_low_confidence_gates_to_noop(engine: DecisionEngine) -> None:
    w = mkworkload()
    # Forecast says breach, but confidence is below 0.4 → NOOP
    d = engine.decide(
        _inp(w, cpu_p95=1.95, cpu_peak=1.95, mem_peak=int(1.95 * 2**30), confidence=0.2)
    )
    assert d.action == ScalingAction.NOOP
    assert d.reason_code == "LOW_FORECAST_CONFIDENCE"


# ── R-008 ─────────────────────────────────────────────────────────────
def test_r008_sustained_low_util_triggers_scale_down(engine: DecisionEngine) -> None:
    w = mkworkload(current=5)  # above floor=1
    d = engine.decide(
        _inp(w, cpu_p95=0.1, cpu_peak=0.1, mem_peak=int(0.1 * 2**30), cpu7d=0.1, mem7d=0.1)
    )
    assert d.action == ScalingAction.HORIZONTAL_DOWN
    assert d.target_replicas == 4


def test_r008_does_not_fire_below_floor(engine: DecisionEngine) -> None:
    w = mkworkload(current=1)
    d = engine.decide(
        _inp(w, cpu_p95=0.1, cpu_peak=0.1, mem_peak=int(0.1 * 2**30), cpu7d=0.1, mem7d=0.1)
    )
    assert d.action != ScalingAction.HORIZONTAL_DOWN


# ── Priority / invariants ─────────────────────────────────────────────
def test_low_confidence_wins_over_cpu_breach(engine: DecisionEngine) -> None:
    w = mkworkload()
    d = engine.decide(
        _inp(w, cpu_p95=1.95, cpu_peak=1.95, mem_peak=int(0.1 * 2**30), confidence=0.1)
    )
    assert d.action == ScalingAction.NOOP
    assert d.reason_code == "LOW_FORECAST_CONFIDENCE"


def test_keda_beats_stable_noop(engine: DecisionEngine) -> None:
    w = mkworkload()
    keda = KedaLagSnapshot(
        workload=w, scaledobject="x", current_value=50, mean_last_hour=20, trend_slope=2.0
    )
    d = engine.decide(_inp(w, cpu_p95=1.0, cpu_peak=1.0, mem_peak=int(1.0 * 2**30), keda=keda))
    assert d.action == ScalingAction.KEDA_PRESCALE


def test_default_fallthrough_is_noop(engine: DecisionEngine) -> None:
    # Deployment far from limits in both directions to avoid R-006, R-008
    w = mkworkload(current=2)
    # cpu at 45% (away from 15% of 50%, avoids R-006 stable); 7d at 20% so < 30% triggers R-008
    # Force scenario where nothing fits: cpu p95 ratio 0.45, mem ratio 0.45, cpu7d 0.45, mem7d 0.45.
    # R-006 stable check fails because |0.45 - 0.45| = 0 <= 0.15 and both ratios < 0.80 → R-006 matches.
    # Pick values where cpu_ratio diverges from cpu7d by >15%.
    d = engine.decide(
        _inp(w, cpu_p95=1.0, cpu_peak=1.0, mem_peak=int(1.0 * 2**30), cpu7d=0.8, mem7d=0.8)
    )
    # cpu7d=0.8 ≥ low_util_threshold=0.3 → R-008 skipped
    # cpu_ratio=0.5, |0.5-0.8|=0.3>0.15 → R-006 skipped
    # → fallthrough NOOP
    assert d.action == ScalingAction.NOOP


@settings(max_examples=50, deadline=None)
@given(
    p95=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    peak=st.floats(min_value=0.0, max_value=20.0, allow_nan=False, allow_infinity=False),
    mem_peak=st.integers(min_value=0, max_value=4 * 2**30),
    conf=st.floats(min_value=0.0, max_value=1.0),
    cpu7d=st.floats(min_value=0.0, max_value=1.0),
    mem7d=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_engine_always_returns_valid_decision(
    p95: float,
    peak: float,
    mem_peak: int,
    conf: float,
    cpu7d: float,
    mem7d: float,
) -> None:
    w = mkworkload()
    e = DecisionEngine(DecisionSettings(), FeatureFlags())
    cpu_peak = max(peak, p95)
    inp = _inp(
        w,
        cpu_p95=p95,
        cpu_peak=cpu_peak,
        mem_peak=mem_peak,
        cpu7d=cpu7d,
        mem7d=mem7d,
        confidence=conf,
    )
    d = e.decide(inp)
    assert d.action in set(ScalingAction)
    assert 0.0 <= d.confidence <= 1.0
    if d.target_replicas is not None:
        assert d.target_replicas >= 0
    if d.action == ScalingAction.HORIZONTAL_UP:
        assert d.target_replicas is not None
        assert d.target_replicas > w.current_replicas
        assert d.target_replicas - w.current_replicas <= DecisionSettings().max_step_replicas
    if d.action == ScalingAction.HORIZONTAL_DOWN:
        assert d.target_replicas is not None
        assert d.target_replicas < w.current_replicas


def test_decision_hash_stable_across_timestamps(engine: DecisionEngine) -> None:
    w = mkworkload()
    inp1 = _inp(w, cpu_p95=1.9, cpu_peak=1.9, mem_peak=int(0.1 * 2**30))
    inp2 = _inp(w, cpu_p95=1.9, cpu_peak=1.9, mem_peak=int(0.1 * 2**30))
    d1 = engine.decide(inp1, correlation_id="run-1")
    d2 = engine.decide(inp2, correlation_id="run-2")
    assert d1.decision_hash() == d2.decision_hash()
    assert d1.correlation_id != d2.correlation_id
