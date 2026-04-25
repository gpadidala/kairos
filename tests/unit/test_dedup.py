"""Dedup key construction + DedupStore behavior against fakeredis."""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from kairos.config.settings import RedisSettings
from kairos.domain.enums import NotificationChannel, ScalingAction, Severity
from kairos.domain.models import ScalingDecision, Workload
from kairos.storage.dedup import DedupKind, DedupStore, dedup_key
from kairos.storage.redis_client import RedisClient


@pytest.fixture
def redis_client() -> RedisClient:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RedisClient(fake, RedisSettings())


@pytest.fixture
def dedup(redis_client: RedisClient) -> DedupStore:
    return DedupStore(redis_client, ttl_pr=3600, ttl_notify=1800, ttl_forecast=3600)


def test_dedup_key_format() -> None:
    k = dedup_key(DedupKind.PR, "Deployment/prod/api", "abc123")
    assert k == "pr:Deployment/prod/api:abc123"


def test_dedup_key_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one part"):
        dedup_key(DedupKind.PR)


def test_dedup_key_escapes_colons() -> None:
    k = dedup_key(DedupKind.NOTIFY, "teams:channel", "abc")
    assert ":" not in k.split(":", 1)[1].split("_")[0] or k.count(":") == 2


async def test_first_sight_returns_true_once(
    dedup: DedupStore, sample_workload: Workload, fixed_now: datetime
) -> None:
    dec = ScalingDecision(
        workload=sample_workload,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="R-001",
        rationale="x",
        target_replicas=5,
        severity=Severity.WARNING,
        confidence=0.9,
        correlation_id="c",
        generated_at=fixed_now,
    )
    assert await dedup.first_sight_pr(dec) is True
    assert await dedup.first_sight_pr(dec) is False


async def test_first_sight_notify_per_channel(
    dedup: DedupStore, sample_workload: Workload, fixed_now: datetime
) -> None:
    dec = ScalingDecision(
        workload=sample_workload,
        action=ScalingAction.HORIZONTAL_UP,
        reason_code="R-001",
        rationale="x",
        target_replicas=5,
        severity=Severity.WARNING,
        confidence=0.9,
        correlation_id="c",
        generated_at=fixed_now,
    )
    # Each channel has its own namespace
    assert await dedup.first_sight_notify(NotificationChannel.TEAMS, dec) is True
    assert await dedup.first_sight_notify(NotificationChannel.SLACK, dec) is True
    assert await dedup.first_sight_notify(NotificationChannel.TEAMS, dec) is False


async def test_forecast_cache_round_trip(dedup: DedupStore, sample_workload: Workload) -> None:
    bucket = datetime(2026, 4, 23, 12, 0, tzinfo=UTC).strftime("%Y%m%d%H")
    assert await dedup.get_forecast_cache(sample_workload, "cpu", bucket) is None
    assert await dedup.set_forecast_cache(sample_workload, "cpu", bucket, '{"x":1}') is True
    assert await dedup.get_forecast_cache(sample_workload, "cpu", bucket) == '{"x":1}'


async def test_redis_ping_healthy(redis_client: RedisClient) -> None:
    assert await redis_client.ping() is True
    assert redis_client.healthy is True
