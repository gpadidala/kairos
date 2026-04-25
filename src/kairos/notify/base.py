"""Notifier abstract interface + shared payload DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from kairos.domain.enums import NotificationChannel
from kairos.domain.models import LLMAdvice, NotificationResult, ScalingDecision


@dataclass(frozen=True, slots=True)
class NotificationPayload:
    """What notifiers receive. Held as a dataclass so the dispatcher can log + dedup."""

    decision: ScalingDecision
    advice: LLMAdvice | None
    pr_url: str | None = None
    grafana_url: str | None = None


class Notifier(ABC):
    channel: NotificationChannel

    @abstractmethod
    async def send(self, payload: NotificationPayload) -> NotificationResult: ...

    @abstractmethod
    async def aclose(self) -> None: ...
