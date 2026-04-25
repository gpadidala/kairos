"""SQL-backed audit store — writes runs/decisions/PRs/notifications to the DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import desc, select

from kairos.domain.enums import ApprovalStatus
from kairos.domain.models import NotificationResult, PRResult, RunResult, ScalingDecision
from kairos.storage.db import (
    ApprovalRow,
    Database,
    DecisionRow,
    NotificationRow,
    PRRow,
    RunRow,
)

log = structlog.get_logger(__name__)


class SQLAuditStore:
    """Durable audit — queryable from the UI + CLI."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_run(self, run: RunResult) -> None:
        async with self._db.session() as s:
            existing = await s.get(RunRow, run.run_id)
            if existing is None:
                s.add(
                    RunRow(
                        id=run.run_id,
                        status=run.status,
                        workloads_processed=run.workloads_processed,
                        started_at=run.started_at,
                        ended_at=run.ended_at,
                        error=run.error,
                    )
                )
            else:
                existing.status = run.status
                existing.workloads_processed = run.workloads_processed
                existing.ended_at = run.ended_at
                existing.error = run.error
            await s.commit()

    async def record_decision(self, run_id: str, decision: ScalingDecision) -> None:
        async with self._db.session() as s:
            s.add(
                DecisionRow(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    workload_uid=decision.workload.uid,
                    action=decision.action.value,
                    reason_code=decision.reason_code,
                    severity=decision.severity.value,
                    decision_hash=decision.decision_hash(),
                    payload_json=decision.model_dump(mode="json"),
                    created_at=decision.generated_at,
                )
            )
            await s.commit()

    async def record_pr(self, run_id: str, pr: PRResult) -> None:
        async with self._db.session() as s:
            s.add(
                PRRow(
                    run_id=run_id,
                    url=str(pr.url),
                    number=pr.number,
                    branch=pr.branch,
                    dry_run=int(pr.dry_run),
                    dedup_hit=int(pr.dedup_hit),
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()

    async def record_notification(self, run_id: str, result: NotificationResult) -> None:
        async with self._db.session() as s:
            s.add(
                NotificationRow(
                    run_id=run_id,
                    channel=result.channel.value,
                    delivered=int(result.delivered),
                    dedup_hit=int(result.dedup_hit),
                    error=result.error,
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()

    # ── Query helpers used by the UI + API ────────────────────────────
    async def recent_decisions(self, *, limit: int = 100) -> list[DecisionRow]:
        async with self._db.session() as s:
            stmt = select(DecisionRow).order_by(desc(DecisionRow.created_at)).limit(limit)
            return list((await s.execute(stmt)).scalars().all())

    async def recent_runs(self, *, limit: int = 50) -> list[RunRow]:
        async with self._db.session() as s:
            stmt = select(RunRow).order_by(desc(RunRow.started_at)).limit(limit)
            return list((await s.execute(stmt)).scalars().all())

    async def recent_prs(self, *, limit: int = 100) -> list[PRRow]:
        async with self._db.session() as s:
            stmt = select(PRRow).order_by(desc(PRRow.created_at)).limit(limit)
            return list((await s.execute(stmt)).scalars().all())

    async def get_decision(self, decision_id: str) -> ScalingDecision | None:
        async with self._db.session() as s:
            row = await s.get(DecisionRow, decision_id)
            if row is None:
                return None
            return ScalingDecision.model_validate(row.payload_json)

    async def list_decisions(self, limit: int, offset: int) -> list[ScalingDecision]:
        async with self._db.session() as s:
            stmt = (
                select(DecisionRow)
                .order_by(desc(DecisionRow.created_at))
                .limit(limit)
                .offset(offset)
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [ScalingDecision.model_validate(r.payload_json) for r in rows]

    async def counters_24h(self) -> dict[str, int]:
        """Summary counters for the dashboard landing page."""
        now = datetime.now(UTC)
        async with self._db.session() as s:
            dec = (
                (
                    await s.execute(
                        select(DecisionRow).where(DecisionRow.created_at >= now.replace(hour=0))
                    )
                )
                .scalars()
                .all()
            )
            prs = (
                (await s.execute(select(PRRow).where(PRRow.created_at >= now.replace(hour=0))))
                .scalars()
                .all()
            )
            pending = (
                (
                    await s.execute(
                        select(ApprovalRow).where(
                            ApprovalRow.status == ApprovalStatus.PENDING.value
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {
                "decisions_today": len(dec),
                "prs_today": len(prs),
                "pending_approvals": len(pending),
            }

    async def aclose(self) -> None:
        return None
