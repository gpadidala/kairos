"""Tests for kairos.keda — catalog, generator, validator."""

from __future__ import annotations

import yaml

from kairos.keda import (
    AzureWorkloadIdentityBundleSpec,
    HTTPScaledObjectSpec,
    JobContainerSpec,
    ScaledJobSpec,
    ScaledObjectSpec,
    TriggerAuthenticationSpec,
    get_scaler,
    lint_http_scaled_object,
    lint_scaled_object,
    lint_trigger_authentication,
    render_azure_workload_identity_bundle,
    render_http_scaled_object,
    render_scaled_job,
    render_scaled_object,
    render_trigger_authentication,
)
from kairos.keda.catalog import SCALERS, ScalerCategory
from kairos.keda.generator import (
    HTTPScaleTargetRef,
    HTTPScalingMetric,
    ScaleTargetRef,
    SecretTargetRef,
    TriggerSpec,
    suggest_trigger_for_workload,
)


# ── catalog ────────────────────────────────────────────────────────
def test_catalog_has_all_priority_scalers() -> None:
    """Sanity — the must-have scalers are there."""
    types = {s.type for s in SCALERS}
    expected = {
        "kafka",
        "rabbitmq",
        "aws-sqs-queue",
        "azure-servicebus",
        "azure-eventhub",
        "aws-kinesis-stream",
        "gcp-pubsub",
        "nats-jetstream",
        "redis-streams",
        "prometheus",
        "postgresql",
        "cron",
        "cpu",
        "memory",
    }
    missing = expected - types
    assert not missing, f"missing scalers: {missing}"


def test_get_scaler_known_and_unknown() -> None:
    assert get_scaler("kafka") is not None
    assert get_scaler("not-a-scaler") is None


def test_kafka_has_lag_threshold_field() -> None:
    kafka = get_scaler("kafka")
    assert kafka is not None
    field_names = {f.name for f in kafka.fields}
    assert {"bootstrapServers", "consumerGroup", "topic", "lagThreshold"} <= field_names
    assert kafka.activation_field == "activationLagThreshold"
    assert kafka.category == ScalerCategory.MESSAGE_BROKER


# ── ScaledObject YAML generation ───────────────────────────────────
def _kafka_so() -> ScaledObjectSpec:
    return ScaledObjectSpec(
        name="orders-scaler",
        namespace="workers",
        target=ScaleTargetRef(name="orders-consumer"),
        min_replicas=0,
        max_replicas=20,
        cooldown_period=120,
        triggers=[
            TriggerSpec(
                type="kafka",
                metadata={
                    "bootstrapServers": "kafka.kafka.svc:9092",
                    "consumerGroup": "orders-svc",
                    "topic": "orders",
                    "lagThreshold": "100",
                    "activationLagThreshold": "10",
                },
                authenticationRef={"name": "kafka-auth"},
            )
        ],
    )


def test_render_scaled_object_round_trips() -> None:
    yaml_str = render_scaled_object(_kafka_so())
    parsed = yaml.safe_load(yaml_str)
    assert parsed["apiVersion"] == "keda.sh/v1alpha1"
    assert parsed["kind"] == "ScaledObject"
    assert parsed["metadata"]["name"] == "orders-scaler"
    assert parsed["spec"]["minReplicaCount"] == 0
    assert parsed["spec"]["maxReplicaCount"] == 20
    assert parsed["spec"]["triggers"][0]["type"] == "kafka"
    assert parsed["spec"]["triggers"][0]["authenticationRef"]["name"] == "kafka-auth"


def test_render_http_scaled_object() -> None:
    spec = HTTPScaledObjectSpec(
        name="api-scaler",
        namespace="prod",
        hosts=["api.example.com"],
        target=HTTPScaleTargetRef(name="api", service="api", port=8080),
        max_replicas=50,
        metric=HTTPScalingMetric(target_value=200),
    )
    parsed = yaml.safe_load(render_http_scaled_object(spec))
    assert parsed["apiVersion"] == "http.keda.sh/v1alpha1"
    assert parsed["spec"]["scalingMetric"]["requestRate"]["targetValue"] == 200


def test_render_trigger_authentication_with_secret() -> None:
    spec = TriggerAuthenticationSpec(
        name="kafka-auth",
        namespace="workers",
        secret_target_refs=[
            SecretTargetRef(parameter="username", name="kafka-creds", key="username"),
            SecretTargetRef(parameter="password", name="kafka-creds", key="password"),
        ],
    )
    parsed = yaml.safe_load(render_trigger_authentication(spec))
    assert parsed["kind"] == "TriggerAuthentication"
    assert len(parsed["spec"]["secretTargetRef"]) == 2


