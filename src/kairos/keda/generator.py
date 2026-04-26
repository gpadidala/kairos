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


# ── Model: ScaledJob (queue-driven batch / one-shot) ────────────
class JobContainerSpec(BaseModel):
    """Subset of the K8s container spec the operator needs to set."""

    name: str = Field(min_length=1)
    image: str = Field(min_length=1)
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    env: list[dict[str, str]] = Field(default_factory=list)


class ScaledJobSpec(BaseModel):
    """Generates a KEDA ScaledJob — one Job per N pending events.

    Use ScaledJob (not ScaledObject) when each unit of work must run to
    completion and shouldn't be killed mid-flight by a scale-down. Common
    examples: video transcoding, ML inference batches, ETL extracts.
    """

    name: str = Field(min_length=1, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    container: JobContainerSpec
    polling_interval: int = Field(default=30, ge=1, le=3600)
    max_replica_count: int = Field(default=100, ge=1, le=10_000)
    successful_jobs_history_limit: int = Field(default=5, ge=0, le=1000)
    failed_jobs_history_limit: int = Field(default=5, ge=0, le=1000)
    parallelism: int = Field(default=1, ge=1)
    completions: int = Field(default=1, ge=1)
    active_deadline_seconds: int = Field(default=600, ge=1, le=86_400)
    backoff_limit: int = Field(default=6, ge=0, le=100)
    scaling_strategy: str = Field(default="default", description="default | custom | accurate")
    triggers: list[TriggerSpec] = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


def render_scaled_job(spec: ScaledJobSpec) -> str:
    """Render a ScaledJob as YAML."""
    container_block: dict[str, Any] = {
        "name": spec.container.name,
        "image": spec.container.image,
    }
    if spec.container.command:
        container_block["command"] = list(spec.container.command)
    if spec.container.args:
        container_block["args"] = list(spec.container.args)
    if spec.container.env:
        container_block["env"] = list(spec.container.env)

    body: dict[str, Any] = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledJob",
        "metadata": {"name": spec.name, "namespace": spec.namespace},
        "spec": {
            "jobTargetRef": {
                "parallelism": spec.parallelism,
                "completions": spec.completions,
                "activeDeadlineSeconds": spec.active_deadline_seconds,
                "backoffLimit": spec.backoff_limit,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [container_block],
                    },
                },
            },
            "pollingInterval": spec.polling_interval,
            "maxReplicaCount": spec.max_replica_count,
            "successfulJobsHistoryLimit": spec.successful_jobs_history_limit,
            "failedJobsHistoryLimit": spec.failed_jobs_history_limit,
            "scalingStrategy": {"strategy": spec.scaling_strategy},
            "triggers": [t.model_dump(exclude_none=True) for t in spec.triggers],
        },
    }
    if spec.labels:
        body["metadata"]["labels"] = dict(spec.labels)
    if spec.annotations:
        body["metadata"]["annotations"] = dict(spec.annotations)
    return str(yaml.safe_dump(body, sort_keys=False, default_flow_style=False))


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


# ── Azure Workload Identity bundle ───────────────────────────────
class AzureWorkloadIdentityBundleSpec(BaseModel):
    """All the YAML + CLI Kairos can hand the operator to wire up Azure WI.

    KEDA 2.15+ removed pod-identity support. The replacement is Microsoft Entra
    Workload Identity. The K8s side is a ServiceAccount + TriggerAuthentication;
    the Azure side is a managed identity + a federated credential, which Kairos
    can't create directly but can render the `az` CLI command for.
    """

    namespace: str = Field(min_length=1)
    service_account_name: str = Field(min_length=1)
    azure_client_id: str = Field(min_length=1, description="UAMI / app client UUID")
    azure_tenant_id: str = Field(min_length=1)
    trigger_auth_name: str = Field(min_length=1)
    aks_oidc_issuer_url: str | None = Field(
        default=None,
        description="From `az aks show ... --query oidcIssuerProfile.issuerUrl`",
    )
    federated_credential_name: str = Field(default="kairos-keda")


