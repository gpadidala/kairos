"""Slack — Block Kit via incoming webhook or bot token."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from kairos.config.settings import SlackSettings
from kairos.domain.enums import NotificationChannel
from kairos.domain.models import NotificationResult
from kairos.notify.base import NotificationPayload, Notifier
from kairos.observability.metrics import NOTIFICATIONS_SENT

log = structlog.get_logger(__name__)

_SEVERITY_EMOJI = {"info": ":bulb:", "warning": ":warning:", "critical": ":rotating_light:"}


def build_slack_blocks(payload: NotificationPayload) -> dict[str, Any]:
    d = payload.decision
    w = d.workload
    emoji = _SEVERITY_EMOJI.get(d.severity.value, ":information_source:")

    fields = [
        {"type": "mrkdwn", "text": f"*Workload*\n`{w.uid}`"},
        {"type": "mrkdwn", "text": f"*Action*\n`{d.action.value}`"},
        {"type": "mrkdwn", "text": f"*Reason*\n`{d.reason_code}`"},
        {"type": "mrkdwn", "text": f"*Severity*\n{d.severity.value}"},
        {"type": "mrkdwn", "text": f"*Confidence*\n{d.confidence:.2f}"},
    ]
    if d.target_replicas is not None:
        fields.append({"type": "mrkdwn", "text": f"*Target replicas*\n{d.target_replicas}"})
    if d.target_cpu_request:
        fields.append({"type": "mrkdwn", "text": f"*Target CPU*\n{d.target_cpu_request}"})
    if d.target_mem_request:
        fields.append({"type": "mrkdwn", "text": f"*Target memory*\n{d.target_mem_request}"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} KAIROS: {d.action.value} for {w.uid}",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": d.rationale}},
        {"type": "section", "fields": fields},
    ]
    if payload.advice is not None:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why:* {payload.advice.why}"}}
        )
    actions: list[dict[str, Any]] = []
    if payload.pr_url:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Review PR"},
                "url": payload.pr_url,
            }
        )
    if payload.grafana_url:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open Grafana"},
                "url": payload.grafana_url,
            }
        )
    if actions:
        blocks.append({"type": "actions", "elements": actions})

    return {"blocks": blocks, "text": f"KAIROS {d.action.value} for {w.uid}"}


class SlackNotifier(Notifier):
    channel = NotificationChannel.SLACK

    def __init__(self, settings: SlackSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout_seconds))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        body = build_slack_blocks(payload)
        if self._settings.webhook_url is not None:
            return await self._send_webhook(body)
        if self._settings.bot_token is not None and self._settings.channel:
            return await self._send_api(body)
        return NotificationResult(
            channel=self.channel,
            delivered=False,
            error="slack not configured (need webhook_url or bot_token+channel)",
        )

    async def _send_webhook(self, body: dict[str, Any]) -> NotificationResult:
        assert self._settings.webhook_url is not None
        try:
            r = await self._client.post(self._settings.webhook_url.get_secret_value(), json=body)
        except httpx.HTTPError as exc:
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="transport").inc()
            return NotificationResult(
                channel=self.channel, delivered=False, error=f"{type(exc).__name__}: {exc}"
            )
        if r.status_code >= 400 or (r.text and r.text != "ok"):
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="http_error").inc()
            return NotificationResult(
                channel=self.channel,
                delivered=False,
                error=f"{r.status_code}: {r.text[:200]}",
            )
        NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="ok").inc()
        return NotificationResult(channel=self.channel, delivered=True)

    async def _send_api(self, body: dict[str, Any]) -> NotificationResult:
        assert self._settings.bot_token is not None
        api_body = dict(body)
        api_body["channel"] = self._settings.channel
        try:
            r = await self._client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self._settings.bot_token.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json=api_body,
            )
        except httpx.HTTPError as exc:
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="transport").inc()
            return NotificationResult(
                channel=self.channel, delivered=False, error=f"{type(exc).__name__}: {exc}"
            )
        data = r.json()
        if not data.get("ok"):
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="api_error").inc()
            return NotificationResult(
                channel=self.channel, delivered=False, error=str(data.get("error", "unknown"))
            )
        NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="ok").inc()
        return NotificationResult(channel=self.channel, delivered=True)