def test_render_trigger_authentication_irsa() -> None:
    spec = TriggerAuthenticationSpec(
        name="sqs-auth",
        namespace="workers",
        pod_identity_provider="aws",
    )
    parsed = yaml.safe_load(render_trigger_authentication(spec))
    assert parsed["spec"]["podIdentity"]["provider"] == "aws"


def test_render_cluster_trigger_authentication_strips_namespace() -> None:
    spec = TriggerAuthenticationSpec(
        name="shared-auth",
        namespace="ignored",
        cluster_scope=True,
        pod_identity_provider="azure-workload",
        pod_identity_id="abcd-1234",
    )
    parsed = yaml.safe_load(render_trigger_authentication(spec))
    assert parsed["kind"] == "ClusterTriggerAuthentication"
    assert "namespace" not in parsed["metadata"]
    assert parsed["spec"]["podIdentity"]["identityId"] == "abcd-1234"


# ── auto-detection from workload annotations ──────────────────────
def test_suggest_kafka_from_annotations() -> None:
    t = suggest_trigger_for_workload(
        {
            "kairos.io/kafka-topic": "orders",
            "kairos.io/kafka-consumer-group": "orders-svc",
            "kairos.io/kafka-bootstrap": "kafka.svc:9092",
        }
    )
    assert t is not None
    assert t.type == "kafka"
    assert t.metadata["topic"] == "orders"


def test_suggest_rabbitmq_from_annotations() -> None:
    t = suggest_trigger_for_workload({"kairos.io/rabbitmq-queue": "tasks"})
    assert t is not None
    assert t.type == "rabbitmq"
    assert t.metadata["queueName"] == "tasks"


def test_suggest_returns_none_when_no_hint() -> None:
    assert suggest_trigger_for_workload({}) is None


def test_suggest_explicit_type_takes_precedence() -> None:
    t = suggest_trigger_for_workload(
        {
            "kairos.io/keda-trigger-type": "cron",
            "kairos.io/kafka-topic": "should-be-ignored",
        }
    )
    assert t is not None
    assert t.type == "cron"


# ── validator ──────────────────────────────────────────────────────
def test_lint_clean_kafka_so_has_only_info_findings() -> None:
    findings = lint_scaled_object(_kafka_so(), termination_grace_period_seconds=60)
    severities = {f.severity for f in findings}
    assert "error" not in severities


def test_lint_short_cooldown_warning() -> None:
    so = _kafka_so()
    so.cooldown_period = 10
    findings = lint_scaled_object(so)
    codes = {f.code for f in findings}
    assert "KEDA-001" in codes


def test_lint_max_below_min_error() -> None:
    so = _kafka_so()
    so.min_replicas = 5
    so.max_replicas = 5
    findings = lint_scaled_object(so)
    assert any(f.code == "KEDA-002" and f.severity == "error" for f in findings)


def test_lint_idle_must_be_below_min() -> None:
    so = _kafka_so()
    so.min_replicas = 2
    so.idle_replicas = 5
    findings = lint_scaled_object(so)
    assert any(f.code == "KEDA-003" and f.severity == "error" for f in findings)


def test_lint_required_field_missing() -> None:
    so = _kafka_so()
    so.triggers[0].metadata.pop("topic")
    findings = lint_scaled_object(so)
    assert any(f.code == "KEDA-101" and "topic" in f.message for f in findings)


def test_lint_inline_secret_detected() -> None:
    so = _kafka_so()
    so.triggers[0].metadata["password"] = "super-secret-123456"
    findings = lint_scaled_object(so)
    assert any(f.code == "KEDA-103" and f.severity == "error" for f in findings)


def test_lint_unknown_scaler_warns_not_errors() -> None:
    so = _kafka_so()
    so.triggers[0] = TriggerSpec(type="brand-new-scaler-2030", metadata={})
    findings = lint_scaled_object(so)
    assert any(f.code == "KEDA-100" for f in findings)
    assert not any(f.code == "KEDA-101" and f.severity == "error" for f in findings)


