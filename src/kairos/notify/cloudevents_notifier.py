"""CloudEvents 1.0 webhook notifier.

Wraps the standard NotificationPayload as a CloudEvent and POSTs it to a
configured webhook URL. Supports both 'structured' (JSON envelope in body)
and 'binary' (ce-* HTTP headers + raw data body) modes.

Sinks: Knative EventSource, Azure Event Grid topic ingest, AWS EventBridge
endpoint, Google Eventarc, plus any custom CE-aware webhook.
"""

from __future__ import annotations

import httpx
import structlog

from kairos.config.settings import CloudEventsSettings
from kairos.domain.enums import NotificationChannel
from kairos.domain.models import NotificationResult
from kairos.notify.base import NotificationPayload, Notifier
from kairos.observability.cloud_events import (
    KAIROS_TYPE_DECISION,
    CloudEvent,
    make_decision_event,
)
from kairos.observability.metrics import NOTIFICATIONS_SENT

log = structlog.get_logger(__name__)


class CloudEventsNotifier(Notifier):
    """Notifier that POSTs every payload as a CloudEvent."""

    channel = NotificationChannel.CLOUDEVENTS

    def __init__(self, settings: CloudEventsSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        if self._settings.webhook_url is None:
            return NotificationResult(
                channel=self.channel,
                delivered=False,
                error="cloudevents.webhook_url not configured",
            )

        event = make_decision_event(
            source=self._settings.source,
            workload_uid=payload.decision.workload.uid,
            decision_payload=payload.decision.model_dump(mode="json"),
        )
        if payload.advice is not None:
            event.data["advice"] = payload.advice.model_dump(mode="json")
        if payload.pr_url:
            event.data["pr_url"] = payload.pr_url
        if payload.approval_id:
            event.data["approval_id"] = payload.approval_id

        try:
            await _post_event(
                self._client,
                str(self._settings.webhook_url),
                event,
                mode=self._settings.mode,
            )
        except Exception as exc:
            NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="error").inc()
            return NotificationResult(
                channel=self.channel,
                delivered=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        NOTIFICATIONS_SENT.labels(channel=self.channel.value, result="ok").inc()
        log.info(
            "cloudevent_delivered",
            workload=payload.decision.workload.uid,
            ce_type=KAIROS_TYPE_DECISION,
            mode=self._settings.mode,
        )
        return NotificationResult(channel=self.channel, delivered=True)


async def _post_event(
    client: httpx.AsyncClient, url: str, event: CloudEvent, *, mode: str
) -> None:
    """POST a CloudEvent in either structured or binary mode."""
    if mode == "binary":
        # Headers carry envelope; body is just the data payload.
        headers = event.to_binary_headers()
        r = await client.post(url, json=event.data, headers=headers)
    else:
        # Structured mode: full envelope as JSON body.
        body = event.to_structured_json()
        r = await client.post(
            url, json=body, headers={"content-type": "application/cloudevents+json"}
        )
    r.raise_for_status()
