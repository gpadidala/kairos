"""Audit store — Postgres (optional). Fall back to JSON log when disabled."""

from __future__ import annotations

from typing import Protocol

import structlog

from kairos.config.settings import PostgresSettings
from kairos.domain.models import NotificationResult, PRResult, RunResult, ScalingDecision

log = structlog.get_logger(__name__)


class AuditStore(Protocol):
    async def record_run(self, run: RunResult) -> None: ...
    async def record_decision(self, run_id: str, decision: ScalingDecision) -> None: ...
    async def record_pr(self, run_id: str, pr: PRResult) -> None: ...
    async def record_notification(self, run_id: str, result: NotificationResult) -> None: ...
    async def get_decision(self, decision_id: str) -> ScalingDecision | None: ...
    async def list_decisions(self, limit: int, offset: int) -> list[ScalingDecision]: ...
    async def aclose(self) -> None: ...


class JSONLogAuditStore:
    """Fallback audit: emits structured log lines. No query/retrieval."""

    async def record_run(self, run: RunResult) -> None:
        log.info("audit_run", run=run.model_dump(mode="json"))

    async def record_decision(self, run_id: str, decision: ScalingDecision) -> None:
        log.info(
            "audit_decision",
            run_id=run_id,
            workload=decision.workload.uid,
            action=decision.action.value,
            reason=decision.reason_code,
            decision_hash=decision.decision_hash(),
        )

    async def record_pr(self, run_id: str, pr: PRResult) -> None:
        log.info(
            "audit_pr",
            run_id=run_id,
            pr_url=str(pr.url),
            pr_number=pr.number,
            dry_run=pr.dry_run,
            dedup_hit=pr.dedup_hit,
        )

    async def record_notification(self, run_id: str, result: NotificationResult) -> None:
        log.info(
            "audit_notification",
            run_id=run_id,
            channel=result.channel.value,
            delivered=result.delivered,
            dedup_hit=result.dedup_hit,
            error=result.error,
        )

    async def get_decision(self, decision_id: str) -> ScalingDecision | None:
        return None

    async def list_decisions(self, limit: int, offset: int) -> list[ScalingDecision]:
        _ = (limit, offset)
        return []

    async def aclose(self) -> None:
        return None


def audit_store_from_settings(settings: PostgresSettings) -> AuditStore:
    """Return a Postgres-backed store when enabled, else JSONLogAuditStore.

    Note: the Postgres implementation is deferred to a future phase; in v1 we ship
    the JSON-log store, which satisfies §11's documented fallback behavior.
    """
    if settings.enabled:
        log.info("postgres_audit_requested_but_using_json_fallback")
    return JSONLogAuditStore()