# ── Round 1: ScaledJob ─────────────────────────────────────────────
def test_render_scaled_job_round_trips() -> None:
    spec = ScaledJobSpec(
        name="transcode-job",
        namespace="media",
        container=JobContainerSpec(
            name="ffmpeg",
            image="myorg/transcoder:1.2.3",
            args=["--queue", "transcode"],
        ),
        active_deadline_seconds=3600,
        max_replica_count=50,
        triggers=[
            TriggerSpec(
                type="rabbitmq",
                metadata={
                    "queueName": "transcode",
                    "mode": "QueueLength",
                    "value": "1",
                },
                authenticationRef={"name": "rabbit-auth"},
            )
        ],
    )
    parsed = yaml.safe_load(render_scaled_job(spec))
    assert parsed["kind"] == "ScaledJob"
    assert parsed["spec"]["jobTargetRef"]["activeDeadlineSeconds"] == 3600
    assert parsed["spec"]["jobTargetRef"]["template"]["spec"]["restartPolicy"] == "Never"
    assert parsed["spec"]["maxReplicaCount"] == 50
    assert parsed["spec"]["triggers"][0]["type"] == "rabbitmq"


# ── Round 1: Azure Workload Identity bundle ───────────────────────
def test_render_azure_workload_identity_bundle_emits_sa_and_trigger_auth() -> None:
    spec = AzureWorkloadIdentityBundleSpec(
        namespace="workers",
        service_account_name="kairos-keda-sa",
        azure_client_id="00000000-0000-0000-0000-000000000001",
        azure_tenant_id="00000000-0000-0000-0000-000000000002",
        trigger_auth_name="kairos-keda-auth",
        aks_oidc_issuer_url="https://oidc.prod-aks.azure.com/abc/",
    )
    out = render_azure_workload_identity_bundle(spec)
    docs = [d for d in yaml.safe_load_all(out) if d is not None]
    kinds = {d["kind"] for d in docs}
    assert kinds == {"ServiceAccount", "TriggerAuthentication"}
    sa = next(d for d in docs if d["kind"] == "ServiceAccount")
    assert (
        sa["metadata"]["annotations"]["azure.workload.identity/client-id"]
        == "00000000-0000-0000-0000-000000000001"
    )
    assert sa["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    ta = next(d for d in docs if d["kind"] == "TriggerAuthentication")
    assert ta["spec"]["podIdentity"]["provider"] == "azure-workload"
    # az CLI hint preserved as comment
    assert "az identity federated-credential create" in out
    assert spec.aks_oidc_issuer_url in out


# ── Round 1: lint_trigger_authentication ──────────────────────────
def test_lint_trigger_auth_warns_on_deprecated_pod_identity() -> None:
    spec = TriggerAuthenticationSpec(
        name="legacy",
        namespace="workers",
        pod_identity_provider="azure",
    )
    findings = lint_trigger_authentication(spec)
    assert any(f.code == "KEDA-104" and f.severity == "warning" for f in findings)


def test_lint_trigger_auth_recommends_workload_identity_for_secrets() -> None:
    spec = TriggerAuthenticationSpec(
        name="kafka-auth",
        namespace="workers",
        secret_target_refs=[
            SecretTargetRef(parameter="username", name="creds", key="username"),
        ],
    )
    findings = lint_trigger_authentication(spec)
    assert any(f.code == "KEDA-105" and f.severity == "info" for f in findings)


def test_lint_trigger_auth_clean_for_workload_identity() -> None:
    spec = TriggerAuthenticationSpec(
        name="aks-auth",
        namespace="workers",
        pod_identity_provider="azure-workload",
        pod_identity_id="abcd-1234",
    )
    findings = lint_trigger_authentication(spec)
    assert not any(f.severity in ("warning", "error") for f in findings)


# ── Round 1: lint_http_scaled_object ──────────────────────────────
def _http_so(*, max_replicas: int = 30, target_value: int = 100) -> HTTPScaledObjectSpec:
    return HTTPScaledObjectSpec(
        name="api-scaler",
        namespace="prod",
        hosts=["api.example.com"],
        target=HTTPScaleTargetRef(name="api", service="api", port=8080),
        max_replicas=max_replicas,
        metric=HTTPScalingMetric(target_value=target_value),
    )


def test_lint_http_always_emits_beta_advisory() -> None:
    findings = lint_http_scaled_object(_http_so())
    assert any(f.code == "KEDA-200" and f.severity == "info" for f in findings)


def test_lint_http_max_below_min_error() -> None:
    spec = _http_so()
    spec.min_replicas = 10
    spec.max_replicas = 5
    findings = lint_http_scaled_object(spec)
    assert any(f.code == "KEDA-201" and f.severity == "error" for f in findings)


def test_lint_http_low_target_value_warning() -> None:
    spec = _http_so(target_value=2)
    findings = lint_http_scaled_object(spec)
    assert any(f.code == "KEDA-202" and f.severity == "warning" for f in findings)
