"""Resolve paths inside the GitOps repo from a Workload."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from kairos.domain.models import Workload


@dataclass(frozen=True, slots=True)
class RepoLayout:
    """Convention over config. Override by setting kairos.io/gitops-path on the workload."""

    base_branch: str = "main"
    branch_prefix: str = "kairos"

    def workload_dir(self, w: Workload) -> PurePosixPath:
        if w.gitops_path:
            return PurePosixPath(w.gitops_path)
        # Default convention: apps/<namespace>/<name>
        return PurePosixPath("apps") / w.namespace / w.name

    def branch_name(self, w: Workload, timestamp: str) -> str:
        """`kairos/{namespace}-{name}-{yyyymmdd-hhmm}`."""
        slug = f"{w.namespace}-{w.name}".replace("/", "-")
        return f"{self.branch_prefix}/{slug}-{timestamp}"
