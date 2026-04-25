"""OpenTelemetry tracing bootstrap. No-op when tracing is disabled."""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Tracer

from kairos.config.settings import Settings

try:  # Optional — collector may be unreachable at import time
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    _OTLP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTLP_AVAILABLE = False

log = logging.getLogger(__name__)


class _TracingState:
    configured: bool = False


def configure_tracing(settings: Settings) -> None:
    """Install OTel TracerProvider with OTLP exporter. Idempotent."""
    if _TracingState.configured or not settings.tracing.enabled:
        return

    resource = Resource.create(
        {
            "service.name": settings.tracing.service_name,
            "service.version": settings.version,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.tracing.sample_ratio),
    )

    if _OTLP_AVAILABLE:
        try:
            exporter = OTLPSpanExporter(endpoint=settings.tracing.otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as exc:  # pragma: no cover — exporter optional at runtime
            log.warning("otlp_exporter_init_failed", exc_info=exc)

    trace.set_tracer_provider(provider)
    _TracingState.configured = True


def get_tracer(name: str = "kairos") -> Tracer:
    return trace.get_tracer(name)
