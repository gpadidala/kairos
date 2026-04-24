"""Decision engine — pure, deterministic orchestration over the rule set."""

from __future__ import annotations

import math
import uuid

import structlog

from pcap.config.settings import DecisionSettings, FeatureFlags
from pcap.decision.policies import requires_human_approval
from pcap.decision.rules import (
    DecisionInput,
    RuleResult,
    _parse_cpu,
    _parse_mem_bytes,
    rule_cpu_headroom,
    rule_daemonset_advisory,
    rule_horizontal_down,
    rule_keda_prescale,
    rule_low_confidence_noop,
    rule_memory_headroom,
    rule_stable_noop,
)
from pcap.domain.enums import ScalingAction, Severity
from pcap.domain.exceptions import DecisionError
from pcap.domain.models import ScalingDecision
from pcap.observability.metrics import DECISIONS_TOTAL

log = structlog.get_logger(__name__)


class DecisionEngine:
    """
    Evaluates rules in priority order, computes target shapes, returns ScalingDecision.

    Priority order:
      1. R-007 Low-confidence gate → NOOP
      2. R-005 KEDA prescale (fastest-moving signal)
      3. R-004 DaemonSet advisory
      4. R-001 CPU headroom breach
      5. R-002 memory headroom breach
      6. R-008 Horizontal scale down (sustained low util)
      7. R-006 Stable NOOP
      8. Default NOOP fallthrough
    """

    def __init__(self, settings: DecisionSettings, features: FeatureFlags) -> None:
        self._settings = settings
        self._features = features

    def decide(self, inp: DecisionInput, *, correlation_id: str | None = None) -> ScalingDecision:
        cid = correlation_id or str(uuid.uuid4())

        ordered = (
            rule_low_confidence_noop,
            rule_keda_prescale,
            rule_daemonset_advisory,
            rule_cpu_headroom,
            rule_memory_headroom,
            rule_horizontal_down,
            rule_stable_noop,
        )
        for rule in ordered:
            try:
                result = rule(inp)
            except Exception as exc:  # pragma: no cover — rules are pure
                raise DecisionError(f"rule {rule.__name__} raised: {exc}") from exc
            if result is not None:
                return self._materialize(inp, result, cid)

        fallthrough = RuleResult(
            rule_id="R-000",
            action=ScalingAction.NOOP,
            reason_code="NO_RULE_MATCHED",
            rationale="No rule conditions met; no action taken.",
            severity=Severity.INFO,
        )
        return self._materialize(inp, fallthrough, cid)

    # ── Target shape computation ──────────────────────────────────────
    def _materialize(
        self, inp: DecisionInput, result: RuleResult, correlation_id: str
    ) -> ScalingDecision:
        action = result.action

        if requires_human_approval(
            inp.workload.kind,
            action,
            allow_statefulset_auto_pr=self._features.allow_statefulset_auto_pr,
        ):
            action = ScalingAction.HUMAN_APPROVAL_REQUIRED

        target_replicas = self._target_replicas(inp, action)
        target_cpu = self._target_cpu(inp, action)
        target_mem = self._target_mem(inp, action)
        confidence = min(inp.cpu_forecast.confidence_score, inp.mem_forecast.confidence_score)

        dec = ScalingDecision(
            workload=inp.workload,
            action=action,
            reason_code=result.reason_code,
            rationale=result.rationale,
            target_replicas=target_replicas,
            target_cpu_request=target_cpu,
            target_mem_request=target_mem,
            forecasts=[inp.cpu_forecast, inp.mem_forecast],
            severity=result.severity,
            confidence=confidence,
            correlation_id=correlation_id,
            generated_at=inp.now,
            requires_approval=(action == ScalingAction.HUMAN_APPROVAL_REQUIRED),
        )
        DECISIONS_TOTAL.labels(action=action.value, severity=result.severity.value).inc()
        log.info(
            "decision_emitted",
            workload=inp.workload.uid,
            action=action.value,
            rule=result.rule_id,
            reason_code=result.reason_code,
            target_replicas=target_replicas,
            target_cpu=target_cpu,
            target_mem=target_mem,
            confidence=round(confidence, 3),
        )
        return dec

    def _target_replicas(self, inp: DecisionInput, action: ScalingAction) -> int | None:
        if action == ScalingAction.HORIZONTAL_UP:
            cpu_lim = _parse_cpu(inp.workload.cpu_limit) or _parse_cpu(inp.workload.cpu_request)
            if cpu_lim is None or cpu_lim <= 0:
                return inp.workload.current_replicas + 1
            ratio = inp.cpu_forecast.p95_predicted / (cpu_lim * inp.settings.cpu_headroom_threshold)
            desired = max(
                inp.workload.current_replicas + 1,
                math.ceil(inp.workload.current_replicas * ratio),
            )
            step = desired - inp.workload.current_replicas
            capped_step = min(step, inp.settings.max_step_replicas)
            return inp.workload.current_replicas + capped_step
        if action == ScalingAction.HORIZONTAL_DOWN:
            new = max(inp.settings.min_replicas_floor, inp.workload.current_replicas - 1)
            if new == inp.workload.current_replicas:
                return None
            return new
        if action == ScalingAction.KEDA_PRESCALE:
            return inp.workload.current_replicas + 1
        return None

    def _target_cpu(self, inp: DecisionInput, action: ScalingAction) -> str | None:
        if action != ScalingAction.VERTICAL_UP:
            return None
        current = _parse_cpu(inp.workload.cpu_request)
        if current is None:
            return None
        new_cores = max(current * 1.5, inp.cpu_forecast.p95_predicted * 1.2)
        new_milli = math.ceil(new_cores * 1000)
        quantum = inp.settings.cpu_request_quantum_m
        rounded = ((new_milli + quantum - 1) // quantum) * quantum
        return f"{rounded}m"

    def _target_mem(self, inp: DecisionInput, action: ScalingAction) -> str | None:
        if action != ScalingAction.VERTICAL_UP:
            return None
        current = _parse_mem_bytes(inp.workload.mem_request)
        if current is None:
            return None
        new_bytes = max(current * 1.5, inp.mem_forecast.peak_predicted * 1.2)
        new_mi = math.ceil(new_bytes / (1024 * 1024))
        quantum = inp.settings.mem_request_quantum_mi
        rounded = ((new_mi + quantum - 1) // quantum) * quantum
        return f"{rounded}Mi"


__all__ = ["DecisionEngine", "DecisionInput"]
