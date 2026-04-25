"""FastAPI router serving the HTMX UI + JSON API for approvals."""

from __future__ import annotations

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
        return RedirectResponse("/ui/dashboard", status_code=status.HTTP_302_FOUND)

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
        if audit is not None:
            counters = await audit.counters_24h()
            recent_runs = await audit.recent_runs(limit=10)
        if approvals is not None:
            status_counts = await approvals.counts()
        return templates.TemplateResponse(
            request,
            "dashboard.html.j2",
            await _ctx(
                deps,
                counters=counters,
                status_counts=status_counts,
                recent_runs=recent_runs,
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
        grafana: GrafanaClient | None = deps["grafana"]
        alerts: list[GrafanaAlert] = []
        if grafana is not None:
            raw = await grafana.list_active_alerts()
            alerts = [_coerce_alert(a) for a in raw]
        return templates.TemplateResponse(
            request,
            "alerts.html.j2",
            await _ctx(deps, alerts=alerts, grafana_url=deps["settings"].grafana.public_url),
        )

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
