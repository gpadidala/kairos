"""FastAPI router serving the HTMX UI + JSON API for approvals."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from kairos.collectors.promql_library import PromQLLibrary, QueryName
from kairos.discovery.workload_discovery import WorkloadDiscovery
from kairos.domain.enums import ApprovalStatus
from kairos.domain.models import GrafanaAlert, PendingApproval, Workload
from kairos.grafana.grafana_client import GrafanaClient
from kairos.storage.approvals import ApprovalStore
from kairos.storage.sql_audit_store import SQLAuditStore

log = structlog.get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ── DI ────────────────────────────────────────────────────────────────
def _deps(request: Request) -> dict[str, Any]:
    """Grab UI dependencies stashed on app.state."""
    state = request.app.state
    return {
        "settings": state.settings,
        "approvals": getattr(state, "approvals", None),
        "audit": getattr(state, "sql_audit", None),
        "grafana": getattr(state, "grafana_client", None),
        "pr_creator": getattr(state, "pr_creator", None),
    }


DepsDep = Depends(_deps)


async def _pending_count(deps: dict[str, Any]) -> int:
    """Count surfaced in the sidebar badge."""
    approvals: ApprovalStore | None = deps["approvals"]
    if approvals is None:
        return 0
    counts = await approvals.counts()
    return int(counts.get(ApprovalStatus.PENDING.value, 0))


async def _ctx(deps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Build the standard template context — every screen gets pending_count + settings."""
    return {
        "settings": deps["settings"],
        "pending_count": await _pending_count(deps),
        **extra,
    }


async def _probe_admin_service(  # noqa: PLR0911, PLR0912 — flat per-service dispatch is clearer than refactor
    service: str, settings: Any, app_state: Any
) -> tuple[str, str]:
    """Probe one external dependency for the /ui/admin page.

    Returns (state, detail) where state ∈ {ok, degraded, down, unconfigured}.
    """
    import httpx  # noqa: PLC0415

    timeout = 3.0
    if service == "grafana":
        url = f"{str(settings.grafana.url).rstrip('/')}/api/health"
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url)
            return ("ok", f"HTTP {r.status_code}") if r.status_code < 400 else (
                "degraded",
                f"HTTP {r.status_code}",
            )
        except Exception as exc:
            return ("down", f"{type(exc).__name__}: {exc}")
    if service == "mimir":
        url = f"{str(settings.mimir.url).rstrip('/')}/ready"
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url)
            return ("ok", f"HTTP {r.status_code}") if r.status_code < 500 else (
                "down",
                f"HTTP {r.status_code}",
            )
        except Exception as exc:
            return ("down", f"{type(exc).__name__}: {exc}")
    if service == "github":
        if not settings.github.repo:
            return ("unconfigured", "set KAIROS_GITHUB__REPO")
        url = f"https://api.github.com/repos/{settings.github.repo}"
        headers: dict[str, str] = {}
        if settings.github.token is not None:
            headers["Authorization"] = f"Bearer {settings.github.token.get_secret_value()}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, headers=headers)
            if r.status_code == 200:
                return "ok", "200 · repo reachable"
            if r.status_code == 401:
                return "down", "401 · invalid token"
            if r.status_code == 404:
                return "down", "404 · repo not found"
            return "degraded", f"HTTP {r.status_code}"
        except Exception as exc:
            return ("down", f"{type(exc).__name__}: {exc}")
    if service == "redis":
        try:
            from kairos.storage.redis_client import RedisClient  # noqa: PLC0415

            client = RedisClient.from_settings(settings.redis)
            ok = await client.ping()
            await client.close()
            return ("ok", "PONG") if ok else ("down", "ping failed")
        except Exception as exc:
            return "down", f"{type(exc).__name__}: {exc}"
    if service == "db":
        db = getattr(app_state, "db", None)
        if db is None:
            return "down", "audit DB not bootstrapped"
        try:
            from sqlalchemy import text  # noqa: PLC0415

            async with db.session() as s:
                await s.execute(text("SELECT 1"))
            return "ok", "SELECT 1"
        except Exception as exc:
            return "down", f"{type(exc).__name__}: {exc}"
    return "unconfigured", f"unknown service: {service}"


