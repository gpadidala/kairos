"""Workload discovery — K8s API (read-only) or static YAML."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import TypeAdapter

from pcap.config.settings import K8sSettings
from pcap.discovery.runtime_detector import detect_runtime
from pcap.domain.enums import WorkloadKind
from pcap.domain.exceptions import ConfigurationError
from pcap.domain.models import Workload

log = structlog.get_logger(__name__)


class WorkloadSource(ABC):
    """A source that can enumerate Workload objects."""

    @abstractmethod
    async def list_workloads(self) -> list[Workload]: ...


class StaticWorkloadSource(WorkloadSource):
    """Reads a YAML file containing a list of workload dicts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def list_workloads(self) -> list[Workload]:
        if not self._path.exists():
            raise ConfigurationError(f"static workloads file not found: {self._path}")
        with self._path.open("r") as f:
            raw = yaml.safe_load(f)
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ConfigurationError(
                f"static workloads file {self._path} must be a list, got {type(raw).__name__}"
            )
        adapter: TypeAdapter[list[Workload]] = TypeAdapter(list[Workload])
        return adapter.validate_python(raw)


class K8sApiWorkloadSource(WorkloadSource):
    """Read-only K8s API discovery. Requires in-cluster or kubeconfig auth."""

    def __init__(self, namespaces: list[str]) -> None:
        self._namespaces = namespaces

    async def list_workloads(self) -> list[Workload]:
        try:
            from kubernetes_asyncio import (  # noqa: PLC0415
                client,
                config,
            )
        except ImportError as exc:  # pragma: no cover
            raise ConfigurationError(
                "kubernetes-asyncio not installed — cannot use k8s API discovery"
            ) from exc

        try:
            config.load_incluster_config()  # type: ignore[no-untyped-call]
        except Exception:
            try:
                await config.load_kube_config()
            except Exception as exc:
                raise ConfigurationError(f"unable to load k8s config: {exc}") from exc

        apps_v1 = client.AppsV1Api()
        workloads: list[Workload] = []

        for ns in self._namespaces or [""]:
            for kind, lister in (
                (WorkloadKind.DEPLOYMENT, apps_v1.list_namespaced_deployment),
                (WorkloadKind.STATEFULSET, apps_v1.list_namespaced_stateful_set),
                (WorkloadKind.DAEMONSET, apps_v1.list_namespaced_daemon_set),
            ):
                try:
                    resp = await lister(namespace=ns) if ns else await lister(namespace="default")
                except Exception as exc:
                    log.warning("k8s_list_failed", kind=kind.value, ns=ns, error=str(exc))
                    continue
                for item in resp.items:
                    wl = _k8s_obj_to_workload(item, kind)
                    if wl is not None:
                        workloads.append(wl)

        await client.ApiClient().close()
        return workloads


def _k8s_obj_to_workload(obj: Any, kind: WorkloadKind) -> Workload | None:  # pragma: no cover
    meta = obj.metadata
    spec = obj.spec
    annotations = dict(meta.annotations or {})
    labels = dict(meta.labels or {})

    if annotations.get("pcap.io/exclude", "").lower() == "true":
        return None

    containers = getattr(getattr(spec, "template", None), "spec", None)
    image = ""
    cpu_req = "100m"
    mem_req = "128Mi"
    cpu_lim: str | None = None
    mem_lim: str | None = None
    if containers and containers.containers:
        c0 = containers.containers[0]
        image = c0.image or ""
        r = getattr(c0, "resources", None)
        if r:
            req = r.requests or {}
            lim = r.limits or {}
            cpu_req = req.get("cpu", cpu_req)
            mem_req = req.get("memory", mem_req)
            cpu_lim = lim.get("cpu")
            mem_lim = lim.get("memory")

    runtime = detect_runtime(annotations=annotations, labels=labels, image=image)
    current = int(getattr(getattr(obj, "status", None), "replicas", 1) or 1)

    return Workload(
        name=meta.name,
        namespace=meta.namespace,
        kind=kind,
        runtime=runtime,
        labels=labels,
        annotations=annotations,
        current_replicas=current,
        cpu_request=cpu_req,
        cpu_limit=cpu_lim,
        mem_request=mem_req,
        mem_limit=mem_lim,
        keda_scaledobject=annotations.get("pcap.io/keda-scaledobject"),
        gitops_path=annotations.get("pcap.io/gitops-path"),
    )


class WorkloadDiscovery:
    """Top-level discovery facade. Picks a source from K8sSettings."""

    def __init__(self, source: WorkloadSource) -> None:
        self._source = source

    @classmethod
    def from_settings(cls, settings: K8sSettings) -> WorkloadDiscovery:
        if settings.mode == "static":
            if not settings.static_workloads_file:
                raise ConfigurationError("k8s.mode=static requires PCAP_K8S__STATIC_WORKLOADS_FILE")
            return cls(StaticWorkloadSource(settings.static_workloads_file))
        if settings.mode in ("in_cluster", "kubeconfig"):
            return cls(K8sApiWorkloadSource(settings.namespaces))
        raise ConfigurationError(f"unknown k8s.mode: {settings.mode}")

    async def list(self) -> list[Workload]:
        wls = await self._source.list_workloads()
        included = [w for w in wls if not w.is_excluded]
        log.info(
            "discovery_complete",
            total=len(wls),
            included=len(included),
            excluded=len(wls) - len(included),
        )
        return included
