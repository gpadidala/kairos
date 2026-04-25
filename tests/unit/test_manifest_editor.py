"""Manifest editor — Kustomize + Helm round-trip with comment preservation."""

from __future__ import annotations

import pytest

from kairos.gitops.manifest_editor import (
    ManifestEditError,
    ManifestEditor,
    ManifestFormat,
    detect_manifest_format,
)

DEPLOYMENT_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments-api
  namespace: prod
  labels:
    app: payments-api  # owning team: payments
spec:
  replicas: 3  # baseline
  selector:
    matchLabels:
      app: payments-api
  template:
    metadata:
      labels:
        app: payments-api
    spec:
      containers:
        - name: payments-api
          image: eclipse-temurin:21-jre
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
            limits:
              cpu: 2
              memory: 2Gi
"""


HELM_VALUES = """\
# payments-api values
replicaCount: 3
image:
  repository: eclipse-temurin
  tag: "21-jre"
resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: "2"
    memory: 2Gi
"""


SCALEDOBJECT_YAML = """\
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: payments-api-scaler
  namespace: prod
spec:
  scaleTargetRef:
    name: payments-api
  minReplicaCount: 1
  maxReplicaCount: 10
  triggers:
    - type: kafka
      metadata:
        topic: payments
        lagThreshold: "100"
"""


def test_detect_manifest_format_kustomize() -> None:
    assert (
        detect_manifest_format("apps/payments-api/deployment.yaml", DEPLOYMENT_YAML)
        == ManifestFormat.KUSTOMIZE
    )


def test_detect_manifest_format_helm_values() -> None:
    assert (
        detect_manifest_format("apps/payments-api/values.yaml", HELM_VALUES)
        == ManifestFormat.HELM_VALUES
    )


def test_detect_manifest_format_helm_prod_values() -> None:
    assert (
        detect_manifest_format("apps/payments-api/values-prod.yaml", HELM_VALUES)
        == ManifestFormat.HELM_VALUES
    )


def test_kustomize_set_replicas_preserves_comments() -> None:
    e = ManifestEditor()
    out = e.set_replicas_kustomize(DEPLOYMENT_YAML, 5)
    assert "replicas: 5" in out
    assert "# baseline" in out
    assert "# owning team: payments" in out
    assert "eclipse-temurin:21-jre" in out


def test_kustomize_set_resources_updates_first_container() -> None:
    e = ManifestEditor()
    out = e.set_container_resources_kustomize(
        DEPLOYMENT_YAML, cpu_request="1500m", mem_request="2048Mi"
    )
    assert "cpu: 1500m" in out
    assert "memory: 2048Mi" in out
    # limits untouched
    assert "cpu: 2" in out or "cpu: '2'" in out
    assert "memory: 2Gi" in out


def test_kustomize_set_resources_requires_named_container_when_given() -> None:
    e = ManifestEditor()
    with pytest.raises(ManifestEditError, match="container named"):
        e.set_container_resources_kustomize(
            DEPLOYMENT_YAML, container_name="does-not-exist", cpu_request="1"
        )


def test_helm_set_replicas_preserves_comments() -> None:
    e = ManifestEditor()
    out = e.set_replicas_helm(HELM_VALUES, 8)
    assert "replicaCount: 8" in out
    assert "# payments-api values" in out


def test_helm_set_resources_creates_path_if_absent() -> None:
    e = ManifestEditor()
    out = e.set_resources_helm(
        "replicaCount: 2\n",
        cpu_request="750m",
        mem_request="1Gi",
    )
    assert "cpu: 750m" in out
    assert "memory: 1Gi" in out


def test_helm_set_resources_preserves_other_values() -> None:
    e = ManifestEditor()
    out = e.set_resources_helm(HELM_VALUES, cpu_request="1500m")
    assert "cpu: 1500m" in out
    assert "memory: 1Gi" in out  # memory preserved
    assert "repository: eclipse-temurin" in out


def test_keda_min_replicas_edit() -> None:
    e = ManifestEditor()
    out = e.set_keda_min_replicas(SCALEDOBJECT_YAML, 3)
    assert "minReplicaCount: 3" in out
    assert "maxReplicaCount: 10" in out
    assert "topic: payments" in out


def test_missing_spec_raises() -> None:
    e = ManifestEditor()
    with pytest.raises(ManifestEditError, match="spec"):
        e.set_replicas_kustomize("apiVersion: v1\nkind: Foo\n", 2)


def test_containers_missing_raises() -> None:
    e = ManifestEditor()
    with pytest.raises(ManifestEditError, match="containers"):
        e.set_container_resources_kustomize(
            "apiVersion: apps/v1\nkind: Deployment\nspec:\n  replicas: 1\n",
            cpu_request="1",
        )
