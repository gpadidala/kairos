"""ApprovalStore + UI endpoints + pre-PR gating."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from kairos.api.app import create_app
from kairos.config.settings import AuditDBSettings, Settings
from kairos.domain.enums import (
    ApprovalStatus,
    ForecastModel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from kairos.domain.models import (
    Forecast,
    MetricPoint,
    PRResult,
    ScalingDecision,
    Workload,
)
from kairos.storage.approvals import ApprovalStore, make_approval_id
from kairos.storage.db import Database


@pytest.fixture
async def db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    d = Database.from_settings(AuditDBSettings(url=url))
    await d.create_all()
    yield d
    await d.dispose()


@pytest.fixture
async def store(db: Database) -> ApprovalStore:
    return ApprovalStore(db, pending_ttl_hours=1)


def _workload() -> Workload:
    return Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.JVM,
        current_replicas=3,
        cpu_request="500m",
        cpu_limit="2",
        mem_request="1Gi",
        mem_limit="2Gi",
        gitops_path="apps/api",
    )


def _decision(cid: str = "run-1") -> ScalingDecision:
    w = _workload()
    now = datetime(2026, 4, 24, 12, tzinfo=UTC)
    fc = Forecast(
        workload=w,
        metric="cpu",
        horizon_hours=48,
        points=[MetricPoint(ts=now + timedelta(hours=i), value=1.5) for i in range(3)],
        p95_predicted=1.8,
        peak_predicted=1.95,
        peak_at=now + timedelta(hours=2),
        confidence_score=0.85,
        model_used=ForecastModel.STATISTICAL,
        generated_at=now,
    )
    return ScalingDecision(
        workload=w,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="CPU_HEADROOM_BREACH",
        rationale="CPU trending up.",
        target_replicas=5,
        forecasts=[fc],
        severity=Severity.WARNING,
        confidence=0.85,
        correlation_id=cid,
        generated_at=now,
    )


async def test_enqueue_is_idempotent(store: ApprovalStore) -> None:
    d = _decision()
    a1 = await store.enqueue(d)
    a2 = await store.enqueue(d)
    assert a1.id == a2.id
    assert a1.status == ApprovalStatus.PENDING
    pending = await store.list_pending()
    assert len(pending) == 1


async def test_approve_then_mark_applied(store: ApprovalStore) -> None:
    approval = await store.enqueue(_decision())
    approved = await store.approve(approval.id, approved_by="alice@example.com")
    assert approved is not None
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.approved_by == "alice@example.com"

    applied = await store.mark_applied(
        approval.id, pr_url="https://github.com/x/y/pull/5", pr_number=5
    )
    assert applied is not None
    assert applied.status == ApprovalStatus.APPLIED
    assert str(applied.pr_url) == "https://github.com/x/y/pull/5"
    assert applied.pr_number == 5


async def test_reject_sets_reason(store: ApprovalStore) -> None:
    approval = await store.enqueue(_decision())
    rejected = await store.reject(
        approval.id, approved_by="bob@example.com", reason="incident in progress"
    )
    assert rejected is not None
    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.rejection_reason == "incident in progress"


async def test_approve_unknown_returns_none(store: ApprovalStore) -> None:
    result = await store.approve("missing", approved_by="anyone")
    assert result is None


async def test_expire_stale(store: ApprovalStore, db: Database) -> None:
    # Insert a row with a backdated created_at
    approval = await store.enqueue(_decision())
    from kairos.storage.db import ApprovalRow  # noqa: PLC0415

    async with db.session() as s:
        row = await s.get(ApprovalRow, approval.id)
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        await s.commit()

    expired = await store.expire_stale()
    assert expired == 1
    after = await store.get(approval.id)
    assert after is not None
    assert after.status == ApprovalStatus.EXPIRED


async def test_counts_groups_by_status(store: ApprovalStore) -> None:
    a = await store.enqueue(_decision(cid="r1"))
    b = await store.enqueue(_decision(cid="r2"))
    await store.approve(a.id, approved_by="alice")
    await store.reject(b.id, approved_by="bob", reason="no")
    counts = await store.counts()
    assert counts.get(ApprovalStatus.APPROVED.value) == 1
    assert counts.get(ApprovalStatus.REJECTED.value) == 1


def test_make_approval_id_is_stable() -> None:
    d = _decision()
    assert make_approval_id(d) == make_approval_id(d)
    assert len(make_approval_id(d)) == 32


# ── UI HTMX + HTML smoke tests ────────────────────────────────────────
@pytest.fixture
def ui_ctx(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Yield (TestClient, shared sqlite url) so tests can seed the DB themselves."""
    url = f"sqlite+aiosqlite:///{tmp_path}/ui.db"
    monkeypatch.setenv("KAIROS_AUDIT_DB__URL", url)
    monkeypatch.setenv("KAIROS_FEATURES__DRY_RUN", "false")
    monkeypatch.setenv("KAIROS_FEATURES__REQUIRE_UI_APPROVAL", "true")
    monkeypatch.setenv("KAIROS_FEATURES__ENABLE_UI", "true")
    settings = Settings()
    with TestClient(create_app(settings)) as c:
        yield c, url


