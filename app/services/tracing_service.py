"""Okuha Cloud tracing via OpenTelemetry.

Okuha auto-instruments OpenAI calls through OTel. This module:
  1. Calls setup_okahu_telemetry() at import time so the OTel TracerProvider
     is registered before any OpenAI client is created.
  2. Exposes span() and trace_event() helpers that emit real OTel spans,
     which Okuha picks up and sends to the cloud dashboard.
  3. Degrades silently to local logging when OKUHA_API_KEY is absent.

Captured per-pipeline:
  - All agent inputs/outputs
  - Token usage per LLM call (via auto-instrumentation)
  - Evidence selection per retrieval round
  - Reflection reasoning
  - Final structured output
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_tracer_initialized = False
_otel_tracer = None


def _init_tracer() -> None:
    global _tracer_initialized, _otel_tracer
    if _tracer_initialized:
        return

    if not settings.okuha_api_key:
        logger.warning("OKUHA_API_KEY not set — Okuha tracing disabled, falling back to local logs")
        _tracer_initialized = True  # mark done so we don't retry every call
        return

    try:
        import os

        from monocle_apptrace.instrumentation.common import setup_monocle_telemetry

        # Monocle reads MONOCLE_API_KEY from the environment
        os.environ.setdefault("MONOCLE_API_KEY", settings.okuha_api_key)
        setup_monocle_telemetry(
            workflow_name=settings.okuha_service_name,
        )
        logger.info("Monocle/Okahu tracing initialised (service=%s)", settings.okuha_service_name)
    except Exception as exc:
        logger.warning("Okuha SDK init failed: %s — falling back to local logs", exc)

    # Obtain the OTel tracer regardless (no-ops if Okuha didn't register a real provider)
    from opentelemetry import trace

    _otel_tracer = trace.get_tracer("medicalrag")
    _tracer_initialized = True


# Initialise eagerly so the OTel provider is in place before any AsyncOpenAI client is created.
_init_tracer()


class TracingService:
    """Facade that wraps OpenTelemetry spans and forwards them to Okuha Cloud."""

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
        """Emit a named event as an OTel span with attributes."""
        if _otel_tracer is None:
            logger.debug("TRACE [%s] trace=%s inputs=%s outputs=%s", event_name, trace_id, inputs, outputs)
            return

        with _otel_tracer.start_as_current_span(event_name) as span:
            span.set_attribute("trace_id", trace_id)
            span.set_attribute("event", event_name)
            # Flatten dicts to scalar OTel attributes
            for k, v in inputs.items():
                span.set_attribute(f"input.{k}", str(v))
            for k, v in outputs.items():
                span.set_attribute(f"output.{k}", str(v))
            if metadata:
                for k, v in metadata.items():
                    span.set_attribute(f"meta.{k}", str(v))

    @asynccontextmanager
    async def span(self, trace_id: str, name: str, attributes: dict | None = None):
        """Async context manager that wraps an agent step in an OTel span."""
        if _otel_tracer is None:
            logger.debug("SPAN [%s] trace=%s", name, trace_id)
            yield
            return

        with _otel_tracer.start_as_current_span(name) as otel_span:
            otel_span.set_attribute("trace_id", trace_id)
            if attributes:
                for k, v in attributes.items():
                    otel_span.set_attribute(k, str(v))
            yield otel_span


tracing_service = TracingService()