# ── Small response models for the JSON endpoints ──────────────────────
class ApprovalAction(BaseModel):
    approved_by: str
    reason: str | None = None


class ApprovalListResponse(BaseModel):
    total: int
    items: list[PendingApproval]


# ── The router ────────────────────────────────────────────────────────
def build_ui_router() -> APIRouter:  # noqa: PLR0915 — single factory registering all UI + API routes
    router = APIRouter()

    # ── HTML screens ──────────────────────────────────────────────────
    @router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    async def ui_root() -> RedirectResponse:
        return RedirectResponse("/ui/home", status_code=status.HTTP_302_FOUND)

    @router.get("/ui/home", response_class=HTMLResponse, include_in_schema=False)
    async def ui_home(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        s = deps["settings"]
        gh_repo_url = f"https://github.com/{s.github.repo}" if s.github.repo else None
        return templates.TemplateResponse(
            request,
            "home.html.j2",
            await _ctx(
                deps,
                grafana_url=s.grafana.public_url,
                github_url=gh_repo_url,
            ),
        )

    @router.get("/ui/admin", response_class=HTMLResponse, include_in_schema=False)
    async def ui_admin(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        s = deps["settings"]
        gh_repo_url = f"https://github.com/{s.github.repo}" if s.github.repo else None
        external = str(s.api.external_url).rstrip("/") if s.api.external_url else ""
        alert_webhook_url = f"{external}/api/v1/alerts/webhook" if external else "(set KAIROS_API__EXTERNAL_URL to enable)"
        github_webhook_url = f"{external}/api/v1/github/webhook" if external else "(set KAIROS_API__EXTERNAL_URL to enable)"
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        profiles: list[Any] = []
        active_profile: Any = getattr(request.app.state, "active_env_profile", None)
        if env_profiles_store is not None:
            profiles = await env_profiles_store.list()
        return templates.TemplateResponse(
            request,
            "admin.html.j2",
            await _ctx(
                deps,
                grafana_url=s.grafana.public_url,
                mimir_url=str(s.mimir.url).rstrip("/"),
                github_url=gh_repo_url,
                github_repo=s.github.repo,
                redis_url=s.redis.url,
                audit_db_url=s.audit_db.url,
                alert_webhook_url=alert_webhook_url,
                github_webhook_url=github_webhook_url,
                teams_enabled=s.teams.webhook_url is not None,
                slack_enabled=s.slack.webhook_url is not None or s.slack.bot_token is not None,
                smtp_enabled=bool(s.smtp.host and s.smtp.to_addrs),
                profiles=profiles,
                active_profile=active_profile,
            ),
        )

    @router.get("/ui/admin/envs/new", response_class=HTMLResponse, include_in_schema=False)
    async def ui_admin_env_new(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "admin_env_form.html.j2",
            await _ctx(deps, profile=None, mode="new"),
        )

    @router.get(
        "/ui/admin/envs/{profile_id}", response_class=HTMLResponse, include_in_schema=False
    )
    async def ui_admin_env_edit(
        profile_id: str, request: Request, deps: dict[str, Any] = DepsDep
    ) -> HTMLResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        profile = await env_profiles_store.get(profile_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        return templates.TemplateResponse(
            request,
            "admin_env_form.html.j2",
            await _ctx(deps, profile=profile, mode="edit"),
        )

    def _opt_float(raw: str) -> float | None:
        s = raw.strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
        return v if v > 0 else None

    @router.post("/ui/admin/envs", include_in_schema=False)
    async def ui_admin_env_create(
        request: Request,
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        grafana_url: Annotated[str, Form()] = "",
        grafana_external_url: Annotated[str, Form()] = "",
        grafana_token: Annotated[str, Form()] = "",
        mimir_url: Annotated[str, Form()] = "",
        mimir_org_id: Annotated[str, Form()] = "",
        mimir_bearer: Annotated[str, Form()] = "",
        github_repo: Annotated[str, Form()] = "",
        github_token: Annotated[str, Form()] = "",
        github_base_branch: Annotated[str, Form()] = "",
        api_external_url: Annotated[str, Form()] = "",
        cost_cpu_per_hour: Annotated[str, Form()] = "",
        cost_mem_gib_per_hour: Annotated[str, Form()] = "",
        cost_currency: Annotated[str, Form()] = "",
        activate: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        profile = await env_profiles_store.create(
            name=name.strip(),
            description=description.strip() or None,
            grafana_url=grafana_url.strip() or None,
            grafana_external_url=grafana_external_url.strip() or None,
            grafana_token=grafana_token.strip() or None,
            mimir_url=mimir_url.strip() or None,
            mimir_org_id=mimir_org_id.strip() or None,
            mimir_bearer=mimir_bearer.strip() or None,
            github_repo=github_repo.strip() or None,
            github_token=github_token.strip() or None,
            github_base_branch=github_base_branch.strip() or None,
            api_external_url=api_external_url.strip() or None,
            cost_cpu_per_hour=_opt_float(cost_cpu_per_hour),
            cost_mem_gib_per_hour=_opt_float(cost_mem_gib_per_hour),
            cost_currency=cost_currency.strip().upper() or None,
        )
        if activate == "on":
            await env_profiles_store.activate(profile.id)
            from kairos.api.app import reload_active_environment  # noqa: PLC0415

            await reload_active_environment(request.app)
        return RedirectResponse("/ui/admin", status_code=status.HTTP_302_FOUND)

    @router.post("/ui/admin/envs/{profile_id}", include_in_schema=False)
    async def ui_admin_env_update(
        profile_id: str,
        request: Request,
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        grafana_url: Annotated[str, Form()] = "",
        grafana_external_url: Annotated[str, Form()] = "",
        grafana_token: Annotated[str, Form()] = "",
        mimir_url: Annotated[str, Form()] = "",
        mimir_org_id: Annotated[str, Form()] = "",
        mimir_bearer: Annotated[str, Form()] = "",
        github_repo: Annotated[str, Form()] = "",
        github_token: Annotated[str, Form()] = "",
        github_base_branch: Annotated[str, Form()] = "",
        api_external_url: Annotated[str, Form()] = "",
        cost_cpu_per_hour: Annotated[str, Form()] = "",
        cost_mem_gib_per_hour: Annotated[str, Form()] = "",
        cost_currency: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        result = await env_profiles_store.update(
            profile_id,
            name=name.strip(),
            description=description.strip() or None,
            grafana_url=grafana_url.strip() or None,
            grafana_external_url=grafana_external_url.strip() or None,
            grafana_token=grafana_token.strip() or None,
            mimir_url=mimir_url.strip() or None,
            mimir_org_id=mimir_org_id.strip() or None,
            mimir_bearer=mimir_bearer.strip() or None,
            github_repo=github_repo.strip() or None,
            github_token=github_token.strip() or None,
            github_base_branch=github_base_branch.strip() or None,
            api_external_url=api_external_url.strip() or None,
            cost_cpu_per_hour=_opt_float(cost_cpu_per_hour),
            cost_mem_gib_per_hour=_opt_float(cost_mem_gib_per_hour),
            cost_currency=cost_currency.strip().upper() or None,
        )
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        if result.is_active:
            from kairos.api.app import reload_active_environment  # noqa: PLC0415

            await reload_active_environment(request.app)
        return RedirectResponse("/ui/admin", status_code=status.HTTP_302_FOUND)

    @router.post("/ui/admin/envs/{profile_id}/activate", include_in_schema=False)
    async def ui_admin_env_activate(profile_id: str, request: Request) -> RedirectResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        result = await env_profiles_store.activate(profile_id)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        from kairos.api.app import reload_active_environment  # noqa: PLC0415

        await reload_active_environment(request.app)
        return RedirectResponse("/ui/admin", status_code=status.HTTP_302_FOUND)

    @router.post("/ui/admin/envs/{profile_id}/deactivate", include_in_schema=False)
    async def ui_admin_env_deactivate(profile_id: str, request: Request) -> RedirectResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        await env_profiles_store.deactivate_all()
        from kairos.api.app import reload_active_environment  # noqa: PLC0415

        await reload_active_environment(request.app)
        return RedirectResponse("/ui/admin", status_code=status.HTTP_302_FOUND)

    @router.post("/ui/admin/envs/{profile_id}/delete", include_in_schema=False)
    async def ui_admin_env_delete(profile_id: str, request: Request) -> RedirectResponse:
        env_profiles_store = getattr(request.app.state, "env_profiles", None)
        if env_profiles_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "env profiles disabled")
        # If we deleted the active one, fall back to env-var settings
        active = await env_profiles_store.get_active()
        await env_profiles_store.delete(profile_id)
        if active and active.id == profile_id:
            from kairos.api.app import reload_active_environment  # noqa: PLC0415

            await reload_active_environment(request.app)
        return RedirectResponse("/ui/admin", status_code=status.HTTP_302_FOUND)

    @router.get("/ui/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def ui_dashboard(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        audit: SQLAuditStore | None = deps["audit"]
        approvals: ApprovalStore | None = deps["approvals"]
        counters: dict[str, int] = {
            "decisions_today": 0,
            "prs_today": 0,
            "pending_approvals": 0,
        }
        status_counts: dict[str, int] = {}
        recent_runs: list[Any] = []
        activity: dict[str, list[dict[str, object]]] = {
            "decisions_per_day": [],
            "prs_per_day": [],
            "actions_breakdown": [],
            "approvals_breakdown": [],
            "alerts_breakdown": [],
        }
        if audit is not None:
            counters = await audit.counters_24h()
            recent_runs = await audit.recent_runs(limit=10)
            activity = await audit.activity_summary(days=7)
        if approvals is not None:
            status_counts = await approvals.counts()
        settings_obj = deps["settings"]
        gh_repo_url = (
            f"https://github.com/{settings_obj.github.repo}" if settings_obj.github.repo else None
        )
        return templates.TemplateResponse(
            request,
            "dashboard.html.j2",
            await _ctx(
                deps,
                counters=counters,
                status_counts=status_counts,
                recent_runs=recent_runs,
                activity=activity,
                grafana_url=settings_obj.grafana.public_url,
                github_url=gh_repo_url,
            ),
        )

    @router.get("/ui/pending", response_class=HTMLResponse, include_in_schema=False)
    async def ui_pending(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        approvals: ApprovalStore | None = deps["approvals"]
        items: list[PendingApproval] = []
        if approvals is not None:
            items = await approvals.list_pending()
        return templates.TemplateResponse(
            request,
            "pending.html.j2",
            await _ctx(deps, items=items),
        )

    @router.get("/ui/history", response_class=HTMLResponse, include_in_schema=False)
    async def ui_history(
        request: Request,
        deps: dict[str, Any] = DepsDep,
    ) -> HTMLResponse:
        approvals: ApprovalStore | None = deps["approvals"]
        audit: SQLAuditStore | None = deps["audit"]
        recent_approvals: list[PendingApproval] = []
        recent_decisions: list[Any] = []
        recent_prs: list[Any] = []
        if approvals is not None:
            recent_approvals = await approvals.list_recent(
                limit=50,
                statuses=(
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.REJECTED,
                    ApprovalStatus.APPLIED,
                    ApprovalStatus.FAILED,
                    ApprovalStatus.EXPIRED,
                ),
            )
        if audit is not None:
            recent_decisions = await audit.recent_decisions(limit=50)
            recent_prs = await audit.recent_prs(limit=50)
        return templates.TemplateResponse(
            request,
            "history.html.j2",
            await _ctx(
                deps,
                approvals=recent_approvals,
                decisions=recent_decisions,
                prs=recent_prs,
            ),
        )

    @router.get("/ui/keda", response_class=HTMLResponse, include_in_schema=False)
    async def ui_keda(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        grafana: GrafanaClient | None = deps["grafana"]
        replicas_added: list[dict[str, Any]] = []
        scale_events: list[dict[str, Any]] = []
        node_pool_size: list[dict[str, Any]] = []
        node_pool_delta: list[dict[str, Any]] = []
        if grafana is not None:
            replicas_added = await grafana.query_prometheus_instant(
                PromQLLibrary.render(QueryName.KEDA_REPLICAS_ADDED_24H)
            )
            scale_events = await grafana.query_prometheus_instant(
                PromQLLibrary.render(QueryName.KEDA_SCALE_EVENTS_24H)
            )
            node_pool_size = await grafana.query_prometheus_instant(
                PromQLLibrary.render(QueryName.NODE_POOL_SIZE)
            )
            node_pool_delta = await grafana.query_prometheus_instant(
                PromQLLibrary.render(QueryName.NODE_POOL_DELTA_24H)
            )
        return templates.TemplateResponse(
            request,
            "keda.html.j2",
            await _ctx(
                deps,
                replicas_added=replicas_added,
                scale_events=scale_events,
                node_pool_size=node_pool_size,
                node_pool_delta=node_pool_delta,
                grafana_url=deps["settings"].grafana.public_url,
            ),
        )

    @router.get("/ui/alerts", response_class=HTMLResponse, include_in_schema=False)
    async def ui_alerts(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        # Two sources merged: live Grafana alerts AND alerts received via webhook
        grafana: GrafanaClient | None = deps["grafana"]
        live_alerts: list[GrafanaAlert] = []
        if grafana is not None:
            raw = await grafana.list_active_alerts()
            live_alerts = [_coerce_alert(a) for a in raw]
        # Webhook-received alerts (from Grafana contact point)
        from kairos.domain.enums import AlertState  # noqa: PLC0415

        alert_store = getattr(request.app.state, "incoming_alerts", None)
        firing: list[Any] = []
        recent: list[Any] = []
        if alert_store is not None:
            firing = await alert_store.list_recent(limit=50, states=(AlertState.FIRING,))
            recent = await alert_store.list_recent(
                limit=50,
                states=(AlertState.RESOLVED, AlertState.ACKNOWLEDGED),
            )
        # Compute the Grafana-side webhook URL the operator should configure
        api_url = str(deps["settings"].api.external_url or "").rstrip("/")
        if not api_url:
            api_url = f"http://localhost:{deps['settings'].api.port}"
        webhook_url = f"{api_url}/api/v1/alerts/webhook"
        return templates.TemplateResponse(
            request,
            "alerts.html.j2",
            await _ctx(
                deps,
                live_alerts=live_alerts,
                firing=firing,
                recent=recent,
                grafana_url=deps["settings"].grafana.public_url,
                webhook_url=webhook_url,
            ),
        )

    # ── Alert webhook receiver (Grafana contact point posts here) ────
    @router.post("/api/v1/alerts/webhook", include_in_schema=True, status_code=202)
    async def receive_alert_webhook(
        request: Request,
        deps: dict[str, Any] = DepsDep,
    ) -> dict[str, Any]:
        """Accept Grafana's webhook payload, normalize, persist."""
        from kairos.storage.alerts import parse_grafana_webhook  # noqa: PLC0415

        store = getattr(request.app.state, "incoming_alerts", None)
        if store is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "alert store not configured",
            )
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid JSON: {exc}") from exc
        normalized = parse_grafana_webhook(payload if isinstance(payload, dict) else {})
        n = await store.upsert_many(normalized)
        log.info(
            "alert_webhook_received",
            count=len(normalized),
            stored=n,
            receiver=payload.get("receiver") if isinstance(payload, dict) else None,
        )
        return {"ok": True, "received": len(normalized), "stored": n}

    # ── Acknowledge an alert from the UI ─────────────────────────────
    @router.post("/ui/alerts/{alert_id}/ack", include_in_schema=False)
    async def htmx_ack_alert(
        alert_id: str,
        request: Request,
        deps: dict[str, Any] = DepsDep,
        acknowledged_by: str = Form(default="ui-user"),
    ) -> HTMLResponse:
        store = getattr(request.app.state, "incoming_alerts", None)
        if store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "alert store not configured")
        result = await store.acknowledge(alert_id, by=acknowledged_by)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
        return HTMLResponse("")  # HTMX swap removes the row

    # ── GitHub webhook (pull_request closed → mark approval merged) ──
    @router.post("/api/v1/github/webhook", include_in_schema=True, status_code=202)
    async def github_webhook(request: Request, deps: dict[str, Any] = DepsDep) -> dict[str, Any]:
        """Receive GitHub PR webhooks. We only act on `pull_request.closed`+merged."""
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            return {"ok": True, "ignored": "not_dict"}
        action = payload.get("action")
        pr = payload.get("pull_request") or {}
        if action != "closed" or not pr.get("merged"):
            return {"ok": True, "ignored": f"action={action} merged={pr.get('merged')}"}

        pr_number = int(pr.get("number") or 0)
        # Find the matching approval by pr_number, mark as merged
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            return {"ok": True, "ignored": "approvals_disabled"}
        from sqlalchemy import select as _select  # noqa: PLC0415

        from kairos.storage.db import ApprovalRow  # noqa: PLC0415

        db = getattr(request.app.state, "db", None)
        if db is None:
            return {"ok": True, "ignored": "db_disabled"}
        async with db.session() as s:
            stmt = _select(ApprovalRow).where(ApprovalRow.pr_number == pr_number)
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                log.info("github_webhook_no_match", pr_number=pr_number)
                return {"ok": True, "matched": False}
            row.status = "merged"
            row.updated_at = datetime.now(UTC)
            await s.commit()
            log.info("approval_merged_via_webhook", pr_number=pr_number, approval_id=row.id)
        return {"ok": True, "matched": True, "pr_number": pr_number}

    # ── Admin: connection tests (HTMX-friendly HTML fragments) ───────
    @router.post("/api/v1/admin/test/{service}", response_class=HTMLResponse, include_in_schema=False)
    async def admin_test_connection(
        service: str, request: Request, deps: dict[str, Any] = DepsDep
    ) -> HTMLResponse:
        s = deps["settings"]
        state, detail = await _probe_admin_service(service, s, request.app.state)
        cls = {
            "ok": "pill pill-success",
            "down": "pill pill-danger",
            "degraded": "pill pill-warn",
            "unconfigured": "pill",
        }.get(state, "pill")
        # Truncate long error messages
        detail_short = (detail[:60] + "…") if len(detail) > 60 else detail
        html = (
            f'<span class="{cls}" id="conn-{service}-state" title="{detail}">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-current opacity-70"></span>'
            f"{state} · {detail_short}"
            f"</span>"
        )
        return HTMLResponse(html)

    # ── Workloads list + detail ──────────────────────────────────────
    @router.get("/ui/workloads", response_class=HTMLResponse, include_in_schema=False)
    async def ui_workloads(request: Request, deps: dict[str, Any] = DepsDep) -> HTMLResponse:
        workloads: list[Workload] = []
        try:
            disc = WorkloadDiscovery.from_settings(deps["settings"].k8s)
            workloads = await disc.list()
        except Exception as exc:
            log.warning("workloads_discovery_failed", error=str(exc))

        # Build a per-workload summary: latest decision (if any), recent PRs.
        summaries: list[dict[str, Any]] = []
        audit: SQLAuditStore | None = deps["audit"]
        recent: list[Any] = await audit.recent_decisions(limit=200) if audit else []
        latest_by_uid: dict[str, Any] = {}
        for d in recent:
            latest_by_uid.setdefault(d.workload_uid, d)
        for w in workloads:
            d = latest_by_uid.get(w.uid)
            summaries.append({"workload": w, "latest": d})
        return templates.TemplateResponse(
            request,
            "workloads.html.j2",
            await _ctx(deps, summaries=summaries),
        )

    @router.get(
        "/ui/workloads/{namespace}/{name}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_workload_detail(
        namespace: str,
        name: str,
        request: Request,
        deps: dict[str, Any] = DepsDep,
    ) -> HTMLResponse:
        workload: Workload | None = None
        try:
            disc = WorkloadDiscovery.from_settings(deps["settings"].k8s)
            for w in await disc.list():
                if w.namespace == namespace and w.name == name:
                    workload = w
                    break
        except Exception as exc:
            log.warning("workload_discovery_failed", error=str(exc))

        if workload is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"workload {namespace}/{name} not found")

        # Pull last 6h of CPU + memory points via Grafana proxy for sparkline.
        grafana: GrafanaClient | None = deps["grafana"]
        cpu_points: list[float] = []
        mem_points: list[float] = []
        keda_health: list[dict[str, Any]] = []
        keda_errors: list[dict[str, Any]] = []
        if grafana is not None:
            cpu_q = PromQLLibrary.render(
                QueryName.CPU_USAGE_CORES, namespace=namespace, workload=name
            )
            mem_q = PromQLLibrary.render(
                QueryName.MEMORY_WORKING_SET, namespace=namespace, workload=name
            )
            cpu_inst = await grafana.query_prometheus_instant(cpu_q)
            mem_inst = await grafana.query_prometheus_instant(mem_q)
            # We only get instant values without a range API on the client; so
            # synthesize a single-point sparkline for v1.
            import contextlib  # noqa: PLC0415

            for r in cpu_inst:
                with contextlib.suppress(KeyError, IndexError, ValueError, TypeError):
                    cpu_points.append(float(r["value"][1]))
            for r in mem_inst:
                with contextlib.suppress(KeyError, IndexError, ValueError, TypeError):
                    mem_points.append(float(r["value"][1]))
            if workload.keda_scaledobject:
                keda_errors = await grafana.query_prometheus_instant(
                    PromQLLibrary.render(
                        QueryName.KEDA_SCALER_ERRORS_TOTAL,
                        namespace=namespace,
                        scaledobject=workload.keda_scaledobject,
                    )
                )
                keda_health = await grafana.query_prometheus_instant(
                    PromQLLibrary.render(
                        QueryName.KEDA_SCALER_ACTIVE,
                        namespace=namespace,
                        scaledobject=workload.keda_scaledobject,
                    )
                )

        # Recent decisions for this workload
        decisions_for_workload: list[Any] = []
        audit: SQLAuditStore | None = deps["audit"]
        if audit is not None:
            for d in await audit.recent_decisions(limit=200):
                if d.workload_uid == workload.uid:
                    decisions_for_workload.append(d)

        return templates.TemplateResponse(
            request,
            "workload_detail.html.j2",
            await _ctx(
                deps,
                workload=workload,
                cpu_points=cpu_points,
                mem_points=mem_points,
                keda_health=keda_health,
                keda_errors=keda_errors,
                decisions=decisions_for_workload[:25],
                grafana_url=deps["settings"].grafana.public_url,
            ),
        )

    # ── HTMX action endpoints ─────────────────────────────────────────
    @router.post("/ui/approvals/{approval_id}/approve", include_in_schema=False)
    async def htmx_approve(
        approval_id: str,
        request: Request,
        approved_by: str = Form(default="ui-user"),
        deps: dict[str, Any] = DepsDep,
    ) -> HTMLResponse:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "approvals not configured")
        updated = await approvals.approve(approval_id, approved_by=approved_by)
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
        await _apply_approved(approvals, deps["pr_creator"], updated)
        # HTMX swap: row disappears
        return HTMLResponse("")

    @router.post("/ui/approvals/{approval_id}/reject", include_in_schema=False)
    async def htmx_reject(
        approval_id: str,
        request: Request,
        approved_by: str = Form(default="ui-user"),
        reason: str = Form(default="rejected by operator"),
        deps: dict[str, Any] = DepsDep,
    ) -> HTMLResponse:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "approvals not configured")
        result = await approvals.reject(approval_id, approved_by=approved_by, reason=reason)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
        return HTMLResponse("")

    # ── JSON API ──────────────────────────────────────────────────────
    @router.get("/api/v1/approvals", response_model=ApprovalListResponse)
    async def api_list_approvals(
        deps: Annotated[dict[str, Any], Depends(_deps)],
        status_: Annotated[
            ApprovalStatus | None,
            Query(alias="status"),
        ] = ApprovalStatus.PENDING,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> ApprovalListResponse:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            return ApprovalListResponse(total=0, items=[])
        if status_ == ApprovalStatus.PENDING:
            items = await approvals.list_pending(limit=limit)
        else:
            items = await approvals.list_recent(
                limit=limit, statuses=(status_,) if status_ else None
            )
        return ApprovalListResponse(total=len(items), items=items)

    @router.get("/api/v1/approvals/{approval_id}", response_model=PendingApproval)
    async def api_get_approval(approval_id: str, deps: dict[str, Any] = DepsDep) -> PendingApproval:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "approvals not configured")
        out = await approvals.get(approval_id)
        if out is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
        return out

    @router.post("/api/v1/approvals/{approval_id}/approve", response_model=PendingApproval)
    async def api_approve(
        approval_id: str,
        body: ApprovalAction,
        deps: dict[str, Any] = DepsDep,
    ) -> PendingApproval:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "approvals not configured")
        updated = await approvals.approve(approval_id, approved_by=body.approved_by)
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
        await _apply_approved(approvals, deps["pr_creator"], updated)
        final = await approvals.get(approval_id)
        return final or updated

    @router.post("/api/v1/approvals/{approval_id}/reject", response_model=PendingApproval)
    async def api_reject(
        approval_id: str,
        body: ApprovalAction,
        deps: dict[str, Any] = DepsDep,
    ) -> PendingApproval:
        approvals: ApprovalStore | None = deps["approvals"]
        if approvals is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "approvals not configured")
        updated = await approvals.reject(
            approval_id,
            approved_by=body.approved_by,
            reason=body.reason or "rejected by operator",
        )
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
        return updated

    return router


# ── Helpers ───────────────────────────────────────────────────────────
async def _apply_approved(
    approvals: ApprovalStore,
    pr_creator: Any,
    approval: PendingApproval,
) -> None:
    """After UI approval, trigger the PR (or mark failed)."""
    if pr_creator is None:
        log.warning("pr_creator_not_configured", approval_id=approval.id)
        await approvals.mark_failed(approval.id, error="PR creator not configured")
        return
    try:
        result = await pr_creator.create_pr_for_decision(approval.decision, advice=approval.advice)
    except Exception as exc:
        log.exception("approval_pr_failed", approval_id=approval.id, error=str(exc))
        await approvals.mark_failed(approval.id, error=f"{type(exc).__name__}: {exc}")
        return
    if result is None:
        await approvals.mark_failed(approval.id, error="PR creation returned None")
        return
    await approvals.mark_applied(approval.id, pr_url=str(result.url), pr_number=result.number)


def _coerce_alert(raw: dict[str, Any]) -> GrafanaAlert:
    labels = raw.get("labels", {}) or {}
    annotations = raw.get("annotations", {}) or {}
    return GrafanaAlert(
        uid=str(
            raw.get("fingerprint")
            or raw.get("uid")
            or raw.get("labels", {}).get("alertname")
            or "unknown"
        ),
        title=str(labels.get("alertname") or annotations.get("summary") or "alert"),
        state=str(raw.get("state", "unknown")).lower(),
        severity=str(labels.get("severity", "info")),
        labels={k: str(v) for k, v in labels.items()},
        summary=annotations.get("summary"),
        starts_at=raw.get("activeAt"),
    )
