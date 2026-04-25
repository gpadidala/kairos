"""Pure decision rules R-001..R-008.

Each rule is a pure function from (DecisionInput) → RuleResult | None.
No I/O. No randomness. No reliance on wall-clock time outside `inp.now`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kairos.collectors.keda_collector import KedaLagSnapshot
from kairos.config.settings import DecisionSettings
from kairos.domain.enums import ScalingAction, Severity, WorkloadKind
from kairos.domain.models import Forecast, Workload

RuleFn = Callable[["DecisionInput"], "RuleResult | None"]


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Everything the engine needs to evaluate rules for one workload."""

    workload: Workload
    cpu_forecast: Forecast
    mem_forecast: Forecast
    cpu_usage_p95_last_7d: float  # fraction 0..1 of the CPU limit
    mem_usage_p95_last_7d: float  # fraction 0..1 of the mem limit
    keda: KedaLagSnapshot | None
    settings: DecisionSettings
    now: datetime


@dataclass(frozen=True, slots=True)
class RuleResult:
    """What a rule returns. Target shape is computed by the engine."""

    rule_id: str
    action: ScalingAction
    reason_code: str
    rationale: str
    severity: Severity


# ── K8s quantity helpers ──────────────────────────────────────────────
def _parse_cpu(q: str | None) -> float | None:
    if q is None:
        return None
    if q.endswith("m"):
        return float(q[:-1]) / 1000.0
    return float(q)


def _parse_mem_bytes(q: str | None) -> float | None:
    if q is None:
        return None
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, mul in units.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * mul
    return float(q)


def _humanize_bytes(n: float) -> str:
    for unit in ("B", "Ki", "Mi", "Gi", "Ti"):
        if abs(n) < 1024.0 or unit == "Ti":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}Ti"  # pragma: no cover


def _human_approval_stateful(inp: DecisionInput, kind: str) -> RuleResult:
    """R-003 helper — returned by CPU / mem rules when the workload is a StatefulSet."""
    return RuleResult(
        rule_id="R-003",
        action=ScalingAction.HUMAN_APPROVAL_REQUIRED,
        reason_code=f"STATEFULSET_{kind}",
        rationale=(
            f"StatefulSet {inp.workload.namespace}/{inp.workload.name} has a "
            f"predicted {kind.lower().replace('_', ' ')}; "
            "scaling requires explicit human approval."
        ),
        severity=Severity.CRITICAL,
    )


# ── R-001: CPU headroom breach → HORIZONTAL_UP for Deployments ────────
def rule_cpu_headroom(inp: DecisionInput) -> RuleResult | None:
    limit = _parse_cpu(inp.workload.cpu_limit) or _parse_cpu(inp.workload.cpu_request)
    if limit is None or limit <= 0:
        return None
    ratio = inp.cpu_forecast.p95_predicted / limit
    if ratio < inp.settings.cpu_headroom_threshold:
        return None

    if inp.workload.kind == WorkloadKind.DEPLOYMENT:
        return RuleResult(
            rule_id="R-001",
            action=ScalingAction.HORIZONTAL_UP,
            reason_code="CPU_HEADROOM_BREACH",
            rationale=(
                f"Forecast p95 CPU {inp.cpu_forecast.p95_predicted:.2f} cores "
                f"exceeds {inp.settings.cpu_headroom_threshold * 100:.0f}% of limit "
                f"({limit:.2f} cores) within the next {inp.cpu_forecast.horizon_hours}h."
            ),
            severity=Severity.WARNING,
        )
    if inp.workload.kind == WorkloadKind.STATEFULSET:
        return _human_approval_stateful(inp, "CPU_HEADROOM_BREACH")
    return None


# ── R-002: Memory breach → VERTICAL_UP ────────────────────────────────
def rule_memory_headroom(inp: DecisionInput) -> RuleResult | None:
    limit = _parse_mem_bytes(inp.workload.mem_limit) or _parse_mem_bytes(inp.workload.mem_request)
    if limit is None or limit <= 0:
        return None
    ratio = inp.mem_forecast.peak_predicted / limit
    if ratio < inp.settings.mem_headroom_threshold:
        return None

    if inp.workload.kind == WorkloadKind.STATEFULSET:
        return _human_approval_stateful(inp, "MEM_HEADROOM_BREACH")
    if inp.workload.kind == WorkloadKind.DAEMONSET:
        # DaemonSet handled by R-004
        return None
    return RuleResult(
        rule_id="R-002",
        action=ScalingAction.VERTICAL_UP,
        reason_code="MEM_HEADROOM_BREACH",
        rationale=(
            f"Forecast peak memory {_humanize_bytes(inp.mem_forecast.peak_predicted)} "
            f"exceeds {inp.settings.mem_headroom_threshold * 100:.0f}% of limit "
            f"({_humanize_bytes(limit)}) within the next {inp.mem_forecast.horizon_hours}h."
        ),
        severity=Severity.WARNING,
    )


