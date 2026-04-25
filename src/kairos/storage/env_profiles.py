"""Environment-profile store: CRUD + active-profile resolution.

A profile is a named override bundle. Operators create profiles like "dev",
"staging", "prod" via the admin UI and activate one at a time. The active
profile is merged on top of env-var Settings to produce effective runtime
configuration.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field, HttpUrl, SecretStr
from sqlalchemy import select, update

from kairos.config.settings import (
    APISettings,
    CostSettings,
    GitHubSettings,
    GrafanaSettings,
    MimirSettings,
    Settings,
)
from kairos.storage.db import Database, EnvironmentProfileRow

log = structlog.get_logger(__name__)


class EnvironmentProfile(BaseModel):
    """API-facing model for an environment profile."""

    id: str
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    is_active: bool = False

    grafana_url: str | None = None
    grafana_external_url: str | None = None
    grafana_token: str | None = None

    mimir_url: str | None = None
    mimir_org_id: str | None = None
    mimir_bearer: str | None = None

    github_repo: str | None = None
    github_token: str | None = None
    github_base_branch: str | None = None

    api_external_url: str | None = None

    cost_cpu_per_hour: float | None = None
    cost_mem_gib_per_hour: float | None = None
    cost_currency: str | None = None

    created_at: datetime
    updated_at: datetime


def _row_to_model(row: EnvironmentProfileRow) -> EnvironmentProfile:
    return EnvironmentProfile(
        id=row.id,
        name=row.name,
        description=row.description,
        is_active=bool(row.is_active),
        grafana_url=row.grafana_url,
        grafana_external_url=row.grafana_external_url,
        grafana_token=row.grafana_token,
        mimir_url=row.mimir_url,
        mimir_org_id=row.mimir_org_id,
        mimir_bearer=row.mimir_bearer,
        github_repo=row.github_repo,
        github_token=row.github_token,
        github_base_branch=row.github_base_branch,
        api_external_url=row.api_external_url,
        cost_cpu_per_hour=row.cost_cpu_per_hour,
        cost_mem_gib_per_hour=row.cost_mem_gib_per_hour,
        cost_currency=row.cost_currency,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class EnvironmentProfileStore:
    """Persists named environment profiles and tracks the active one."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[EnvironmentProfile]:
        async with self._db.session() as s:
            rows = (await s.execute(select(EnvironmentProfileRow))).scalars().all()
            return sorted(
                (_row_to_model(r) for r in rows),
                key=lambda p: (not p.is_active, p.name.lower()),
            )

    async def get(self, profile_id: str) -> EnvironmentProfile | None:
        async with self._db.session() as s:
            row = await s.get(EnvironmentProfileRow, profile_id)
            return _row_to_model(row) if row else None

    async def get_active(self) -> EnvironmentProfile | None:
        async with self._db.session() as s:
            stmt = select(EnvironmentProfileRow).where(EnvironmentProfileRow.is_active == 1)
            row = (await s.execute(stmt)).scalar_one_or_none()
            return _row_to_model(row) if row else None

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        grafana_url: str | None = None,
        grafana_external_url: str | None = None,
        grafana_token: str | None = None,
        mimir_url: str | None = None,
        mimir_org_id: str | None = None,
        mimir_bearer: str | None = None,
        github_repo: str | None = None,
        github_token: str | None = None,
        github_base_branch: str | None = None,
        api_external_url: str | None = None,
        cost_cpu_per_hour: float | None = None,
        cost_mem_gib_per_hour: float | None = None,
        cost_currency: str | None = None,
    ) -> EnvironmentProfile:
        now = datetime.now(UTC)
        pid = str(uuid.uuid4())
        async with self._db.session() as s:
            row = EnvironmentProfileRow(
                id=pid,
                name=name,
                description=description,
                is_active=0,
                grafana_url=grafana_url,
                grafana_external_url=grafana_external_url,
                grafana_token=grafana_token,
                mimir_url=mimir_url,
                mimir_org_id=mimir_org_id,
                mimir_bearer=mimir_bearer,
                github_repo=github_repo,
                github_token=github_token,
                github_base_branch=github_base_branch,
                api_external_url=api_external_url,
                cost_cpu_per_hour=cost_cpu_per_hour,
                cost_mem_gib_per_hour=cost_mem_gib_per_hour,
                cost_currency=cost_currency,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def update(self, profile_id: str, **fields: object) -> EnvironmentProfile | None:
        async with self._db.session() as s:
            row = await s.get(EnvironmentProfileRow, profile_id)
            if row is None:
                return None
            for key, val in fields.items():
                if hasattr(row, key) and key not in {"id", "is_active", "created_at"}:
                    setattr(row, key, val)
            row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return _row_to_model(row)

    async def delete(self, profile_id: str) -> bool:
        async with self._db.session() as s:
            row = await s.get(EnvironmentProfileRow, profile_id)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def activate(self, profile_id: str) -> EnvironmentProfile | None:
        """Mark exactly one profile active, deactivate all others."""
        async with self._db.session() as s:
            target = await s.get(EnvironmentProfileRow, profile_id)
            if target is None:
                return None
            await s.execute(update(EnvironmentProfileRow).values(is_active=0))
            target.is_active = 1
            target.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(target)
            log.info("env_profile_activated", id=target.id, name=target.name)
            return _row_to_model(target)

    async def deactivate_all(self) -> None:
        async with self._db.session() as s:
            await s.execute(update(EnvironmentProfileRow).values(is_active=0))
            await s.commit()

    async def seed_starter_profiles(self, base: Settings) -> bool:
        """If no profiles exist, create `nonprod` (active) + `prod` (inactive).

        Both copy the current env-var-resolved URLs as a starting point. Tokens
        are intentionally left blank so they fall through to the env-var
        SecretStr defaults — the operator fills them in via /ui/admin/envs/<id>.

        Returns True if seeding actually happened.
        """
        async with self._db.session() as s:
            existing = (await s.execute(select(EnvironmentProfileRow))).scalars().all()
            if list(existing):
                return False

        # nonprod — pre-populated from current env vars, activated. Hot-reload
        # treats empty-fields-with-active-profile identically to no-profile, so
        # this is a no-op functionally; it just gives the operator a card to edit.
        nonprod = await self.create(
            name="nonprod",
            description="Non-production (dev / staging / QA) — copies current env-var values",
            grafana_url=str(base.grafana.url) if base.grafana.url else None,
            grafana_external_url=(
                str(base.grafana.external_url) if base.grafana.external_url else None
            ),
            grafana_token=None,
            mimir_url=str(base.mimir.url) if base.mimir.url else None,
            mimir_org_id=base.mimir.org_id,
            mimir_bearer=None,
            github_repo=base.github.repo or None,
            github_token=None,
            github_base_branch=base.github.base_branch or None,
            api_external_url=str(base.api.external_url) if base.api.external_url else None,
        )
        await self.activate(nonprod.id)

        # prod — same starting point so the operator can see the diff. They
        # edit the URLs / tokens to point at production. Stays inactive until
        # explicitly activated.
        await self.create(
            name="prod",
            description="Production — edit URLs + tokens, then activate when ready",
            grafana_url=str(base.grafana.url) if base.grafana.url else None,
            grafana_external_url=(
                str(base.grafana.external_url) if base.grafana.external_url else None
            ),
            grafana_token=None,
            mimir_url=str(base.mimir.url) if base.mimir.url else None,
            mimir_org_id=base.mimir.org_id,
            mimir_bearer=None,
            github_repo=base.github.repo or None,
            github_token=None,
            github_base_branch=base.github.base_branch or None,
            api_external_url=str(base.api.external_url) if base.api.external_url else None,
        )
        log.info("env_profiles_seeded", profiles=["nonprod", "prod"], active="nonprod")
        return True


def apply_active_profile(  # noqa: PLR0912 — flat per-section merge is clearer than refactor
    base: Settings, profile: EnvironmentProfile | None
) -> Settings:
    """Return a Settings copy with the active profile's non-empty fields merged in.

    Pydantic v2 model_copy(update=...) clones the Settings without re-running env-var
    discovery, so this is safe to call repeatedly.
    """
    if profile is None:
        return base

    grafana_updates: dict[str, object] = {}
    if profile.grafana_url:
        grafana_updates["url"] = HttpUrl(profile.grafana_url)
    if profile.grafana_external_url:
        grafana_updates["external_url"] = HttpUrl(profile.grafana_external_url)
    if profile.grafana_token:
        grafana_updates["api_token"] = SecretStr(profile.grafana_token)
    grafana = (
        base.grafana.model_copy(update=grafana_updates) if grafana_updates else base.grafana
    )

    mimir_updates: dict[str, object] = {}
    if profile.mimir_url:
        mimir_updates["url"] = HttpUrl(profile.mimir_url)
    if profile.mimir_org_id:
        mimir_updates["org_id"] = profile.mimir_org_id
    if profile.mimir_bearer:
        mimir_updates["auth_bearer"] = SecretStr(profile.mimir_bearer)
    mimir = base.mimir.model_copy(update=mimir_updates) if mimir_updates else base.mimir

    github_updates: dict[str, object] = {}
    if profile.github_repo:
        github_updates["repo"] = profile.github_repo
    if profile.github_token:
        github_updates["token"] = SecretStr(profile.github_token)
    if profile.github_base_branch:
        github_updates["base_branch"] = profile.github_base_branch
    github = base.github.model_copy(update=github_updates) if github_updates else base.github

    api_updates: dict[str, object] = {}
    if profile.api_external_url:
        api_updates["external_url"] = HttpUrl(profile.api_external_url)
    api = base.api.model_copy(update=api_updates) if api_updates else base.api

    cost_updates: dict[str, object] = {}
    if profile.cost_cpu_per_hour is not None and profile.cost_cpu_per_hour > 0:
        cost_updates["cpu_per_hour"] = profile.cost_cpu_per_hour
    if profile.cost_mem_gib_per_hour is not None and profile.cost_mem_gib_per_hour > 0:
        cost_updates["mem_gib_per_hour"] = profile.cost_mem_gib_per_hour
    if profile.cost_currency:
        cost_updates["currency"] = profile.cost_currency
    cost = base.cost.model_copy(update=cost_updates) if cost_updates else base.cost

    # Confirm types for the cast — model_copy preserves model type
    assert isinstance(grafana, GrafanaSettings)
    assert isinstance(mimir, MimirSettings)
    assert isinstance(github, GitHubSettings)
    assert isinstance(api, APISettings)
    assert isinstance(cost, CostSettings)

    return base.model_copy(
        update={
            "grafana": grafana,
            "mimir": mimir,
            "github": github,
            "api": api,
            "cost": cost,
        }
    )
