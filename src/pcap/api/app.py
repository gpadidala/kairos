"""FastAPI application factory."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pcap import __version__
from pcap.api.routes import decisions, forecasts, health, runs, workloads
from pcap.config.logging import configure_logging, get_logger
from pcap.config.settings import Settings, get_settings
from pcap.observability.middleware import CorrelationIdMiddleware
from pcap.observability.tracing import configure_tracing

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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings)
    configure_tracing(settings)
    log.info(
        "pcap_starting",
        environment=settings.environment,
        version=settings.version,
        dry_run=settings.features.dry_run,
    )
    try:
        yield
    finally:
        log.info("pcap_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Tests can pass their own Settings."""
    s = settings or get_settings()

    app = FastAPI(
        title="PCAP — Predictive Capacity & Autoscaling Platform",
        version=__version__,
        description=(
            "Augments KEDA with 48h CPU/memory forecasts, deterministic decisions, "
            "and GitOps PR automation for AKS workloads."
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

    # Authenticated routes
    auth_dep = [Depends(require_auth)]
    app.include_router(workloads.router, dependencies=auth_dep)
    app.include_router(forecasts.router, dependencies=auth_dep)
    app.include_router(decisions.router, dependencies=auth_dep)
    app.include_router(runs.router, dependencies=auth_dep)

    return app
