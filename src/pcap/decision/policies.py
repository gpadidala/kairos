"""Per-kind policies — which actions are even considered for which workload kinds."""

from __future__ import annotations

from pcap.domain.enums import ScalingAction, WorkloadKind

# Which actions may be auto-emitted for each kind (without forcing HUMAN_APPROVAL_REQUIRED).
_AUTO_ALLOWED: dict[WorkloadKind, frozenset[ScalingAction]] = {
    WorkloadKind.DEPLOYMENT: frozenset(
        {
            ScalingAction.NOOP,
            ScalingAction.HORIZONTAL_UP,
            ScalingAction.HORIZONTAL_DOWN,
            ScalingAction.VERTICAL_UP,
            ScalingAction.VERTICAL_DOWN,
            ScalingAction.KEDA_PRESCALE,
        }
    ),
    WorkloadKind.STATEFULSET: frozenset(
        {
            ScalingAction.NOOP,
            ScalingAction.HUMAN_APPROVAL_REQUIRED,
            # Auto-PR for StatefulSet guarded by features flag (§5.3)
        }
    ),
    WorkloadKind.DAEMONSET: frozenset(
        {
            ScalingAction.NOOP,
            ScalingAction.NODE_POOL_ADVISORY,
        }
    ),
}


def is_auto_allowed(kind: WorkloadKind, action: ScalingAction) -> bool:
    return action in _AUTO_ALLOWED.get(kind, frozenset())


def requires_human_approval(
    kind: WorkloadKind,
    action: ScalingAction,
    *,
    allow_statefulset_auto_pr: bool,
) -> bool:
    if kind == WorkloadKind.STATEFULSET:
        # The master spec pins StatefulSet breaches to human approval unless opt-in.
        if action in (ScalingAction.NOOP, ScalingAction.HUMAN_APPROVAL_REQUIRED):
            return False
        return not allow_statefulset_auto_pr
    return False
