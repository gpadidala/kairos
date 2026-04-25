"""Async-friendly circuit breakers. One per external service; exposed as a gauge.

We don't use pybreaker's `call_async` because pybreaker 1.2.0 has a bug
(NameError: 'gen'). This implementation is a small async-first breaker covering
the same semantics: consecutive failures → OPEN; reset_timeout elapses → HALF_OPEN;
a trial success → CLOSED.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import TypeVar

from kairos.domain.exceptions import CircuitOpenError
from kairos.observability.metrics import CIRCUIT_BREAKER_STATE

T = TypeVar("T")


class State(IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitBreaker:
    """Minimal thread-safe(-ish under asyncio) breaker."""

    def __init__(
        self,
        service: str,
        *,
        fail_max: int = 5,
        reset_timeout: int = 60,
    ) -> None:
        self.service = service
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._consecutive_failures = 0
        self._state: State = State.CLOSED
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()
        CIRCUIT_BREAKER_STATE.labels(service=service).set(State.CLOSED.value)

    @property
    def current_state(self) -> State:
        return self._state

    def _transition(self, new_state: State) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
        CIRCUIT_BREAKER_STATE.labels(service=self.service).set(new_state.value)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self._reset_timeout:
                    self._transition(State.HALF_OPEN)
                else:
                    raise CircuitOpenError(self.service, "circuit breaker open")

        try:
            result = await fn()
        except Exception:
            async with self._lock:
                self._consecutive_failures += 1
                if self._state == State.HALF_OPEN or self._consecutive_failures >= self._fail_max:
                    self._transition(State.OPEN)
            raise

        async with self._lock:
            self._consecutive_failures = 0
            if self._state != State.CLOSED:
                self._transition(State.CLOSED)
        return result


_registry: dict[str, CircuitBreaker] = {}


def breaker_for(
    service: str,
    *,
    fail_max: int = 5,
    reset_timeout: int = 60,
) -> CircuitBreaker:
    """Get-or-create a breaker for a named service."""
    if service not in _registry:
        _registry[service] = CircuitBreaker(service, fail_max=fail_max, reset_timeout=reset_timeout)
    return _registry[service]


def update_breaker_gauge(service: str) -> None:
    """Force a gauge refresh (useful from test code)."""
    cb = _registry.get(service)
    if cb is None:
        return
    CIRCUIT_BREAKER_STATE.labels(service=service).set(cb.current_state.value)


def reset_all_breakers() -> None:
    """Reset every registered breaker (tests only)."""
    _registry.clear()
