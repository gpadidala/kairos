"""Round-trip YAML editor for Kustomize and Helm manifests.

Uses `ruamel.yaml` to preserve comments and ordering. Every edit method returns
the modified file contents as a string — it never writes to disk (that's the
GitHubClient's job).
"""

from __future__ import annotations

import io
from enum import StrEnum

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from pcap.domain.exceptions import PCAPError


class ManifestFormat(StrEnum):
    KUSTOMIZE = "kustomize"
    HELM_VALUES = "helm_values"


class ManifestEditError(PCAPError):
    """Raised on manifest structure mismatch (e.g. missing spec.replicas)."""


def detect_manifest_format(file_path: str, content: str) -> ManifestFormat:
    """Heuristic: Helm values live in a file called values*.yaml; otherwise Kustomize."""
    lower = file_path.lower()
    if lower.endswith("values.yaml") or "/values" in lower or lower.endswith("values-prod.yaml"):
        return ManifestFormat.HELM_VALUES
    if "kind:" in content and ("Deployment" in content or "StatefulSet" in content):
        return ManifestFormat.KUSTOMIZE
    return ManifestFormat.HELM_VALUES


class ManifestEditor:
    """Preserves comments/ordering/anchors via ruamel round-trip."""

    def __init__(self) -> None:
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)

    # ── Parsing ───────────────────────────────────────────────────────
    def load(self, content: str) -> CommentedMap:
        data = self._yaml.load(content)
        if data is None:
            return CommentedMap()
        if not isinstance(data, CommentedMap):
            raise ManifestEditError("top-level YAML must be a mapping")
        return data

    def dump(self, data: CommentedMap) -> str:
        buf = io.StringIO()
        self._yaml.dump(data, buf)
        return buf.getvalue()

    # ── Kustomize: Deployment/StatefulSet/DaemonSet manifest edits ────
    def set_replicas_kustomize(self, content: str, new_replicas: int) -> str:
        data = self.load(content)
        spec = data.get("spec")
        if not isinstance(spec, CommentedMap):
            raise ManifestEditError("manifest missing .spec mapping")
        spec["replicas"] = int(new_replicas)
        return self.dump(data)

    def set_container_resources_kustomize(
        self,
        content: str,
        *,
        container_name: str | None = None,
        cpu_request: str | None = None,
        mem_request: str | None = None,
    ) -> str:
        data = self.load(content)
        containers = self._get_containers(data)
        target = self._find_container(containers, container_name)
        resources = target.setdefault("resources", CommentedMap())
        requests = resources.setdefault("requests", CommentedMap())
        if cpu_request is not None:
            requests["cpu"] = cpu_request
        if mem_request is not None:
            requests["memory"] = mem_request
        return self.dump(data)

    # ── Helm values edits ─────────────────────────────────────────────
    def set_replicas_helm(
        self, content: str, new_replicas: int, *, key: str = "replicaCount"
    ) -> str:
        data = self.load(content)
        data[key] = int(new_replicas)
        return self.dump(data)

    def set_resources_helm(
        self,
        content: str,
        *,
        cpu_request: str | None = None,
        mem_request: str | None = None,
        resources_path: str = "resources",
    ) -> str:
        data = self.load(content)
        parts = resources_path.split(".")
        cursor: CommentedMap = data
        for part in parts:
            cursor = cursor.setdefault(part, CommentedMap())
            if not isinstance(cursor, CommentedMap):  # pragma: no cover
                raise ManifestEditError(f"path segment '{part}' is not a mapping")
        requests = cursor.setdefault("requests", CommentedMap())
        if cpu_request is not None:
            requests["cpu"] = cpu_request
        if mem_request is not None:
            requests["memory"] = mem_request
        return self.dump(data)

    # ── KEDA ScaledObject edits ───────────────────────────────────────
    def set_keda_min_replicas(self, content: str, new_min: int) -> str:
        data = self.load(content)
        spec = data.get("spec")
        if not isinstance(spec, CommentedMap):
            raise ManifestEditError("ScaledObject missing .spec mapping")
        spec["minReplicaCount"] = int(new_min)
        return self.dump(data)

    # ── Private helpers ───────────────────────────────────────────────
    def _get_containers(self, data: CommentedMap) -> CommentedSeq:
        try:
            containers = data["spec"]["template"]["spec"]["containers"]
        except (KeyError, TypeError) as exc:
            raise ManifestEditError("manifest missing .spec.template.spec.containers") from exc
        if not isinstance(containers, CommentedSeq) or len(containers) == 0:
            raise ManifestEditError("containers must be a non-empty list")
        return containers

    def _find_container(self, containers: CommentedSeq, name: str | None) -> CommentedMap:
        if name is None:
            first = containers[0]
            if not isinstance(first, CommentedMap):  # pragma: no cover
                raise ManifestEditError("container entry is not a mapping")
            return first
        for c in containers:
            if isinstance(c, CommentedMap) and c.get("name") == name:
                return c
        raise ManifestEditError(f"container named {name!r} not found")
