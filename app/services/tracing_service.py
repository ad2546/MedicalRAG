"""Okahu Cloud tracing via direct OTLP HTTP export.

Sends spans directly to Okahu's ingestion endpoint using the OpenTelemetry
OTLP HTTP exporter — bypassing monocle's sync-only OpenAI instrumentation.

Every LLM call, retrieval step, and pipeline run appears as a workflow trace
in portal.okahu.co.

Degrades silently to a no-op when OKAHU_API_KEY is absent.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_tracer_initialized = False
_otel_tracer = None

# Okahu OTLP HTTP ingestion endpoint
_OKAHU_OTLP_ENDPOINT = "https://ingest.okahu.co/api/v1/monocle/traces"


def _init_tracer() -> None:
    global _tracer_initialized, _otel_tracer
    if _tracer_initialized:
        return

    if not settings.okahu_api_key:
        logger.warning("OKAHU_API_KEY not set — Okahu Cloud tracing disabled.")
        _tracer_initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            "service.name": settings.okahu_service_name,
            "service.version": "1.0.0",
        })

        exporter = OTLPSpanExporter(
            endpoint=_OKAHU_OTLP_ENDPOINT,
            headers={"x-okahu-api-key": settings.okahu_api_key},
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _otel_tracer = trace.get_tracer("medicalrag")
        logger.info(
            "Okahu Cloud tracing initialised (OTLP) — workflow=%s endpoint=%s",
            settings.okahu_service_name,
            _OKAHU_OTLP_ENDPOINT,
        )
    except Exception as exc:
        logger.warning("Okahu tracing init failed: %s — running without tracing", exc)

    _tracer_initialized = True


# Initialise eagerly so the provider is registered before any LLM client is created.
_init_tracer()


class TracingService:
    """Wraps OpenTelemetry spans and exports them to Okahu Cloud."""

    def new_trace_id(self) -> str:
        return str(uuid.uuid4())

    def trace_event(
        self,
        trace_id: str,
        event_name: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a named event as a completed OTel span."""
        if _otel_tracer is None:
            logger.debug("TRACE [%s] trace=%s", event_name, trace_id)
            return

        span = _otel_tracer.start_span(event_name)
        span.set_attribute("trace_id", trace_id)
        span.set_attribute("event", event_name)
        for k, v in inputs.items():
            span.set_attribute(f"input.{k}", str(v))
        for k, v in outputs.items():
            span.set_attribute(f"output.{k}", str(v))
        if metadata:
            for k, v in metadata.items():
                span.set_attribute(f"meta.{k}", str(v))
        span.end()

    @asynccontextmanager
    async def span(self, trace_id: str, name: str, attributes: dict | None = None):
        """Async context manager that wraps an agent step in an OTel span."""
        if _otel_tracer is None:
            logger.debug("SPAN [%s] trace=%s", name, trace_id)
            yield
            return

        otel_span = _otel_tracer.start_span(name)
        otel_span.set_attribute("trace_id", trace_id)
        if attributes:
            for k, v in attributes.items():
                otel_span.set_attribute(k, str(v))
        try:
            yield otel_span
        finally:
            otel_span.end()


tracing_service = TracingService()
