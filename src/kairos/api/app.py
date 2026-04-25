"""FastAPI application factory."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kairos import __version__
from kairos.api.routes import decisions, forecasts, health, runs, workloads
from kairos.config.logging import configure_logging, get_logger
from kairos.config.settings import Settings, get_settings
from kairos.observability.middleware import CorrelationIdMiddleware
from kairos.observability.tracing import configure_tracing

log = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _verify_token(settings: Settings, token: str | None) -> None:
    """Validate bearer token against configured SHA-256 digests. Empty list = open."""
    if not settings.api.token_sha256_list:
        return
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if digest not in settings.api.token_sha256_list:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bearer token")


async def require_auth(request: Request, credentials: BearerDep) -> None:
    settings: Settings = request.app.state.settings
    token = credentials.credentials if credentials else None
    _verify_token(settings, token)


async def _build_runtime_clients(app: FastAPI, settings: Settings) -> None:
    """(Re)build clients that depend on Settings overrides — Grafana + PR creator.

    Called from lifespan on startup, and from the admin route when the operator
    activates a different environment profile.
    """
    # Grafana client — optional; used by /ui/alerts and /ui/keda panels.
    app.state.grafana_client = None
    try:
        from kairos.grafana.grafana_client import GrafanaClient  # noqa: PLC0415

        app.state.grafana_client = GrafanaClient(settings.grafana)
        # Auto-provision a webhook contact point in Grafana so it knows where
        # to send Kairos-bound alerts. Best-effort: failures don't block startup.
        if settings.grafana.api_token is not None and settings.api.external_url is not None:
            try:
                from kairos.grafana.contact_points import (  # noqa: PLC0415
                    ensure_kairos_contact_point,
                )

                webhook = f"{str(settings.api.external_url).rstrip('/')}/api/v1/alerts/webhook"
                await ensure_kairos_contact_point(app.state.grafana_client, webhook_url=webhook)
            except Exception as exc:
                log.info("grafana_contact_point_provision_skipped", error=str(exc))
    except Exception as exc:
        log.info("grafana_client_unavailable", error=str(exc))

    # PR creator — real when enable_pr_creation=true, demo stub otherwise.
    app.state.pr_creator = None
    try:
        if settings.features.enable_pr_creation and settings.github.repo and settings.github.token:
            from kairos.gitops.github_client import GitHubClient, PRCreator  # noqa: PLC0415
            from kairos.gitops.repo_layout import RepoLayout  # noqa: PLC0415
            from kairos.storage.dedup import DedupStore  # noqa: PLC0415
            from kairos.storage.redis_client import RedisClient  # noqa: PLC0415

            gh = GitHubClient(settings.github)
            redis_client = RedisClient.from_settings(settings.redis)
            dedup_store = DedupStore(
                redis_client,
                ttl_pr=settings.redis.dedup_ttl_pr_seconds,
                ttl_notify=settings.redis.dedup_ttl_notify_seconds,
                ttl_forecast=settings.redis.dedup_ttl_forecast_seconds,
            )
            app.state.pr_creator = PRCreator(
                gh,
                dedup_store,
                RepoLayout(
                    base_branch=settings.github.base_branch,
                    branch_prefix=settings.github.branch_prefix,
                ),
                github_settings=settings.github,
                dry_run=settings.features.dry_run,
            )
        else:
            from kairos.api.demo_pr import DemoPRCreator  # noqa: PLC0415

            app.state.pr_creator = DemoPRCreator()
    except Exception as exc:
        log.warning("pr_creator_bootstrap_failed", error=str(exc))


async def reload_active_environment(app: FastAPI) -> None:
    """Re-resolve the active env profile and rebuild dependent clients.

    Called on startup (in lifespan) and after an admin activate/update/delete.
    Updates `app.state.settings` to be the merged effective config; keeps the
    original env-var settings on `app.state.settings_base`.
    """
    base: Settings = app.state.settings_base
    profile = None
    profiles_store = getattr(app.state, "env_profiles", None)
    if profiles_store is not None:
        try:
            profile = await profiles_store.get_active()
        except Exception as exc:
            log.warning("env_profile_load_failed", error=str(exc))

    from kairos.storage.env_profiles import apply_active_profile  # noqa: PLC0415

    effective = apply_active_profile(base, profile)
    app.state.settings = effective
    app.state.active_env_profile = profile
    await _build_runtime_clients(app, effective)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.settings_base = settings
    configure_logging(settings)
    configure_tracing(settings)

    # Audit DB + approvals: best-effort bootstrap. Failures log-and-skip so
    # the API stays up even when the DB isn't reachable.
    app.state.db = None
    app.state.sql_audit = None
    app.state.approvals = None
    app.state.env_profiles = None
    try:
        from kairos.storage.approvals import ApprovalStore  # noqa: PLC0415
        from kairos.storage.db import Database  # noqa: PLC0415
        from kairos.storage.env_profiles import EnvironmentProfileStore  # noqa: PLC0415
        from kairos.storage.sql_audit_store import SQLAuditStore  # noqa: PLC0415

        db = Database.from_settings(settings.audit_db)
        await db.create_all()
        app.state.db = db
        app.state.sql_audit = SQLAuditStore(db)
        app.state.approvals = ApprovalStore(
            db, pending_ttl_hours=settings.audit_db.pending_ttl_hours
        )
        # Incoming-alerts store (Grafana webhook receiver target)
        from kairos.storage.alerts import IncomingAlertStore  # noqa: PLC0415

        app.state.incoming_alerts = IncomingAlertStore(db)
        app.state.env_profiles = EnvironmentProfileStore(db)
        # Seed starter profiles (nonprod + prod) on first boot so the operator
        # has something to edit. No-op on subsequent boots.
        try:
            seeded = await app.state.env_profiles.seed_starter_profiles(settings)
            if seeded:
                log.info("env_profiles_starter_seeded")
        except Exception as exc:
            log.warning("env_profiles_seed_failed", error=str(exc))
    except Exception as exc:
        log.warning("audit_db_bootstrap_failed", error=str(exc))

    # Resolve active env profile + build runtime clients (Grafana, PRCreator).
    await reload_active_environment(app)
    settings = app.state.settings  # may now reflect the active profile

    log.info(
        "kairos_starting",
        environment=settings.environment,
        version=settings.version,
        dry_run=settings.features.dry_run,
        require_ui_approval=settings.features.require_ui_approval,
        ui_enabled=settings.features.enable_ui,
    )
    try:
        yield
    finally:
        current_db: Any = getattr(app.state, "db", None)
        if current_db is not None:
            await current_db.dispose()
        log.info("kairos_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Tests can pass their own Settings."""
    s = settings or get_settings()

    app = FastAPI(
        title="KAIROS — Predictive Capacity & Autoscaling Platform",
        version=__version__,
        description=(
            "Augments KEDA with 48h CPU/memory forecasts, deterministic decisions, "
            "GitOps PR automation, and a human-in-the-loop approval UI for AKS workloads."
        ),
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = s

    app.add_middleware(CorrelationIdMiddleware)
    if s.api.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.api.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    # Unauthenticated routes
    app.include_router(health.router)

    # UI + approval workflow (screens unauthenticated, API endpoints gated)
    if s.features.enable_ui:
        from kairos.ui.routes import build_ui_router  # noqa: PLC0415

        ui_router = build_ui_router()
        app.include_router(ui_router)

    # Authenticated routes
    auth_dep = [Depends(require_auth)]
    app.include_router(workloads.router, dependencies=auth_dep)
    app.include_router(forecasts.router, dependencies=auth_dep)
    app.include_router(decisions.router, dependencies=auth_dep)
    app.include_router(runs.router, dependencies=auth_dep)

    return app
