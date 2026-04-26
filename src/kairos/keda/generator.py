"""Generators for ScaledObject / TriggerAuthentication / HTTPScaledObject YAML.

Pydantic models that mirror the KEDA CRD schema closely enough for Kairos to
emit valid YAML. We deliberately don't model every optional field — just the
ones operators actually set (per docs/keda-reference.md).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field

from kairos.keda.catalog import get_scaler


# ── Model: ScaledObject ───────────────────────────────────────────
class ScaleTargetRef(BaseModel):
    """Mirrors KEDA's spec.scaleTargetRef schema. mixedCase mirrors the YAML."""

    name: str = Field(min_length=1)
    kind: str = "Deployment"
    apiVersion: str = "apps/v1"  # noqa: N815 — mirror Kubernetes YAML field name


class TriggerSpec(BaseModel):
    type: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    authenticationRef: dict[str, str] | None = None  # noqa: N815 — KEDA YAML field name


class FallbackSpec(BaseModel):
    failureThreshold: int = Field(default=3, ge=1)  # noqa: N815 — KEDA YAML field name
    replicas: int = Field(default=1, ge=0)


class ScaledObjectSpec(BaseModel):
    name: str = Field(min_length=1, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    target: ScaleTargetRef
    polling_interval: int = Field(default=30, ge=1, le=3600)
    cooldown_period: int = Field(default=300, ge=1, le=3600)
    min_replicas: int = Field(default=0, ge=0, le=10_000)
    max_replicas: int = Field(default=10, ge=1, le=10_000)
    idle_replicas: int | None = Field(default=None, ge=0)
    triggers: list[TriggerSpec] = Field(min_length=1)
    fallback: FallbackSpec | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


def render_scaled_object(spec: ScaledObjectSpec) -> str:
    """Render a ScaledObject as YAML, ready to apply or PR into a GitOps repo."""
    body: dict[str, Any] = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {"name": spec.name, "namespace": spec.namespace},
        "spec": {
            "scaleTargetRef": spec.target.model_dump(exclude_none=True),
            "pollingInterval": spec.polling_interval,
            "cooldownPeriod": spec.cooldown_period,
            "minReplicaCount": spec.min_replicas,
            "maxReplicaCount": spec.max_replicas,
            "triggers": [t.model_dump(exclude_none=True) for t in spec.triggers],
        },
    }
    meta = body["metadata"]
    if spec.labels:
        meta["labels"] = dict(spec.labels)
    if spec.annotations:
        meta["annotations"] = dict(spec.annotations)
    so_spec = body["spec"]
    if spec.idle_replicas is not None:
        so_spec["idleReplicaCount"] = spec.idle_replicas
    if spec.fallback is not None:
        so_spec["fallback"] = spec.fallback.model_dump()
    return str(yaml.safe_dump(body, sort_keys=False, default_flow_style=False))


# ── Model: TriggerAuthentication ─────────────────────────────────
class SecretTargetRef(BaseModel):
    parameter: str
    name: str
    key: str


class TriggerAuthenticationSpec(BaseModel):
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    cluster_scope: bool = Field(
        default=False, description="Render as ClusterTriggerAuthentication if True"
    )

    secret_target_refs: list[SecretTargetRef] = Field(default_factory=list)
    pod_identity_provider: str | None = Field(
        default=None, description="aws | azure | azure-workload | gcp | none"
    )
    pod_identity_id: str | None = Field(
        default=None, description="Client ID for azure-workload, etc."
    )
    vault_address: str | None = None
    vault_secrets: list[dict[str, str]] = Field(default_factory=list)


def render_trigger_authentication(spec: TriggerAuthenticationSpec) -> str:
    """Render a TriggerAuthentication or ClusterTriggerAuthentication as YAML."""
    kind = "ClusterTriggerAuthentication" if spec.cluster_scope else "TriggerAuthentication"
    body: dict[str, Any] = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": kind,
        "metadata": {"name": spec.name},
        "spec": {},
    }
    if not spec.cluster_scope:
        body["metadata"]["namespace"] = spec.namespace

    if spec.secret_target_refs:
        body["spec"]["secretTargetRef"] = [s.model_dump() for s in spec.secret_target_refs]
    if spec.pod_identity_provider:
        pod_id: dict[str, str] = {"provider": spec.pod_identity_provider}
        if spec.pod_identity_id:
            pod_id["identityId"] = spec.pod_identity_id
        body["spec"]["podIdentity"] = pod_id
    if spec.vault_address and spec.vault_secrets:
        body["spec"]["hashiCorpVault"] = {
            "address": spec.vault_address,
            "authentication": "token",
            "secrets": list(spec.vault_secrets),
        }
    return str(yaml.safe_dump(body, sort_keys=False, default_flow_style=False))


