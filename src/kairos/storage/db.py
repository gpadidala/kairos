"""SQLAlchemy async engine + ORM models for durable audit + approvals.

Defaults to SQLite (`sqlite+aiosqlite:///./kairos-audit.db`). Swap to Postgres
by setting `KAIROS_AUDIT_DB__URL=postgresql+asyncpg://...`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import structlog
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from kairos.config.settings import AuditDBSettings

log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    pass


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_hash: Mapped[str] = mapped_column(String(32), index=True)
    workload_uid: Mapped[str] = mapped_column(String(253), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)

    decision_json: Mapped[dict[str, object]] = mapped_column(JSON)
    advice_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DecisionRow(Base):
    """Historical record of every decision produced by the pipeline."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    workload_uid: Mapped[str] = mapped_column(String(253), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    decision_hash: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    workloads_processed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PRRow(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(512))
    number: Mapped[int] = mapped_column(Integer)
    branch: Mapped[str] = mapped_column(String(253))
    dry_run: Mapped[int] = mapped_column(Integer, default=0)
    dedup_hit: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NotificationRow(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)
    delivered: Mapped[int] = mapped_column(Integer)
    dedup_hit: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AlertRow(Base):
    """Alerts received from Grafana via webhook."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    workload_uid: Mapped[str | None] = mapped_column(String(253), nullable=True, index=True)
    portfolio: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    program: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    team: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    labels_json: Mapped[dict[str, object]] = mapped_column(JSON)
    annotations_json: Mapped[dict[str, object]] = mapped_column(JSON)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    silence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    panel_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_json: Mapped[dict[str, object]] = mapped_column(JSON)


class EnvironmentProfileRow(Base):
    """Operator-managed environment profile.

    A profile is a named bundle of connection overrides (Grafana / Mimir /
    GitHub / API external URL). At most one profile is `is_active=True` at a
    time. The active profile is merged on top of env-var Settings to produce
    the effective runtime configuration.
    """

    __tablename__ = "environment_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=0, index=True)

    grafana_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grafana_external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grafana_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    mimir_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mimir_org_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mimir_bearer: Mapped[str | None] = mapped_column(Text, nullable=True)

    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_base_branch: Mapped[str | None] = mapped_column(String(64), nullable=True)

    api_external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Database:
    """Owns the async engine + sessionmaker."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    @classmethod
    def from_settings(cls, settings: AuditDBSettings) -> Database:
        # For SQLite, use NullPool so each session gets a fresh connection —
        # avoids stale reads when external processes (seeds, maintenance) write
        # to the same file while the server is running.
        kwargs: dict[str, object] = {"echo": settings.echo, "future": True}
        if settings.url.startswith("sqlite"):
            kwargs["poolclass"] = NullPool
        else:
            kwargs["pool_pre_ping"] = True
        engine = create_async_engine(settings.url, **kwargs)
        return cls(engine)

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("audit_db_ready", url=str(self._engine.url).split("@")[-1])

    async def dispose(self) -> None:
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as s:
            yield s
