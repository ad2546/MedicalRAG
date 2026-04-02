"""Okahu Cloud tracing via monocle-apptrace + OpenTelemetry.

Setup:
  1. Set OKAHU_API_KEY=<your key> in .env  (get from Okahu portal or welcome email)
  2. Set MONOCLE_EXPORTER=okahu in .env
  3. Set OKAHU_SERVICE_NAME=medicalChatbot (must match app name in portal)

Monocle reads OKAHU_API_KEY and MONOCLE_EXPORTER directly from the environment.
Every pipeline run will appear as a workflow trace in portal.okahu.co.

Degrades silently to local OTel (no-op exporter) when OKAHU_API_KEY is absent.
"""

import logging
import os
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

    if not settings.okahu_api_key:
        logger.warning(
            "OKAHU_API_KEY not set — Okahu Cloud tracing disabled. "
            "Add OKAHU_API_KEY=<key> and MONOCLE_EXPORTER=okahu to .env to enable."
        )
        _tracer_initialized = True
        return

    try:
        # Ensure env vars are set before monocle initialises its exporter
        os.environ.setdefault("OKAHU_API_KEY", settings.okahu_api_key)
        os.environ.setdefault("MONOCLE_EXPORTER", "okahu")

        from monocle_apptrace.instrumentation.common import setup_monocle_telemetry

        setup_monocle_telemetry(workflow_name=settings.okahu_service_name)
        logger.info(
            "Okahu Cloud tracing initialised — workflow=%s", settings.okahu_service_name
        )
    except Exception as exc:
        logger.warning("Okahu SDK init failed: %s — falling back to local OTel", exc)

    # Obtain OTel tracer (no-ops if monocle didn't register a real provider)
    from opentelemetry import trace

    _otel_tracer = trace.get_tracer("medicalrag")
    _tracer_initialized = True


# Initialise eagerly — OTel provider must be in place before any LLM client is created.
_init_tracer()


class TracingService:
    """Facade that wraps OpenTelemetry spans and forwards them to Okahu Cloud."""

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
