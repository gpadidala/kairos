"""Dedup key generation + SET NX EX gating. See ADR-0005."""

from __future__ import annotations

from enum import StrEnum

import structlog

from pcap.domain.enums import NotificationChannel
from pcap.domain.models import ScalingDecision, Workload
from pcap.observability.metrics import DEDUP_HITS_TOTAL
from pcap.storage.redis_client import RedisClient

log = structlog.get_logger(__name__)


class DedupKind(StrEnum):
    PR = "pr"
    NOTIFY = "notify"
    FORECAST = "forecast"


def dedup_key(kind: DedupKind, *parts: str) -> str:
    """Build a canonical dedup key `{kind}:{p1}:{p2}:...`."""
    if not parts:
        raise ValueError("dedup_key requires at least one part after kind")
    safe_parts = [p.replace(":", "_") for p in parts]
    return f"{kind.value}:{':'.join(safe_parts)}"


class DedupStore:
    """Thin wrapper that gates side effects via Redis SET NX EX."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        ttl_pr: int,
        ttl_notify: int,
        ttl_forecast: int,
    ) -> None:
        self._redis = redis
        self._ttl = {
            DedupKind.PR: ttl_pr,
            DedupKind.NOTIFY: ttl_notify,
            DedupKind.FORECAST: ttl_forecast,
        }

    async def first_sight(self, kind: DedupKind, key: str, value: str = "1") -> bool:
        """
        Returns True if this key was freshly set (proceed with side effect).
        Returns False if the key already existed (dedup hit; skip).
        """
        ttl = self._ttl[kind]
        set_ok = await self._redis.set_nx_ex(key, value, ttl)
        if not set_ok:
            DEDUP_HITS_TOTAL.labels(kind=kind.value).inc()
            log.info("dedup_hit", kind=kind.value, key=key)
        return set_ok

    # Convenience helpers — stable key shapes defined in ADR-0005
    async def first_sight_pr(self, decision: ScalingDecision) -> bool:
        key = dedup_key(DedupKind.PR, decision.workload.uid, decision.decision_hash())
        return await self.first_sight(DedupKind.PR, key)

    async def first_sight_notify(
        self, channel: NotificationChannel, decision: ScalingDecision
    ) -> bool:
        key = dedup_key(
            DedupKind.NOTIFY, channel.value, decision.workload.uid, decision.decision_hash()
        )
        return await self.first_sight(DedupKind.NOTIFY, key)

    def forecast_cache_key(self, workload: Workload, metric: str, bucket: str) -> str:
        return dedup_key(DedupKind.FORECAST, workload.uid, metric, bucket)

    async def get_forecast_cache(self, workload: Workload, metric: str, bucket: str) -> str | None:
        return await self._redis.get(self.forecast_cache_key(workload, metric, bucket))

    async def set_forecast_cache(
        self, workload: Workload, metric: str, bucket: str, payload: str
    ) -> bool:
        return await self._redis.set(
            self.forecast_cache_key(workload, metric, bucket),
            payload,
            ttl_seconds=self._ttl[DedupKind.FORECAST],
        )
