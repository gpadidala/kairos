"""Domain models — Pydantic v2 boundary objects. Immutable where practical."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from kairos.domain.enums import (
    ApprovalStatus,
    ForecastModel,
    LLMProviderName,
    NotificationChannel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)

# ── helpers ───────────────────────────────────────────────────────────────
_CPU_RE = re.compile(r"^\d+(\.\d+)?(m)?$")
_MEM_RE = re.compile(r"^\d+(\.\d+)?(Ki|Mi|Gi|Ti|k|M|G|T)?$")


def _strict() -> ConfigDict:
    return ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        frozen=False,  # models often rebuilt incrementally; immutability enforced per-field
        validate_assignment=True,
    )


class Workload(BaseModel):
    """A Kubernetes workload KAIROS observes. Read-only source of truth from discovery."""

    model_config = _strict()

    name: str = Field(min_length=1, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    kind: WorkloadKind
    runtime: Runtime = Runtime.UNKNOWN
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    current_replicas: int = Field(ge=0, le=10_000)
    cpu_request: str = Field(description="Kubernetes CPU quantity, e.g. '500m' or '2'")
    cpu_limit: str | None = None
    mem_request: str = Field(description="Kubernetes memory quantity, e.g. '512Mi'")
    mem_limit: str | None = None
    keda_scaledobject: str | None = None
    gitops_path: str | None = Field(
        default=None,
        description="Path inside the GitOps repo where this workload's manifest lives.",
    )

    @field_validator("cpu_request", "cpu_limit")
    @classmethod
    def _v_cpu(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _CPU_RE.match(v):
            raise ValueError(f"invalid CPU quantity: {v!r}")
        return v

    @field_validator("mem_request", "mem_limit")
    @classmethod
    def _v_mem(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _MEM_RE.match(v):
            raise ValueError(f"invalid memory quantity: {v!r}")
        return v

    @property
    def uid(self) -> str:
        """Stable identifier used in dedup keys, metric labels, and cache keys."""
        return f"{self.kind.value}/{self.namespace}/{self.name}"

    @property
    def is_excluded(self) -> bool:
        return self.annotations.get("kairos.io/exclude", "").lower() == "true"


class MetricPoint(BaseModel):
    """A single timestamped sample."""

    model_config = _strict()

    ts: datetime
    value: float


class MetricSeries(BaseModel):
    """Observed metric series for a single workload / metric name."""

    model_config = _strict()

    workload: Workload
    metric: str = Field(min_length=1)
    points: list[MetricPoint] = Field(default_factory=list)
    resolution_seconds: int = Field(gt=0, le=86_400)

    @property
    def duration_seconds(self) -> int:
        if len(self.points) < 2:
            return 0
        return int((self.points[-1].ts - self.points[0].ts).total_seconds())


class Forecast(BaseModel):
    """A bounded prediction for one metric over a horizon."""

    model_config = _strict()

    workload: Workload
    metric: str
    horizon_hours: int = Field(gt=0, le=168)
    points: list[MetricPoint] = Field(default_factory=list)
    p95_predicted: float
    peak_predicted: float
    peak_at: datetime
    breach_at: datetime | None = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    model_used: ForecastModel
    generated_at: datetime

    @model_validator(mode="after")
    def _check_monotonic(self) -> Forecast:
        if self.points:
            prev = self.points[0].ts
            for p in self.points[1:]:
                if p.ts < prev:
                    raise ValueError("forecast points must be monotonically non-decreasing by ts")
                prev = p.ts
        return self


class ScalingDecision(BaseModel):
    """Deterministic output of the decision engine for one workload."""

    model_config = _strict()

    workload: Workload
    action: ScalingAction
    reason_code: str = Field(
        min_length=1, description="Stable enum-like identifier, e.g. 'R-001' or 'CPU_HEADROOM'"
    )
    rationale: str = Field(min_length=1)
    target_replicas: int | None = Field(default=None, ge=0, le=10_000)
    target_cpu_request: str | None = None
    target_mem_request: str | None = None
    forecasts: list[Forecast] = Field(default_factory=list)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    correlation_id: str = Field(min_length=1)
    generated_at: datetime
    requires_approval: bool = False

    @field_validator("target_cpu_request")
    @classmethod
    def _v_target_cpu(cls, v: str | None) -> str | None:
        if v is not None and not _CPU_RE.match(v):
            raise ValueError(f"invalid target_cpu_request: {v!r}")
        return v

    @field_validator("target_mem_request")
    @classmethod
    def _v_target_mem(cls, v: str | None) -> str | None:
        if v is not None and not _MEM_RE.match(v):
            raise ValueError(f"invalid target_mem_request: {v!r}")
        return v

    def decision_hash(self) -> str:
        """
        Content-addressed hash used as the dedup key canonical input.

        Built from: workload uid + action + target_replicas + target_cpu + target_mem + reason_code.
        Excludes correlation_id / timestamps so identical decisions dedupe across runs.
        """
        h = hashlib.sha256()
        h.update(self.workload.uid.encode())
        h.update(b"|")
        h.update(self.action.value.encode())
        h.update(b"|")
        h.update(str(self.target_replicas).encode())
        h.update(b"|")
        h.update((self.target_cpu_request or "").encode())
        h.update(b"|")
        h.update((self.target_mem_request or "").encode())
        h.update(b"|")
        h.update(self.reason_code.encode())
        return h.hexdigest()[:16]


class LLMAdvice(BaseModel):
    """LLM-generated human-readable guidance attached to a decision."""

    model_config = _strict()

    why: str = Field(min_length=1)
    horizontal_vs_vertical: str = Field(min_length=1)
    risks_of_inaction: str = Field(min_length=1)
    engineer_steps: list[str] = Field(min_length=1)
    validation_steps: list[str] = Field(min_length=1)
    provider_used: LLMProviderName
    prompt_version: str = Field(default="v1")
    tokens_used: int = Field(ge=0)


class PRResult(BaseModel):
    """Outcome of opening a GitOps PR.

    `number=0` denotes a stub result (dry-run or dedup-hit).
    """

    model_config = _strict()

    url: HttpUrl
    number: int = Field(ge=0)
    branch: str
    files_changed: list[str]
    dry_run: bool = False
    dedup_hit: bool = False


class NotificationResult(BaseModel):
    """Per-channel notification result."""

    model_config = _strict()

    channel: NotificationChannel
    delivered: bool
    error: str | None = None
    dedup_hit: bool = False


class PendingApproval(BaseModel):
    """A ScalingDecision awaiting human approval before side effects fire."""

    model_config = _strict()

    id: str
    decision: ScalingDecision
    advice: LLMAdvice | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime
    updated_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    pr_url: HttpUrl | None = None
    pr_number: int | None = None
    error: str | None = None

    @property
    def decision_hash(self) -> str:
        return self.decision.decision_hash()


class KedaActivity(BaseModel):
    """A single KEDA-driven replica-count change observed in the last N hours."""

    model_config = _strict()

    workload_uid: str
    scaledobject: str
    from_replicas: int = Field(ge=0)
    to_replicas: int = Field(ge=0)
    ts: datetime

    @property
    def delta(self) -> int:
        return self.to_replicas - self.from_replicas


class NodePoolActivity(BaseModel):
    """Node-pool size change observed within the observation window."""

    model_config = _strict()

    node_pool: str
    from_nodes: int = Field(ge=0)
    to_nodes: int = Field(ge=0)
    ts: datetime

    @property
    def delta(self) -> int:
        return self.to_nodes - self.from_nodes


class GrafanaAlert(BaseModel):
    """Active alert pulled from the Grafana alerting API."""

    model_config = _strict()

    uid: str
    title: str
    state: str  # "alerting" | "pending" | "normal" | "no_data" | "error"
    severity: str = "info"
    labels: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    starts_at: datetime | None = None


class RunResult(BaseModel):
    """Summary of one pipeline run. Used by the API and audit store."""

    model_config = _strict()

    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["running", "succeeded", "failed", "partial"]
    workloads_processed: int = 0
    decisions: list[ScalingDecision] = Field(default_factory=list)
    prs: list[PRResult] = Field(default_factory=list)
    notifications: list[NotificationResult] = Field(default_factory=list)
    error: str | None = None
