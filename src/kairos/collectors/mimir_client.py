"""Async Mimir / Prometheus HTTP client with breaker + retries + metrics."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx
import structlog

from kairos.config.settings import MimirSettings
from kairos.domain.exceptions import ExternalServiceError
from kairos.domain.models import MetricPoint, MetricSeries, Workload
from kairos.observability.metrics import EXTERNAL_CALL_DURATION
from kairos.resilience.breakers import breaker_for
from kairos.resilience.retry import http_retry

log = structlog.get_logger(__name__)

SERVICE = "mimir"


class MimirClient:
    """Async Mimir query client. Bulkheaded by a semaphore."""

    def __init__(self, settings: MimirSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._sem = asyncio.Semaphore(settings.max_concurrent)
        self._breaker = breaker_for(SERVICE)

        headers: dict[str, str] = {"Accept": "application/json"}
        if settings.org_id:
            headers["X-Scope-OrgID"] = settings.org_id
        if settings.auth_bearer is not None:
            headers["Authorization"] = f"Bearer {settings.auth_bearer.get_secret_value()}"

        self._client = client or httpx.AsyncClient(
            base_url=str(settings.url).rstrip("/"),
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers=headers,
            http2=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── Low-level HTTP ────────────────────────────────────────────────
    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter()
        result = "ok"

        async def _do_request() -> dict[str, Any]:
            async for attempt in http_retry():
                with attempt:
                    r = await self._client.get(path, params=params)
                    r.raise_for_status()
                    data: dict[str, Any] = r.json()
                    if data.get("status") != "success":
                        raise ExternalServiceError(
                            SERVICE, f"query failed: {data.get('error', 'unknown')}"
                        )
                    return data
            raise RuntimeError("unreachable")  # pragma: no cover

        try:
            async with self._sem:
                return await self._breaker.call(_do_request)
        except httpx.HTTPError as exc:
            result = "http_error"
            raise ExternalServiceError(
                SERVICE,
                f"{type(exc).__name__}: {exc}",
                status=getattr(getattr(exc, "response", None), "status_code", None),
            ) from exc
        except ExternalServiceError:
            result = "error"
            raise
        finally:
            EXTERNAL_CALL_DURATION.labels(service=SERVICE, result=result).observe(
                time.perf_counter() - start
            )

    # ── Public API ────────────────────────────────────────────────────
    async def query_instant(self, query: str, *, at: datetime | None = None) -> float | None:
        """Execute an instant query, return scalar value (or None if no data)."""
        params: dict[str, Any] = {"query": query}
        if at is not None:
            params["time"] = at.timestamp()
        data = await self._request("/prometheus/api/v1/query", params)
        result = data["data"]["result"]
        if not result:
            return None
        value = result[0]["value"][1]
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def query_range(
        self,
        workload: Workload,
        metric: str,
        query: str,
        *,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> MetricSeries:
        """Execute a range query and return it as a MetricSeries."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end <= start:
            raise ValueError("end must be strictly after start")

        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step_seconds,
        }
        data = await self._request("/prometheus/api/v1/query_range", params)
        result = data["data"]["result"]
        points: list[MetricPoint] = []
        if result:
            for ts_str, val_str in result[0].get("values", []):
                try:
                    ts = datetime.fromtimestamp(float(ts_str), tz=UTC)
                    val = float(val_str)
                except (TypeError, ValueError):
                    continue
                points.append(MetricPoint(ts=ts, value=val))

        return MetricSeries(
            workload=workload,
            metric=metric,
            points=points,
            resolution_seconds=step_seconds,
        )

    async def ping(self) -> bool:
        """Readiness probe target."""
        try:
            await self._request("/prometheus/api/v1/query", {"query": "up"})
        except (ExternalServiceError, httpx.HTTPError) as exc:
            log.warning("mimir_ping_failed", error=str(exc))
            return False
        return True
