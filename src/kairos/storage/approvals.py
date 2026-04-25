"""Approval store — persists PendingApproval rows for the UI workflow."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import desc, func, select, update

from kairos.domain.enums import ApprovalStatus
from kairos.domain.models import LLMAdvice, PendingApproval, ScalingDecision
from kairos.storage.db import ApprovalRow, Database

log = structlog.get_logger(__name__)


def make_approval_id(decision: ScalingDecision) -> str:
    """Stable per-run approval id. Same run + same workload + same decision = same id."""
    raw = f"{decision.correlation_id}|{decision.workload.uid}|{decision.decision_hash()}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _row_to_model(row: ApprovalRow) -> PendingApproval:
    decision = ScalingDecision.model_validate(row.decision_json)
    advice = LLMAdvice.model_validate(row.advice_json) if row.advice_json else None
    return PendingApproval(
        id=row.id,
        decision=decision,
        advice=advice,
        status=ApprovalStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        rejection_reason=row.rejection_reason,
        pr_url=row.pr_url,  # type: ignore[arg-type]  # pydantic coerces str→HttpUrl
        pr_number=row.pr_number,
        error=row.error,
    )


class ApprovalStore:
    """CRUD for pending approvals. Backed by SQLAlchemy async."""

    def __init__(self, db: Database, *, pending_ttl_hours: int = 24) -> None:
        self._db = db
        self._ttl = timedelta(hours=pending_ttl_hours)

    async def enqueue(
        self,
        decision: ScalingDecision,
        advice: LLMAdvice | None = None,
    ) -> PendingApproval:
        """Insert a pending approval (idempotent by id)."""
        now = datetime.now(UTC)
        approval_id = make_approval_id(decision)

        async with self._db.session() as s:
            existing = await s.get(ApprovalRow, approval_id)
            if existing is not None:
                log.info(
                    "approval_already_exists",
                    id=approval_id,
                    status=existing.status,
                )
                return _row_to_model(existing)

            row = ApprovalRow(
                id=approval_id,
                decision_hash=decision.decision_hash(),
                workload_uid=decision.workload.uid,
                action=decision.action.value,
                severity=decision.severity.value,
                status=ApprovalStatus.PENDING.value,
                decision_json=decision.model_dump(mode="json"),
                advice_json=advice.model_dump(mode="json") if advice else None,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def get(self, approval_id: str) -> PendingApproval | None:
        async with self._db.session() as s:
            row = await s.get(ApprovalRow, approval_id)
            return _row_to_model(row) if row else None

    async def list_pending(self, *, limit: int = 200) -> list[PendingApproval]:
        async with self._db.session() as s:
            stmt = (
                select(ApprovalRow)
                .where(ApprovalRow.status == ApprovalStatus.PENDING.value)
                .order_by(desc(ApprovalRow.created_at))
                .limit(limit)
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [_row_to_model(r) for r in rows]

    async def list_recent(
        self,
        *,
        limit: int = 200,
        statuses: tuple[ApprovalStatus, ...] | None = None,
    ) -> list[PendingApproval]:
        async with self._db.session() as s:
            stmt = select(ApprovalRow).order_by(desc(ApprovalRow.updated_at)).limit(limit)
            if statuses:
                stmt = stmt.where(ApprovalRow.status.in_([st.value for st in statuses]))
            rows = (await s.execute(stmt)).scalars().all()
            return [_row_to_model(r) for r in rows]

    async def approve(self, approval_id: str, *, approved_by: str) -> PendingApproval | None:
        now = datetime.now(UTC)
        async with self._db.session() as s:
            row = await s.get(ApprovalRow, approval_id)
            if row is None or row.status != ApprovalStatus.PENDING.value:
                return _row_to_model(row) if row else None
            row.status = ApprovalStatus.APPROVED.value
            row.approved_by = approved_by
            row.approved_at = now
            row.updated_at = now
            await s.commit()
            await s.refresh(row)
            log.info(
                "approval_approved",
                id=approval_id,
                approved_by=approved_by,
                workload=row.workload_uid,
            )
            return _row_to_model(row)

    async def reject(
        self, approval_id: str, *, approved_by: str, reason: str
    ) -> PendingApproval | None:
        now = datetime.now(UTC)
        async with self._db.session() as s:
            row = await s.get(ApprovalRow, approval_id)
            if row is None or row.status != ApprovalStatus.PENDING.value:
                return _row_to_model(row) if row else None
            row.status = ApprovalStatus.REJECTED.value
            row.approved_by = approved_by
            row.approved_at = now
            row.updated_at = now
            row.rejection_reason = reason
            await s.commit()
            await s.refresh(row)
            log.info("approval_rejected", id=approval_id, approved_by=approved_by, reason=reason)
            return _row_to_model(row)

    async def mark_applied(
        self,
        approval_id: str,
        *,
        pr_url: str,
        pr_number: int,
    ) -> PendingApproval | None:
        now = datetime.now(UTC)
        async with self._db.session() as s:
            row = await s.get(ApprovalRow, approval_id)
            if row is None:
                return None
            row.status = ApprovalStatus.APPLIED.value
            row.pr_url = pr_url
            row.pr_number = pr_number
            row.updated_at = now
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def mark_failed(self, approval_id: str, *, error: str) -> PendingApproval | None:
        now = datetime.now(UTC)
        async with self._db.session() as s:
            row = await s.get(ApprovalRow, approval_id)
            if row is None:
                return None
            row.status = ApprovalStatus.FAILED.value
            row.error = error
            row.updated_at = now
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def expire_stale(self) -> int:
        """Mark pending rows older than TTL as expired. Returns count."""
        cutoff = datetime.now(UTC) - self._ttl
        async with self._db.session() as s:
            stmt = (
                update(ApprovalRow)
                .where(
                    ApprovalRow.status == ApprovalStatus.PENDING.value,
                    ApprovalRow.created_at < cutoff,
                )
                .values(status=ApprovalStatus.EXPIRED.value, updated_at=datetime.now(UTC))
            )
            result = await s.execute(stmt)
            await s.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    async def counts(self) -> dict[str, int]:
        async with self._db.session() as s:
            stmt = select(ApprovalRow.status, func.count()).group_by(ApprovalRow.status)
            rows = (await s.execute(stmt)).all()
            return {str(status): int(count) for status, count in rows}
