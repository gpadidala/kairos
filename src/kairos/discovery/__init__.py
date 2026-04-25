"""Workload discovery + runtime detection."""

from kairos.discovery.runtime_detector import detect_runtime
from kairos.discovery.workload_discovery import (
    StaticWorkloadSource,
    WorkloadDiscovery,
    WorkloadSource,
)

__all__ = [
    "StaticWorkloadSource",
    "WorkloadDiscovery",
    "WorkloadSource",
    "detect_runtime",
]