@pytest.fixture
def client(ui_ctx):  # type: ignore[no-untyped-def]
    return ui_ctx[0]


def test_ui_dashboard_renders(client: TestClient) -> None:
    r = client.get("/ui/dashboard")
    assert r.status_code == 200
    assert "Overview" in r.text
    # Glass UI uses concise "Pending" stat-card label + link to /ui/pending
    assert "Pending" in r.text
    assert "/ui/pending" in r.text


def test_ui_pending_empty(client: TestClient) -> None:
    r = client.get("/ui/pending")
    assert r.status_code == 200
    assert "Pending Approvals" in r.text


def test_ui_history_renders(client: TestClient) -> None:
    r = client.get("/ui/history")
    assert r.status_code == 200
    assert "History" in r.text


def test_ui_keda_panel_handles_missing_grafana(client: TestClient) -> None:
    r = client.get("/ui/keda")
    assert r.status_code == 200
    # Empty state visible — Grafana not wired in this smoke test
    assert "KEDA Activity" in r.text


def test_ui_alerts_handles_missing_grafana(client: TestClient) -> None:
    r = client.get("/ui/alerts")
    assert r.status_code == 200
    assert "Alerts" in r.text


def test_ui_root_redirects_to_dashboard(client: TestClient) -> None:
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/dashboard"


def test_api_list_approvals_empty(client: TestClient) -> None:
    r = client.get("/api/v1/approvals?status=pending")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_api_get_missing_approval_404(client: TestClient) -> None:
    r = client.get("/api/v1/approvals/does-not-exist")
    assert r.status_code == 404


def test_htmx_approve_full_cycle(ui_ctx) -> None:  # type: ignore[no-untyped-def]
    """Insert an approval via the SQLite file, then approve via HTMX → PR stub fires."""
    import asyncio  # noqa: PLC0415

    client, db_url = ui_ctx

    async def _seed() -> str:
        d = Database.from_settings(AuditDBSettings(url=db_url))
        await d.create_all()
        store = ApprovalStore(d, pending_ttl_hours=1)
        a = await store.enqueue(_decision())
        await d.dispose()
        return a.id

    class _StubPR:
        def __init__(self) -> None:
            self._dry_run = False
            self.calls: list[ScalingDecision] = []

        async def create_pr_for_decision(
            self,
            decision: ScalingDecision,
            *,
            advice=None,  # type: ignore[no-untyped-def]
        ) -> PRResult | None:
            self.calls.append(decision)
            return PRResult(
                url=HttpUrl("https://github.com/acme/gitops/pull/99"),
                number=99,
                branch="kairos/test",
                files_changed=["apps/api/deployment.yaml"],
            )

    stub = _StubPR()
    client.app.state.pr_creator = stub  # type: ignore[attr-defined]

    approval_id = asyncio.run(_seed())
    r = client.post(f"/ui/approvals/{approval_id}/approve", data={"approved_by": "tester"})
    assert r.status_code == 200
    assert r.text == ""
    assert len(stub.calls) == 1

    async def _verify() -> None:
        d = Database.from_settings(AuditDBSettings(url=db_url))
        store = ApprovalStore(d, pending_ttl_hours=1)
        a = await store.get(approval_id)
        assert a is not None
        assert a.status == ApprovalStatus.APPLIED
        assert a.pr_number == 99
        await d.dispose()

    asyncio.run(_verify())


def test_htmx_reject_persists_reason(ui_ctx) -> None:  # type: ignore[no-untyped-def]
    import asyncio  # noqa: PLC0415

    client, db_url = ui_ctx

    async def _seed() -> str:
        d = Database.from_settings(AuditDBSettings(url=db_url))
        await d.create_all()
        store = ApprovalStore(d, pending_ttl_hours=1)
        a = await store.enqueue(_decision(cid="run-reject"))
        await d.dispose()
        return a.id

    approval_id = asyncio.run(_seed())
    r = client.post(
        f"/ui/approvals/{approval_id}/reject",
        data={"approved_by": "tester", "reason": "not now"},
    )
    assert r.status_code == 200

    async def _verify() -> None:
        d = Database.from_settings(AuditDBSettings(url=db_url))
        store = ApprovalStore(d, pending_ttl_hours=1)
        a = await store.get(approval_id)
        assert a is not None
        assert a.status == ApprovalStatus.REJECTED
        assert a.rejection_reason == "not now"
        await d.dispose()

    asyncio.run(_verify())