# ── Model: HTTPScaledObject (HTTP add-on) ────────────────────────
class HTTPScaleTargetRef(BaseModel):
    name: str
    service: str
    port: int = Field(ge=1, le=65535)
    kind: str = "Deployment"
    apiVersion: str = "apps/v1"  # noqa: N815 — mirror Kubernetes YAML field name


class HTTPScalingMetric(BaseModel):
    target_value: int = Field(default=100, ge=1)
    granularity: str = Field(default="1s")
    window: str = Field(default="1m")


class HTTPScaledObjectSpec(BaseModel):
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    hosts: list[str] = Field(min_length=1)
    path_prefixes: list[str] = Field(default_factory=lambda: ["/"])
    target: HTTPScaleTargetRef
    min_replicas: int = Field(default=0, ge=0)
    max_replicas: int = Field(default=10, ge=1)
    scaledown_period: int = Field(default=300, ge=1)
    metric: HTTPScalingMetric = Field(default_factory=HTTPScalingMetric)


def render_http_scaled_object(spec: HTTPScaledObjectSpec) -> str:
    """Render an HTTPScaledObject (KEDA HTTP add-on) as YAML."""
    body: dict[str, Any] = {
        "apiVersion": "http.keda.sh/v1alpha1",
        "kind": "HTTPScaledObject",
        "metadata": {"name": spec.name, "namespace": spec.namespace},
        "spec": {
            "hosts": list(spec.hosts),
            "pathPrefixes": list(spec.path_prefixes),
            "scaleTargetRef": spec.target.model_dump(),
            "replicas": {"min": spec.min_replicas, "max": spec.max_replicas},
            "scaledownPeriod": spec.scaledown_period,
            "scalingMetric": {
                "requestRate": {
                    "granularity": spec.metric.granularity,
                    "targetValue": spec.metric.target_value,
                    "window": spec.metric.window,
                },
            },
        },
    }
    return str(yaml.safe_dump(body, sort_keys=False, default_flow_style=False))


# ── Convenience: scaler-type-aware preview from a Workload ──────
def suggest_trigger_for_workload(annotations: dict[str, str]) -> TriggerSpec | None:
    """Inspect kairos.io/* and well-known annotations to suggest a trigger.

    Recognized:
      - kairos.io/keda-trigger-type     overrides the auto-detection
      - kairos.io/kafka-topic           → kafka trigger
      - kairos.io/rabbitmq-queue        → rabbitmq trigger
      - kairos.io/sqs-queue-url         → aws-sqs-queue trigger
      - kairos.io/prometheus-query      → prometheus trigger
    """
    explicit = annotations.get("kairos.io/keda-trigger-type")
    if explicit and get_scaler(explicit):
        return TriggerSpec(type=explicit, metadata={})

    if topic := annotations.get("kairos.io/kafka-topic"):
        return TriggerSpec(
            type="kafka",
            metadata={
                "bootstrapServers": annotations.get(
                    "kairos.io/kafka-bootstrap", "kafka.kafka.svc:9092"
                ),
                "consumerGroup": annotations.get(
                    "kairos.io/kafka-consumer-group", "default-group"
                ),
                "topic": topic,
                "lagThreshold": annotations.get("kairos.io/kafka-lag-threshold", "100"),
            },
        )

    if queue := annotations.get("kairos.io/rabbitmq-queue"):
        return TriggerSpec(
            type="rabbitmq",
            metadata={
                "queueName": queue,
                "mode": "QueueLength",
                "value": annotations.get("kairos.io/rabbitmq-target", "10"),
                "protocol": "amqp",
            },
        )

    if url := annotations.get("kairos.io/sqs-queue-url"):
        return TriggerSpec(
            type="aws-sqs-queue",
            metadata={
                "queueURL": url,
                "queueLength": annotations.get("kairos.io/sqs-target", "5"),
                "awsRegion": annotations.get("kairos.io/aws-region", "us-east-1"),
                "identityOwner": "operator",
            },
        )

    if query := annotations.get("kairos.io/prometheus-query"):
        return TriggerSpec(
            type="prometheus",
            metadata={
                "serverAddress": annotations.get(
                    "kairos.io/prometheus-server",
                    "http://prometheus.monitoring.svc:9090",
                ),
                "query": query,
                "threshold": annotations.get("kairos.io/prometheus-threshold", "100"),
            },
        )
    return None
