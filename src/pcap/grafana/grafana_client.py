"""Async Grafana HTTP client. Folders + dashboards + unified alerts."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from pcap.config.settings import GrafanaSettings
from pcap.domain.exceptions import ExternalServiceError
from pcap.observability.metrics import EXTERNAL_CALL_DURATION
from pcap.resilience.breakers import breaker_for

log = structlog.get_logger(__name__)

SERVICE = "grafana"


class GrafanaClient:
    """Minimal async Grafana client."""

    def __init__(self, settings: GrafanaSettings, client: httpx.AsyncClient | None = None) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if settings.api_token is not None:
            headers["Authorization"] = f"Bearer {settings.api_token.get_secret_value()}"

        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=str(settings.url).rstrip("/"),
            timeout=httpx.Timeout(15.0),
            headers=headers,
        )
        self._breaker = breaker_for(SERVICE)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        result = "ok"

        async def _call() -> dict[str, Any]:
            r = await self._client.request(method, path, json=json, params=params)
            if r.status_code >= 400:
                raise ExternalServiceError(
                    SERVICE,
                    f"{method} {path} -> {r.status_code}: {r.text[:400]}",
                    status=r.status_code,
                )
            if r.status_code == 204 or not r.content:
                return {}
            payload: Any = r.json()
            if isinstance(payload, dict):
                return payload
            return {"data": payload}

        try:
            return await self._breaker.call(_call)
        except ExternalServiceError:
            result = "error"
            raise
        except httpx.HTTPError as exc:
            result = "http_error"
            raise ExternalServiceError(SERVICE, f"{type(exc).__name__}: {exc}") from exc
        finally:
            EXTERNAL_CALL_DURATION.labels(service=SERVICE, result=result).observe(
                time.perf_counter() - start
            )

    # ── Folders ───────────────────────────────────────────────────────
    async def ensure_folder(self, title: str) -> str:
        """Create or return the folder UID for `title`."""
        folders = await self._request("GET", "/api/folders")
        data = folders.get("data", folders)
        if isinstance(data, list):
            for f in data:
                if isinstance(f, dict) and f.get("title") == title:
                    return str(f.get("uid", ""))
        created = await self._request("POST", "/api/folders", json={"title": title})
        return str(created.get("uid", ""))

    # ── Dashboards ────────────────────────────────────────────────────
    async def upsert_dashboard(
        self,
        dashboard: dict[str, Any],
        *,
        folder_uid: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dashboard": dashboard,
            "folderUid": folder_uid,
            "overwrite": overwrite,
        }
        return await self._request("POST", "/api/dashboards/db", json=payload)

    # ── Unified alerting ──────────────────────────────────────────────
    async def upsert_alert_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        """Create or update a provisioned alert rule (uid required in the payload)."""
        uid = str(rule.get("uid", ""))
        if not uid:
            raise ValueError("alert rule must have 'uid'")
        try:
            return await self._request("PUT", f"/api/v1/provisioning/alert-rules/{uid}", json=rule)
        except ExternalServiceError as exc:
            if exc.status == 404:
                return await self._request("POST", "/api/v1/provisioning/alert-rules", json=rule)
            raise

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/health")
        except ExternalServiceError as exc:
            log.warning("grafana_ping_failed", error=str(exc))
            return False
        return True
