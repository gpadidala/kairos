"""Deterministic monthly cost estimator for scaling decisions.

Cost model (linear, separable):
  hourly_pod_cost = cpu_cores * cpu_per_hour + mem_gib * mem_per_gib_per_hour
  monthly_pod_cost = hourly_pod_cost * hours_per_month
  monthly_workload_cost = monthly_pod_cost * replicas

This is a *list-price* approximation good enough to give reviewers a rounded
$/month expectation. It deliberately ignores:
  - Spot / RI / SP discounts (apply at the cluster boundary, not the pod)
  - Node bin-packing inefficiency (the pod still costs what it requests)
  - GPU / specialty SKUs (callers pass custom rates per env profile)
  - Egress + storage (pod-shape changes don't move those needles)

The defaults target Azure AKS Standard_D8s_v5 list price as a sane baseline
across hyperscalers. Override per-env via EnvironmentProfile rates.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kairos.domain.enums import ScalingAction
from kairos.domain.models import CostImpact, ScalingDecision


class CostRates(BaseModel):
    """Cost rates for one environment, in a single currency.

    Defaults reflect roughly-pooled hyperscaler list prices for a general-purpose
    on-demand SKU; override per env profile (cheaper Spot in nonprod, premium
    SKUs in prod, etc.).
    """

    cpu_per_hour: float = Field(default=0.0400, gt=0, description="$/vCPU/hour (list)")
    mem_gib_per_hour: float = Field(default=0.0050, gt=0, description="$/GiB/hour (list)")
    currency: str = Field(default="USD", min_length=1, max_length=8)
    hours_per_month: float = Field(default=730.0, gt=0)


def _bytes_to_gib(b: float) -> float:
    return b / (1024.0**3)


def estimate_workload_cost(
    *, replicas: int, cpu_cores: float, mem_gib: float, rates: CostRates
) -> float:
    """Monthly $ for a workload with the given pod shape + replica count."""
    if replicas <= 0:
        return 0.0
    pod_hourly = cpu_cores * rates.cpu_per_hour + mem_gib * rates.mem_gib_per_hour
    return max(0.0, pod_hourly * rates.hours_per_month * replicas)


def _resolved_pod_shape(
    *, cpu_request: str, mem_request: str, target_cpu: str | None, target_mem: str | None
) -> tuple[float, float, float, float]:
    """Return (current_cpu_cores, current_mem_gib, projected_cpu_cores, projected_mem_gib)."""
    # Lazy import — avoids `engine → estimator → rules → engine` circular import.
    from kairos.decision.rules import _parse_cpu, _parse_mem_bytes  # noqa: PLC0415

    cur_cpu = _parse_cpu(cpu_request) or 0.0
    cur_mem_bytes = _parse_mem_bytes(mem_request) or 0.0
    new_cpu = _parse_cpu(target_cpu) if target_cpu else cur_cpu
    new_mem_bytes = _parse_mem_bytes(target_mem) if target_mem else cur_mem_bytes
    return (
        cur_cpu,
        _bytes_to_gib(cur_mem_bytes),
        new_cpu or cur_cpu,
        _bytes_to_gib(new_mem_bytes or cur_mem_bytes),
    )


def estimate_decision_cost(
    decision: ScalingDecision, rates: CostRates | None = None
) -> CostImpact:
    """Compute the monthly $ delta this decision would cause if applied.

    Handles all action types:
      - HORIZONTAL_UP / HORIZONTAL_DOWN / KEDA_PRESCALE: replica delta only
      - VERTICAL_UP / VERTICAL_DOWN: per-pod shape delta only
      - NOOP / ADVISORY / HUMAN_APPROVAL_REQUIRED: zero delta
    """
    rates_used = rates or CostRates()
    wl = decision.workload

    cur_replicas = wl.current_replicas
    new_replicas = (
        decision.target_replicas if decision.target_replicas is not None else cur_replicas
    )

    cur_cpu, cur_mem_gib, new_cpu, new_mem_gib = _resolved_pod_shape(
        cpu_request=wl.cpu_request,
        mem_request=wl.mem_request,
        target_cpu=decision.target_cpu_request,
        target_mem=decision.target_mem_request,
    )

    current_monthly = estimate_workload_cost(
        replicas=cur_replicas, cpu_cores=cur_cpu, mem_gib=cur_mem_gib, rates=rates_used
    )
    projected_monthly = estimate_workload_cost(
        replicas=new_replicas, cpu_cores=new_cpu, mem_gib=new_mem_gib, rates=rates_used
    )
    delta = projected_monthly - current_monthly
    pct = (delta / current_monthly * 100.0) if current_monthly > 0 else 0.0

    if decision.action in (ScalingAction.NOOP, ScalingAction.NODE_POOL_ADVISORY):
        # Advisory / NOOP — surface zero so the UI can render "no impact".
        delta = 0.0
        projected_monthly = current_monthly
        pct = 0.0

    direction: str = "flat"
    if abs(delta) >= 0.01:
        direction = "up" if delta > 0 else "down"

    cpu_share = (
        max(new_cpu * new_replicas, cur_cpu * cur_replicas)
        * rates_used.cpu_per_hour
        * rates_used.hours_per_month
    )
    mem_share = (
        max(new_mem_gib * new_replicas, cur_mem_gib * cur_replicas)
        * rates_used.mem_gib_per_hour
        * rates_used.hours_per_month
    )

    return CostImpact(
        currency=rates_used.currency,
        current_monthly=round(current_monthly, 2),
        projected_monthly=round(projected_monthly, 2),
        delta_monthly=round(delta, 2),
        delta_percent=round(pct, 2),
        direction=direction,  # type: ignore[arg-type]
        cpu_share_monthly=round(cpu_share, 2),
        mem_share_monthly=round(mem_share, 2),
        cpu_per_hour=rates_used.cpu_per_hour,
        mem_gib_per_hour=rates_used.mem_gib_per_hour,
    )
