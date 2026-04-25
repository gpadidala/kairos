"""Workload discovery — static YAML path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kairos.config.settings import K8sSettings
from kairos.discovery.workload_discovery import StaticWorkloadSource, WorkloadDiscovery
from kairos.domain.exceptions import ConfigurationError


@pytest.fixture
def static_workloads_file(tmp_path: Path) -> Path:
    data = [
        {
            "name": "api",
            "namespace": "prod",
            "kind": "Deployment",
            "runtime": "jvm",
            "current_replicas": 3,
            "cpu_request": "500m",
            "mem_request": "1Gi",
            "gitops_path": "apps/api",
            "labels": {"app": "api"},
            "annotations": {},
        },
        {
            "name": "worker",
            "namespace": "prod",
            "kind": "Deployment",
            "runtime": "python",
            "current_replicas": 2,
            "cpu_request": "200m",
            "mem_request": "512Mi",
            "gitops_path": "apps/worker",
            "labels": {},
            "annotations": {"kairos.io/exclude": "true"},
        },
    ]
    f = tmp_path / "workloads.yaml"
    f.write_text(yaml.safe_dump(data))
    return f


async def test_static_source_reads_yaml(static_workloads_file: Path) -> None:
    src = StaticWorkloadSource(static_workloads_file)
    wls = await src.list_workloads()
    assert len(wls) == 2
    assert wls[0].name == "api"
    assert wls[1].is_excluded is True


async def test_discovery_excludes_annotated(static_workloads_file: Path) -> None:
    disc = WorkloadDiscovery(StaticWorkloadSource(static_workloads_file))
    wls = await disc.list()
    assert len(wls) == 1
    assert wls[0].name == "api"


async def test_discovery_from_settings_static(static_workloads_file: Path) -> None:
    s = K8sSettings(mode="static", static_workloads_file=str(static_workloads_file))
    disc = WorkloadDiscovery.from_settings(s)
    wls = await disc.list()
    assert len(wls) == 1


def test_discovery_from_settings_static_requires_file() -> None:
    s = K8sSettings(mode="static", static_workloads_file=None)
    with pytest.raises(ConfigurationError):
        WorkloadDiscovery.from_settings(s)


async def test_missing_file_raises() -> None:
    src = StaticWorkloadSource("/nonexistent/workloads.yaml")
    with pytest.raises(ConfigurationError):
        await src.list_workloads()