def render_azure_workload_identity_bundle(spec: AzureWorkloadIdentityBundleSpec) -> str:
    """Render a multi-doc YAML containing ServiceAccount + TriggerAuthentication.

    Includes inline comments showing the matching `az identity federated-credential
    create` command the operator must run on the Azure side.
    """
    sa_doc = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": spec.service_account_name,
            "namespace": spec.namespace,
            "annotations": {
                "azure.workload.identity/client-id": spec.azure_client_id,
                "azure.workload.identity/tenant-id": spec.azure_tenant_id,
            },
            "labels": {"azure.workload.identity/use": "true"},
        },
    }
    ta_doc = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "TriggerAuthentication",
        "metadata": {"name": spec.trigger_auth_name, "namespace": spec.namespace},
        "spec": {
            "podIdentity": {
                "provider": "azure-workload",
                "identityId": spec.azure_client_id,
            },
        },
    }
    sa_yaml = yaml.safe_dump(sa_doc, sort_keys=False, default_flow_style=False)
    ta_yaml = yaml.safe_dump(ta_doc, sort_keys=False, default_flow_style=False)

    issuer = spec.aks_oidc_issuer_url or "$(az aks show -g <rg> -n <cluster> --query oidcIssuerProfile.issuerUrl -o tsv)"
    azure_cli = (
        f"# Run on the Azure side (one-time; needs the AKS OIDC issuer URL).\n"
        f"# az aks update -g <rg> -n <cluster> --enable-oidc-issuer --enable-workload-identity\n"
        f"#\n"
        f"# az identity federated-credential create \\\n"
        f"#     --name {spec.federated_credential_name} \\\n"
        f"#     --identity-name <uami-name> \\\n"
        f"#     --resource-group <rg> \\\n"
        f"#     --issuer {issuer} \\\n"
        f"#     --subject system:serviceaccount:{spec.namespace}:{spec.service_account_name} \\\n"
        f"#     --audience api://AzureADTokenExchange\n"
    )
    return f"{azure_cli}---\n{sa_yaml}---\n{ta_yaml}"


# ── Model: HTTPScaledObject (HTTP add-on) ────────────────────────
class HTTPScaleTargetRef(BaseModel):
    name: str
    service: str
    port: int = Field(ge=1, le=65535)
    kind: str = "Deployment"
    apiVersion: str = "apps/v1"  # noqa: N815 — mirror Kubernetes YAML field name


class HTTPScalingMetric(BaseModel):
    """requestRate scaling — one of two modes the HTTP add-on supports.

    Use this when traffic is throughput-shaped (req/sec). For long-tail or
    streaming workloads where each request occupies a pod for a meaningful
    duration, prefer ConcurrencyMetric instead.
    """

    target_value: int = Field(default=100, ge=1)
    granularity: str = Field(default="1s")
    window: str = Field(default="1m")


class HTTPConcurrencyMetric(BaseModel):
    """Concurrency scaling — target N in-flight requests per pod.

    Pick this for slow or streaming endpoints (LLM inference, large file
    uploads, websocket-heavy APIs) where requestRate misrepresents load.
    """

    target_value: int = Field(default=10, ge=1, description="In-flight requests per pod")


class HTTPScaledObjectSpec(BaseModel):
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    hosts: list[str] = Field(min_length=1)
    path_prefixes: list[str] = Field(default_factory=lambda: ["/"])
    target: HTTPScaleTargetRef
    min_replicas: int = Field(default=0, ge=0)
    max_replicas: int = Field(default=10, ge=1)
    scaledown_period: int = Field(default=300, ge=1)
    metric: HTTPScalingMetric | None = Field(default_factory=HTTPScalingMetric)
    concurrency: HTTPConcurrencyMetric | None = None
    response_header_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Interceptor responseHeaderTimeout — must exceed slowest legitimate "
            "response (incl. cold-start) or users see 503/timeout."
        ),
    )


def render_http_scaled_object(spec: HTTPScaledObjectSpec) -> str:
    """Render an HTTPScaledObject (KEDA HTTP add-on) as YAML.

    Picks `concurrency` over `requestRate` when both are set — concurrency is
    the more conservative metric for slow endpoints.
    """
    if spec.concurrency is not None:
        scaling_metric: dict[str, Any] = {
            "concurrency": {"targetValue": spec.concurrency.target_value},
        }
    elif spec.metric is not None:
        scaling_metric = {
            "requestRate": {
                "granularity": spec.metric.granularity,
                "targetValue": spec.metric.target_value,
                "window": spec.metric.window,
            },
        }
    else:
        # Default to a sane requestRate if neither is provided
        scaling_metric = {
            "requestRate": {"granularity": "1s", "targetValue": 100, "window": "1m"},
        }

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
            "scalingMetric": scaling_metric,
        },
    }
    if spec.response_header_timeout_seconds is not None:
        body["spec"]["responseHeaderTimeout"] = (
            f"{spec.response_header_timeout_seconds}s"
        )
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
