"""Demo PR creator — simulates opening a GitOps PR without calling GitHub.

Used when `enable_pr_creation=false` (the default for local demos and dry-run
environments). Returns a deterministic fake URL + number so the UI history page
shows the flow end-to-end.
"""

from __future__ import annotations

import hashlib
import itertools

from pydantic import HttpUrl

from pcap.domain.enums import ScalingAction
from pcap.domain.models import LLMAdvice, PRResult, ScalingDecision


class DemoPRCreator:
    """A mock that returns PRResult objects without any network I/O."""

    def __init__(self) -> None:
        self._counter = itertools.count(start=1)
        self._dry_run = False

    async def create_pr_for_decision(
        self,
        decision: ScalingDecision,
        *,
        advice: LLMAdvice | None = None,
    ) -> PRResult | None:
        _ = advice
        if decision.action in (
            ScalingAction.NOOP,
            ScalingAction.HUMAN_APPROVAL_REQUIRED,
            ScalingAction.NODE_POOL_ADVISORY,
        ):
            return None

        number = next(self._counter)
        uid_hash = hashlib.sha1(
            decision.decision_hash().encode(), usedforsecurity=False
        ).hexdigest()[:8]
        branch = f"pcap/demo-{decision.workload.namespace}-{decision.workload.name}-{uid_hash}"

        return PRResult(
            url=HttpUrl(f"https://github.com/demo/gitops/pull/{number}"),
            number=number,
            branch=branch,
            files_changed=[f"apps/{decision.workload.name}/deployment.yaml"],
            dry_run=self._dry_run,
        )
