"""Tenacity retry policies."""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.ConnectError | httpx.ReadError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def http_retry(attempts: int = 3) -> AsyncRetrying:
    """Exponential-backoff retry for idempotent HTTP calls."""
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=0.2, max=5.0),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