# ── R-004: DaemonSet breach → NODE_POOL_ADVISORY ──────────────────────
def rule_daemonset_advisory(inp: DecisionInput) -> RuleResult | None:
    if inp.workload.kind != WorkloadKind.DAEMONSET:
        return None
    cpu_lim = _parse_cpu(inp.workload.cpu_limit) or _parse_cpu(inp.workload.cpu_request)
    mem_lim = _parse_mem_bytes(inp.workload.mem_limit) or _parse_mem_bytes(inp.workload.mem_request)
    cpu_breach = (
        cpu_lim is not None
        and cpu_lim > 0
        and (inp.cpu_forecast.p95_predicted / cpu_lim) >= inp.settings.cpu_headroom_threshold
    )
    mem_breach = (
        mem_lim is not None
        and mem_lim > 0
        and (inp.mem_forecast.peak_predicted / mem_lim) >= inp.settings.mem_headroom_threshold
    )
    if not (cpu_breach or mem_breach):
        return None
    which = "CPU" if cpu_breach else "memory"
    return RuleResult(
        rule_id="R-004",
        action=ScalingAction.NODE_POOL_ADVISORY,
        reason_code="DAEMONSET_NODE_ADVISORY",
        rationale=(
            f"DaemonSet {inp.workload.name} {which} forecast exceeds threshold; "
            "consider resizing node pool SKUs or adjusting taints/tolerations."
        ),
        severity=Severity.WARNING,
    )


# ── R-005: KEDA lag sustained uptrend → KEDA_PRESCALE ─────────────────
def rule_keda_prescale(inp: DecisionInput) -> RuleResult | None:
    k = inp.keda
    if k is None or k.trend_slope <= 0:
        return None
    # Proxy "> 2 stddev over 1h" by: slope per minute >= (mean / 30), floored at 0.05.
    threshold = max(0.05, k.mean_last_hour / 30.0)
    if k.trend_slope < threshold:
        return None
    return RuleResult(
        rule_id="R-005",
        action=ScalingAction.KEDA_PRESCALE,
        reason_code="KEDA_LAG_TRENDING_UP",
        rationale=(
            f"KEDA scaler '{k.scaledobject}' on {inp.workload.uid} shows sustained "
            f"lag growth (slope {k.trend_slope:.3f}/min over mean {k.mean_last_hour:.2f}). "
            "Recommend bumping minReplicaCount to pre-scale before the queue backs up."
        ),
        severity=Severity.WARNING,
    )


# ── R-006: Stable within tolerance → NOOP ─────────────────────────────
_STABLE_DELTA = 0.15


def rule_stable_noop(inp: DecisionInput) -> RuleResult | None:
    cpu_lim = _parse_cpu(inp.workload.cpu_limit) or _parse_cpu(inp.workload.cpu_request)
    mem_lim = _parse_mem_bytes(inp.workload.mem_limit) or _parse_mem_bytes(inp.workload.mem_request)
    if cpu_lim is None or mem_lim is None:
        return None
    cpu_ratio = inp.cpu_forecast.p95_predicted / cpu_lim
    mem_ratio = inp.mem_forecast.peak_predicted / mem_lim
    if (
        abs(cpu_ratio - inp.cpu_usage_p95_last_7d) <= _STABLE_DELTA
        and abs(mem_ratio - inp.mem_usage_p95_last_7d) <= _STABLE_DELTA
        and cpu_ratio < inp.settings.cpu_headroom_threshold
        and mem_ratio < inp.settings.mem_headroom_threshold
    ):
        return RuleResult(
            rule_id="R-006",
            action=ScalingAction.NOOP,
            reason_code="STABLE_WITHIN_TOLERANCE",
            rationale="Forecast stays within ±15% of current utilization; no action needed.",
            severity=Severity.INFO,
        )
    return None


# ── R-007: Low confidence → NOOP ──────────────────────────────────────
def rule_low_confidence_noop(inp: DecisionInput) -> RuleResult | None:
    conf = min(inp.cpu_forecast.confidence_score, inp.mem_forecast.confidence_score)
    if conf >= 0.4:
        return None
    return RuleResult(
        rule_id="R-007",
        action=ScalingAction.NOOP,
        reason_code="LOW_FORECAST_CONFIDENCE",
        rationale=(
            f"Forecast confidence {conf:.2f} below gating threshold 0.40 — skipping "
            "scaling changes until more history is available."
        ),
        severity=Severity.INFO,
    )


# ── R-008: Sustained <30% 7d → HORIZONTAL_DOWN ────────────────────────
def rule_horizontal_down(inp: DecisionInput) -> RuleResult | None:
    if inp.workload.kind != WorkloadKind.DEPLOYMENT:
        return None
    if inp.workload.current_replicas <= inp.settings.min_replicas_floor:
        return None
    if (
        inp.cpu_usage_p95_last_7d >= inp.settings.low_utilization_threshold
        or inp.mem_usage_p95_last_7d >= inp.settings.low_utilization_threshold
    ):
        return None
    return RuleResult(
        rule_id="R-008",
        action=ScalingAction.HORIZONTAL_DOWN,
        reason_code="SUSTAINED_LOW_UTILIZATION",
        rationale=(
            f"Sustained utilization below {inp.settings.low_utilization_threshold * 100:.0f}% "
            f"for {inp.settings.low_utilization_days}d; safe to reduce replica count."
        ),
        severity=Severity.INFO,
    )
