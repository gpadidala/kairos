"""Fan-out dispatcher — parallel send with per-channel dedup + partial failure tolerance."""

from __future__ import annotations

import asyncio

import structlog

from pcap.domain.enums import NotificationChannel
from pcap.domain.models import NotificationResult, ScalingDecision
from pcap.notify.base import NotificationPayload, Notifier
from pcap.storage.dedup import DedupStore

log = structlog.get_logger(__name__)


class NotifyDispatcher:
    """Delivers one payload to every configured channel concurrently."""

    def __init__(self, notifiers: list[Notifier], dedup: DedupStore) -> None:
        self._notifiers = notifiers
        self._dedup = dedup

    async def aclose(self) -> None:
        await asyncio.gather(*(n.aclose() for n in self._notifiers), return_exceptions=True)

    async def fan_out(self, payload: NotificationPayload) -> list[NotificationResult]:
        if not self._notifiers:
            return []

        async def _one(notifier: Notifier) -> NotificationResult:
            return await self._dispatch_one(notifier, payload)

        results = await asyncio.gather(*(_one(n) for n in self._notifiers), return_exceptions=True)
        out: list[NotificationResult] = []
        for notifier, r in zip(self._notifiers, results, strict=True):
            if isinstance(r, BaseException):
                log.exception(
                    "notifier_raised",
                    channel=notifier.channel.value,
                    error=str(r),
                )
                out.append(
                    NotificationResult(
                        channel=notifier.channel,
                        delivered=False,
                        error=f"{type(r).__name__}: {r}",
                    )
                )
            else:
                out.append(r)
        return out

    async def _dispatch_one(
        self, notifier: Notifier, payload: NotificationPayload
    ) -> NotificationResult:
        decision: ScalingDecision = payload.decision
        first_sight = await self._dedup.first_sight_notify(notifier.channel, decision)
        if not first_sight:
            return NotificationResult(channel=notifier.channel, delivered=False, dedup_hit=True)
        try:
            return await notifier.send(payload)
        except Exception as exc:
            log.exception(
                "notifier_send_raised",
                channel=notifier.channel.value,
                error=str(exc),
            )
            return NotificationResult(
                channel=notifier.channel, delivered=False, error=f"{type(exc).__name__}: {exc}"
            )

    @property
    def channels(self) -> list[NotificationChannel]:
        return [n.channel for n in self._notifiers]
