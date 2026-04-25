"""Health, readiness, Prometheus metrics, and service-status endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from kairos import __version__
from kairos.observability.metrics import REGISTRY

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]


class ServiceStatus(BaseModel):
    """One row in the live status pill."""

    name: str
    state: str  # "ok" | "degraded" | "down" | "unknown"
    detail: str | None = None


class StatusResponse(BaseModel):
    overall: str  # "ok" | "degraded" | "down"
    services: list[ServiceStatus]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness. Always 200 if the process is alive."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(response: Response) -> ReadinessResponse:
    checks: dict[str, bool] = {"app": True}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=ready, checks=checks)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/status", response_model=StatusResponse, include_in_schema=False)
async def status_endpoint(request: Request) -> StatusResponse:
    """Live service status — polled by the UI header pill every 10s.

    Probes: KAIROS itself (always ok if we got here), Redis (via approval store ping),
    Mimir, Grafana. Each check is best-effort; a failed probe becomes a "down" row
    rather than a 5xx.
    """
    state: Any = request.app.state
    services: list[ServiceStatus] = [
        ServiceStatus(name="KAIROS", state="ok", detail=__version__),
    ]

    # Audit DB
    db = getattr(state, "db", None)
    if db is None:
        services.append(ServiceStatus(name="audit DB", state="down", detail="not bootstrapped"))
    else:
        services.append(ServiceStatus(name="audit DB", state="ok", detail="sqlite"))

    # Mimir + Grafana — quick HEAD/GET
    mimir_url = str(state.settings.mimir.url).rstrip("/")
    grafana_url = str(state.settings.grafana.url).rstrip("/")
    services.append(await _probe_http(name="Mimir", url=f"{mimir_url}/ready"))
    services.append(await _probe_http(name="Grafana", url=f"{grafana_url}/api/health"))

    # Approvals store
    approvals = getattr(state, "approvals", None)
    services.append(
        ServiceStatus(
            name="approvals",
            state="ok" if approvals is not None else "down",
            detail="enabled" if approvals is not None else "disabled",
        )
    )

    states = [s.state for s in services]
    if any(s == "down" for s in states):
        overall = "degraded" if any(s == "ok" for s in states) else "down"
    elif any(s == "degraded" for s in states):
        overall = "degraded"
    else:
        overall = "ok"
    return StatusResponse(overall=overall, services=services)


async def _probe_http(*, name: str, url: str, timeout: float = 2.0) -> ServiceStatus:  # noqa: ASYNC109
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url)
        if r.status_code < 500:
            # 200 ok; Mimir's /ready returns 503 during warmup but reachable
            return ServiceStatus(
                name=name,
                state="ok" if r.status_code < 400 else "degraded",
                detail=f"HTTP {r.status_code}",
            )
        return ServiceStatus(name=name, state="degraded", detail=f"HTTP {r.status_code}")
    except Exception as exc:
        return ServiceStatus(name=name, state="down", detail=type(exc).__name__)
