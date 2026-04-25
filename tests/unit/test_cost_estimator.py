"""Cost estimator — deterministic monthly $ delta per scaling decision."""

from __future__ import annotations

from datetime import UTC, datetime

from kairos.cost.estimator import (
    CostRates,
    estimate_decision_cost,
    estimate_workload_cost,
)
from kairos.domain.enums import ScalingAction, Severity, WorkloadKind
from kairos.domain.models import ScalingDecision, Workload


def _wl(*, replicas: int = 3, cpu: str = "500m", mem: str = "512Mi") -> Workload:
    return Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        current_replicas=replicas,
        cpu_request=cpu,
        mem_request=mem,
    )


def _decide(
    *,
    workload: Workload,
    action: ScalingAction = ScalingAction.HORIZONTAL_UP,
    target_replicas: int | None = None,
    target_cpu: str | None = None,
    target_mem: str | None = None,
) -> ScalingDecision:
    return ScalingDecision(
        workload=workload,
        action=action,
        reason_code="TEST",
        rationale="test",
        target_replicas=target_replicas,
        target_cpu_request=target_cpu,
        target_mem_request=target_mem,
        severity=Severity.INFO,
        confidence=0.9,
        correlation_id="cid-test",
        generated_at=datetime.now(UTC),
    )


def test_workload_cost_linear_in_replicas() -> None:
    rates = CostRates(cpu_per_hour=0.04, mem_gib_per_hour=0.005)
    one = estimate_workload_cost(replicas=1, cpu_cores=1.0, mem_gib=2.0, rates=rates)
    five = estimate_workload_cost(replicas=5, cpu_cores=1.0, mem_gib=2.0, rates=rates)
    assert abs(five - one * 5) < 0.01


def test_horizontal_up_increases_cost() -> None:
    wl = _wl(replicas=2, cpu="500m", mem="512Mi")
    dec = _decide(workload=wl, action=ScalingAction.HORIZONTAL_UP, target_replicas=4)
    impact = estimate_decision_cost(dec)
    assert impact.direction == "up"
    assert impact.delta_monthly > 0
    assert impact.projected_monthly > impact.current_monthly


def test_horizontal_down_decreases_cost() -> None:
    wl = _wl(replicas=4, cpu="500m", mem="512Mi")
    dec = _decide(workload=wl, action=ScalingAction.HORIZONTAL_DOWN, target_replicas=2)
    impact = estimate_decision_cost(dec)
    assert impact.direction == "down"
    assert impact.delta_monthly < 0


def test_noop_has_zero_delta() -> None:
    wl = _wl(replicas=3)
    dec = _decide(workload=wl, action=ScalingAction.NOOP)
    impact = estimate_decision_cost(dec)
    assert impact.direction == "flat"
    assert impact.delta_monthly == 0.0


def test_vertical_up_costs_more_per_pod() -> None:
    wl = _wl(replicas=3, cpu="500m", mem="512Mi")
    dec = _decide(
        workload=wl,
        action=ScalingAction.VERTICAL_UP,
        target_cpu="1000m",
        target_mem="1024Mi",
    )
    impact = estimate_decision_cost(dec)
    assert impact.direction == "up"
    assert impact.delta_monthly > 0


def test_zero_replicas_zero_cost() -> None:
    rates = CostRates()
    cost = estimate_workload_cost(replicas=0, cpu_cores=2.0, mem_gib=4.0, rates=rates)
    assert cost == 0.0


def test_known_dollar_value_for_dev_default() -> None:
    """Sanity-check the magnitude using AKS-ish defaults so we'd notice silly bugs.

    1 vCPU + 2 GiB pod * 1 replica * 730h/mo
       = (1 * 0.04 + 2 * 0.005) * 730 = 0.05 * 730 = $36.50/mo
    """
    rates = CostRates(cpu_per_hour=0.04, mem_gib_per_hour=0.005, hours_per_month=730.0)
    cost = estimate_workload_cost(replicas=1, cpu_cores=1.0, mem_gib=2.0, rates=rates)
    assert abs(cost - 36.50) < 0.01
