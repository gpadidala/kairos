"""Structlog configuration. JSON to stdout, trace-correlated, secret-free."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor

from kairos.config.settings import Settings, get_settings

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "token",
        "password",
        "secret",
        "webhook_url",
        "bearer",
        "dsn",
    }
)


def _scrub(d: MutableMapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        lk = k.lower()
        if any(s in lk for s in _SENSITIVE_KEYS):
            out[k] = "***REDACTED***"
        elif isinstance(v, MutableMapping):
            out[k] = _scrub(v)
        else:
            out[k] = v
    return out


def _redact_secrets(_logger: Any, _name: str, event: EventDict) -> EventDict:
    """Scrub any key whose name suggests a secret. Redacts value, keeps key."""
    return cast("EventDict", _scrub(event))


def _add_service_context(service: str, version: str, environment: str) -> Processor:
    def _proc(_logger: Any, _name: str, event: EventDict) -> EventDict:
        event.setdefault("service", service)
        event.setdefault("version", version)
        event.setdefault("environment", environment)
        return event

    return _proc


def configure_logging(settings: Settings | None = None) -> None:
    """Install structlog + stdlib logging. Safe to call more than once."""
    s = settings or get_settings()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_service_context("kairos", s.version, s.environment),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_secrets,
    ]

    if s.logging.json_format:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, s.logging.level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, httpx, etc.) through structlog
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(message)s") if s.logging.json_format else logging.Formatter()
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(s.logging.level)

    # Quiet noisy third parties
    for noisy in ("httpcore", "httpx", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Use module `__name__` by convention."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
