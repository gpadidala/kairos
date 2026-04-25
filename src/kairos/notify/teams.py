"""Microsoft Teams — Adaptive Card v1.5 via incoming webhook."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from kairos.config.settings import TeamsSettings
from kairos.domain.enums import NotificationChannel
from kairos.domain.models import NotificationResult
from kairos.notify.base import NotificationPayload, Notifier
from kairos.observability.metrics import NOTIFICATIONS_SENT

log = structlog.get_logger(__name__)

_SEVERITY_COLOR = {
    "info": "good",
    "warning": "warning",
    "critical": "attention",
}


def build_teams_card(payload: NotificationPayload) -> dict[str, Any]:
    d = payload.decision
    w = d.workload
    facts: list[dict[str, str]] = [
        {"title": "Workload", "value": w.uid},
        {"title": "Action", "value": d.action.value},
        {"title": "Reason", "value": d.reason_code},
        {"title": "Severity", "value": d.severity.value},
        {"title": "Confidence", "value": f"{d.confidence:.2f}"},
    ]
    if d.target_replicas is not None:
        facts.append({"title": "Target replicas", "value": str(d.target_replicas)})
    if d.target_cpu_request:
        facts.append({"title": "Target CPU", "value": d.target_cpu_request})
    if d.target_mem_request:
        facts.append({"title": "Target memory", "value": d.target_mem_request})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"KAIROS: {d.action.value} for {w.uid}",
            "color": _SEVERITY_COLOR.get(d.severity.value, "default"),
        },
        {"type": "TextBlock", "wrap": True, "text": d.rationale},
        {"type": "FactSet", "facts": facts},
    ]
    if payload.advice is not None:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "text": f"**Why:** {payload.advice.why}",
            }
        )

    actions: list[dict[str, Any]] = []
    if payload.pr_url:
        actions.append({"type": "Action.OpenUrl", "title": "Review PR", "url": payload.pr_url})
    if payload.grafana_url:
        actions.append(
            {"type": "Action.OpenUrl", "title": "Open Grafana", "url": payload.grafana_url}
        )

    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
    }
    if actions:
        card["actions"] = actions

    # Teams incoming webhook envelope
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


class TeamsNotifier(Notifier):
    channel = NotificationChannel.TEAMS

    def __init__(self, settings: TeamsSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout_seconds))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        if self._settings.webhook_url is None:
            return NotificationResult(
                channel=self.channel, delivered=False, error="teams webhook not configured"
            )
        body = build_teams_card(payload)
        try:
            r = await self._client.post(self._settings.webhook_url.get_secret_value(), json=body)
            if r.status_code >= 400:
                NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="http_error").inc()
                return NotificationResult(
                    channel=self.channel,
                    delivered=False,
                    error=f"{r.status_code}: {r.text[:200]}",
                )
        except httpx.HTTPError as exc:
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="transport").inc()
            return NotificationResult(
                channel=self.channel, delivered=False, error=f"{type(exc).__name__}: {exc}"
            )
        NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="ok").inc()
        log.info("teams_delivered", workload=payload.decision.workload.uid)
        return NotificationResult(channel=self.channel, delivered=True)
