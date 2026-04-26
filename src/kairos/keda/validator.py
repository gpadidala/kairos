"""Production-best-practices linter for ScaledObjectSpec.

Codifies the checklist from docs/keda-reference.md §"Production Best Practices".
Returns non-blocking findings — operators see them in the UI before applying.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from kairos.keda.catalog import get_scaler
from kairos.keda.generator import (
    HTTPScaledObjectSpec,
    ScaledObjectSpec,
    TriggerAuthenticationSpec,
    TriggerSpec,
)


class LintFinding(BaseModel):
    """One observation from the linter — surfaced as a pill in the UI."""

    code: str = Field(min_length=1, description="Stable ID, e.g. 'KEDA-001'")
    severity: Literal["info", "warning", "error"]
    message: str = Field(min_length=1)
    field: str | None = None


def lint_scaled_object(
    spec: ScaledObjectSpec,
    *,
    termination_grace_period_seconds: int | None = None,
) -> list[LintFinding]:
    """Apply the production-best-practices checks. Returns findings, never raises."""
    findings: list[LintFinding] = []

    # KEDA-001: cooldown sanity
    if spec.cooldown_period < 60:
        findings.append(
            LintFinding(
                code="KEDA-001",
                severity="warning",
                message=(
                    "cooldownPeriod < 60s tends to flap on bursty traffic. "
                    "Use 60-120s for bursty workloads, 300-600s for long-tail batch."
                ),
                field="cooldown_period",
            )
        )

    # KEDA-002: maxReplicas sanity
    if spec.max_replicas <= spec.min_replicas:
        findings.append(
            LintFinding(
                code="KEDA-002",
                severity="error",
                message="maxReplicaCount must be greater than minReplicaCount.",
                field="max_replicas",
            )
        )

    # KEDA-003: idleReplicaCount must be < minReplicaCount when set
    if spec.idle_replicas is not None and spec.idle_replicas >= spec.min_replicas:
        findings.append(
            LintFinding(
                code="KEDA-003",
                severity="error",
                message="idleReplicaCount must be strictly less than minReplicaCount.",
                field="idle_replicas",
            )
        )

    # KEDA-004: scale-to-zero advisory
    if spec.min_replicas == 0:
        findings.append(
            LintFinding(
                code="KEDA-004",
                severity="info",
                message=(
                    "Scale-to-zero enabled — verify activation thresholds on triggers "
                    "and confirm cold-start meets your SLO."
                ),
                field="min_replicas",
            )
        )

    # KEDA-005: terminationGracePeriodSeconds must clear cooldown work in flight
    if (
        termination_grace_period_seconds is not None
        and termination_grace_period_seconds < 30
    ):
        findings.append(
            LintFinding(
                code="KEDA-005",
                severity="warning",
                message=(
                    f"terminationGracePeriodSeconds={termination_grace_period_seconds}s is short. "
                    "Set ≥ max message-processing time so SIGTERM lets in-flight work drain."
                ),
                field="terminationGracePeriodSeconds",
            )
        )

    # Per-trigger checks
    for i, trig in enumerate(spec.triggers):
        findings.extend(_lint_trigger(trig, index=i))

    # KEDA-007: multi-trigger advisory
    if len(spec.triggers) > 1:
        findings.append(
            LintFinding(
                code="KEDA-007",
                severity="info",
                message=(
                    f"{len(spec.triggers)} triggers configured — KEDA evaluates them as "
                    "logical OR. Highest reported value drives scaling."
                ),
                field="triggers",
            )
        )

    return findings


def lint_trigger_authentication(spec: TriggerAuthenticationSpec) -> list[LintFinding]:
    """Lint a TriggerAuthentication for deprecated patterns.

    KEDA 2.15+ removed the legacy `aad-pod-identity` provider. The replacement
    on Azure is Microsoft Entra Workload Identity (`azure-workload`). We surface
    this as a warning when operators still emit the old form.
    """
    findings: list[LintFinding] = []

    # KEDA-104: deprecated pod-identity provider (aad-pod-identity)
    if spec.pod_identity_provider in ("azure", "aad-pod-identity"):
        findings.append(
            LintFinding(
                code="KEDA-104",
                severity="warning",
                message=(
                    "podIdentity.provider 'azure' (aad-pod-identity) was removed in KEDA 2.15+. "
                    "Migrate to 'azure-workload' (Microsoft Entra Workload Identity). "
                    "AKS managed add-on will start running 2.15+ in the AKS 1.31 preview."
                ),
                field="pod_identity_provider",
            )
        )

    # KEDA-105: prefer cloud-native identity over inline secret bundles
    if spec.secret_target_refs and spec.pod_identity_provider is None:
        findings.append(
            LintFinding(
                code="KEDA-105",
                severity="info",
                message=(
                    "Auth uses Kubernetes Secrets. For cloud-native deployments, "
                    "prefer Workload Identity (AKS), IRSA (EKS), or GCP Workload Identity. "
                    "Cloud-native identity removes the secret rotation burden."
                ),
                field="secret_target_refs",
            )
        )
    return findings


def lint_http_scaled_object(spec: HTTPScaledObjectSpec) -> list[LintFinding]:
    """Lint an HTTPScaledObject — surfaces the HTTP-add-on beta caveats."""
    findings: list[LintFinding] = []

    # KEDA-200: HTTP add-on beta advisory (always-on info)
    findings.append(
        LintFinding(
            code="KEDA-200",
            severity="info",
            message=(
                "KEDA HTTP Add-on is pre-1.0 (beta). Pin the chart version and expect "
                "schema drift on minor upgrades. For workloads that can't tolerate a "
                "queue-based proxy, consider Knative Serving or Prometheus request-rate."
            ),
            field=None,
        )
    )

    # KEDA-201: max < min sanity (mirrors KEDA-002 for ScaledObject)
    if spec.max_replicas <= spec.min_replicas:
        findings.append(
            LintFinding(
                code="KEDA-201",
                severity="error",
                message="replicas.max must be greater than replicas.min.",
                field="max_replicas",
            )
        )

    # KEDA-202: very small targetValue tends to over-scale (requestRate only)
    if spec.metric is not None and spec.metric.target_value <= 5:
        findings.append(
            LintFinding(
                code="KEDA-202",
                severity="warning",
                message=(
                    f"requestRate.targetValue={spec.metric.target_value}/RPS-per-pod is low. "
                    "Per-pod target < 5 RPS often produces flapping during normal traffic dips."
                ),
                field="metric.target_value",
            )
        )

    # KEDA-203: responseHeaderTimeout sanity for cold starts
    if (
        spec.response_header_timeout_seconds is not None
        and spec.response_header_timeout_seconds < 20
    ):
        findings.append(
            LintFinding(
                code="KEDA-203",
                severity="warning",
                message=(
                    f"responseHeaderTimeout={spec.response_header_timeout_seconds}s is short. "
                    "Pod cold-starts plus the slowest legitimate response must fit inside this "
                    "window or users see HTTP 503 / 'timeout awaiting response headers' during "
                    "scale-from-zero. Default is 20s; raise to match your slowest endpoint."
                ),
                field="response_header_timeout_seconds",
            )
        )

    # KEDA-204: scale-from-zero with concurrency mode is the right pick for slow paths
    if spec.concurrency is not None and spec.min_replicas == 0:
        findings.append(
            LintFinding(
                code="KEDA-204",
                severity="info",
                message=(
                    "Concurrency mode + scale-to-zero is recommended for LLM inference, "
                    "large file uploads, and websocket-heavy APIs where requestRate "
                    "misrepresents load. Verify cold-start fits inside responseHeaderTimeout."
                ),
                field="concurrency",
            )
        )
    return findings


def _lint_trigger(trig: TriggerSpec, *, index: int) -> list[LintFinding]:
    """Per-trigger checks: known scaler, required fields, activation threshold."""
    findings: list[LintFinding] = []
    spec = get_scaler(trig.type)
    field_prefix = f"triggers[{index}]"

    if spec is None:
        findings.append(
            LintFinding(
                code="KEDA-100",
                severity="warning",
                message=(
                    f"Unknown scaler type '{trig.type}'. Kairos ships a curated catalog; "
                    "the YAML is still emitted but won't be lint-checked."
                ),
                field=f"{field_prefix}.type",
            )
        )
        return findings

    # KEDA-101: required field check
    for f in spec.fields:
        if f.required and f.name not in trig.metadata:
            findings.append(
                LintFinding(
                    code="KEDA-101",
                    severity="error",
                    message=f"{spec.name}: required field '{f.name}' missing.",
                    field=f"{field_prefix}.metadata.{f.name}",
                )
            )

    # KEDA-102: activation threshold missing on a scaler that supports one
    if (
        spec.activation_field
        and spec.activation_field not in trig.metadata
    ):
        findings.append(
            LintFinding(
                code="KEDA-102",
                severity="info",
                message=(
                    f"{spec.name}: no '{spec.activation_field}' set — workload will wake from "
                    "zero on the first qualifying event. Set a small value (e.g., 5-10) to "
                    "avoid waking on stale messages."
                ),
                field=f"{field_prefix}.metadata.{spec.activation_field}",
            )
        )

    # KEDA-103: secret in inline metadata
    for k, v in trig.metadata.items():
        if any(x in k.lower() for x in ("password", "token", "secret", "credential")):
            findings.append(
                LintFinding(
                    code="KEDA-103",
                    severity="error",
                    message=(
                        f"Inline secret detected in trigger metadata ('{k}'). Move to a "
                        "TriggerAuthentication referencing a Secret."
                    ),
                    field=f"{field_prefix}.metadata.{k}",
                )
            )
        # Also catch anything that *looks* like a token value
        if isinstance(v, str) and len(v) > 40 and ("Bearer " in v or v.startswith(("ghp_", "glsa_", "sk-"))):
            findings.append(
                LintFinding(
                    code="KEDA-103",
                    severity="error",
                    message=(
                        f"Token-shaped value in trigger metadata ('{k}'). "
                        "Move to TriggerAuthentication."
                    ),
                    field=f"{field_prefix}.metadata.{k}",
                )
            )
    return findings
