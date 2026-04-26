"""CloudEvents 1.0 envelope + outbound webhook notifier."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from pydantic import HttpUrl, SecretStr

from kairos.config.settings import CloudEventsSettings
from kairos.domain.enums import (
    NotificationChannel,
    Runtime,
    ScalingAction,
    Severity,
    WorkloadKind,
)
from kairos.domain.models import (
    LLMAdvice,
    ScalingDecision,
    Workload,
)
from kairos.notify.base import NotificationPayload
from kairos.notify.cloudevents_notifier import CloudEventsNotifier
from kairos.observability.cloud_events import (
    KAIROS_TYPE_ALERT_FIRING,
    KAIROS_TYPE_ALERT_RESOLVED,
    KAIROS_TYPE_APPROVAL_APPROVED,
    KAIROS_TYPE_DECISION,
    CloudEvent,
    make_alert_event,
    make_approval_event,
    make_decision_event,
    make_pr_event,
)


# ── Envelope ───────────────────────────────────────────────────────
def test_envelope_includes_required_fields() -> None:
    ev = CloudEvent(source="https://kairos.local/", type=KAIROS_TYPE_DECISION, data={"k": "v"})
    body = ev.to_structured_json()
    for required in ("id", "source", "specversion", "type", "time", "data"):
        assert required in body
    assert body["specversion"] == "1.0"
    assert body["data"] == {"k": "v"}


def test_binary_mode_emits_ce_headers() -> None:
    ev = CloudEvent(
        source="https://kairos.local/",
        type=KAIROS_TYPE_DECISION,
        subject="Deployment/prod/api",
    )
    headers = ev.to_binary_headers()
    assert headers["ce-source"] == "https://kairos.local/"
    assert headers["ce-type"] == KAIROS_TYPE_DECISION
    assert headers["ce-subject"] == "Deployment/prod/api"
    assert headers["ce-specversion"] == "1.0"
    assert "ce-id" in headers and "ce-time" in headers


def test_make_decision_event() -> None:
    ev = make_decision_event(
        source="kairos://test",
        workload_uid="Deployment/prod/api",
        decision_payload={"action": "horizontal_up"},
    )
    assert ev.type == KAIROS_TYPE_DECISION
    assert ev.subject == "Deployment/prod/api"
    assert ev.data["action"] == "horizontal_up"


def test_make_approval_event_status_mapping() -> None:
    approved = make_approval_event(
        source="kairos://test",
        workload_uid="Deployment/prod/api",
        approval_id="abc",
        status="approved",
        payload={},
    )
    assert approved.type == KAIROS_TYPE_APPROVAL_APPROVED


def test_make_alert_event_state_mapping() -> None:
    fire = make_alert_event(source="k", fingerprint="fp1", state="firing", payload={})
    resolve = make_alert_event(source="k", fingerprint="fp1", state="resolved", payload={})
    assert fire.type == KAIROS_TYPE_ALERT_FIRING
    assert resolve.type == KAIROS_TYPE_ALERT_RESOLVED


def test_make_pr_event_subject() -> None:
    ev = make_pr_event(
        source="k",
        pr_number=42,
        workload_uid="Deployment/prod/api",
        merged=True,
        payload={"url": "https://github.com/org/repo/pull/42"},
    )
    assert ev.subject.endswith("#pr-42")


# ── Notifier (with respx mocking the webhook target) ──────────────
def _payload() -> NotificationPayload:
    wl = Workload(
        name="api",
        namespace="prod",
        kind=WorkloadKind.DEPLOYMENT,
        runtime=Runtime.UNKNOWN,
        current_replicas=2,
        cpu_request="500m",
        mem_request="512Mi",
    )
    dec = ScalingDecision(
        workload=wl,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="R-001",
        rationale="ce test",
        target_replicas=4,
        severity=Severity.WARNING,
        confidence=0.9,
        correlation_id="cid-ce",
        generated_at=datetime.now(UTC),
    )
    advice = LLMAdvice(
        why="why", horizontal_vs_vertical="hv", risks_of_inaction="risk",
        engineer_steps=["s"], validation_steps=["v"],
        provider_used=__import__("kairos.domain.enums", fromlist=["LLMProviderName"]).LLMProviderName.CANNED,
        prompt_version="v2", tokens_used=0,
    )
    return NotificationPayload(decision=dec, advice=advice, approval_id="ap-1")


@pytest.mark.asyncio
@respx.mock
async def test_notifier_structured_mode_posts_envelope() -> None:
    route = respx.post("https://webhook.local/ce").mock(return_value=httpx.Response(204))
    settings = CloudEventsSettings(
        webhook_url=HttpUrl("https://webhook.local/ce"), source="kairos://test", mode="structured"
    )
    n = CloudEventsNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is True
    assert r.channel == NotificationChannel.CLOUDEVENTS
    assert route.called
    # Body is structured JSON envelope
    sent = route.calls.last.request.read().decode()
    assert KAIROS_TYPE_DECISION in sent
    assert "specversion" in sent
    assert route.calls.last.request.headers["content-type"].startswith("application/cloudevents+json")
    await n.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_notifier_binary_mode_emits_ce_headers() -> None:
    route = respx.post("https://webhook.local/ce-bin").mock(return_value=httpx.Response(202))
    settings = CloudEventsSettings(
        webhook_url=HttpUrl("https://webhook.local/ce-bin"), source="kairos://test", mode="binary"
    )
    n = CloudEventsNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is True
    headers = route.calls.last.request.headers
    assert headers["ce-type"] == KAIROS_TYPE_DECISION
    assert headers["ce-source"] == "kairos://test"
    await n.aclose()


@pytest.mark.asyncio
async def test_notifier_no_webhook_url_returns_error() -> None:
    settings = CloudEventsSettings(webhook_url=None)
    n = CloudEventsNotifier(settings)
    r = await n.send(_payload())
    assert r.delivered is False
    assert r.error is not None and "webhook_url" in r.error
    await n.aclose()


# Suppress an unused-import warning
_ = SecretStr
