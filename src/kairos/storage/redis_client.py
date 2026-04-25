"""Async Redis client wrapper with graceful-degradation fallback."""

from __future__ import annotations

import contextlib
from typing import Any

import redis.asyncio as aioredis
import structlog
from redis.asyncio.client import Redis

from kairos.config.settings import RedisSettings

log = structlog.get_logger(__name__)


class RedisClient:
    """
    Thin wrapper over redis.asyncio.

    Methods fail-open: if Redis is unreachable, they log a warning and return a
    "permissive" sentinel so pipelines continue (see ADR-0005).
    """

    def __init__(self, client: Redis, settings: RedisSettings) -> None:
        self._client = client
        self._settings = settings
        self._healthy = True

    @classmethod
    def from_settings(cls, settings: RedisSettings) -> RedisClient:
        client: Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.url,
            socket_timeout=settings.timeout_seconds,
            socket_connect_timeout=settings.timeout_seconds,
            decode_responses=True,
        )
        return cls(client, settings)

    @property
    def healthy(self) -> bool:
        return self._healthy

    async def ping(self) -> bool:
        try:
            await self._client.ping()
        except Exception as exc:
            self._healthy = False
            log.warning("redis_ping_failed", error=str(exc))
            return False
        self._healthy = True
        return True

    async def set_nx_ex(self, key: str, value: str, ttl_seconds: int) -> bool:
        """
        Atomic SET NX EX. Returns True if the key was set (first-seen), False if
        it already existed (dedup hit). On Redis error, returns True (fail-open).
        """
        try:
            result = await self._client.set(key, value, ex=ttl_seconds, nx=True)
            return bool(result)
        except Exception as exc:
            log.warning("redis_set_nx_failed_open", key=key, error=str(exc))
            self._healthy = False
            return True

    async def get(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
            return value if value is None else str(value)
        except Exception as exc:
            log.warning("redis_get_failed", key=key, error=str(exc))
            self._healthy = False
            return None

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        try:
            if ttl_seconds is not None:
                await self._client.set(key, value, ex=ttl_seconds)
            else:
                await self._client.set(key, value)
            return True
        except Exception as exc:
            log.warning("redis_set_failed", key=key, error=str(exc))
            self._healthy = False
            return False

    async def delete(self, key: str) -> int:
        try:
            result: Any = await self._client.delete(key)
            return int(result)
        except Exception as exc:
            log.warning("redis_delete_failed", key=key, error=str(exc))
            return 0

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._client.aclose()


class _RedisState:
    singleton: RedisClient | None = None


async def get_redis(settings: RedisSettings) -> RedisClient:
    """Return a process-wide RedisClient singleton."""
    if _RedisState.singleton is None:
        _RedisState.singleton = RedisClient.from_settings(settings)
    return _RedisState.singleton


def reset_redis_singleton() -> None:
    """Used by tests."""
    _RedisState.singleton = None
