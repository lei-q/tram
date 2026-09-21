"""Optional OpenTelemetry hookup - a no-op unless tram[otel] is installed."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def start_telemetry(service_name: str = "tram") -> object | None:
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        logger.debug("opentelemetry not installed; event log remains the black box")
        return None

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    return provider
