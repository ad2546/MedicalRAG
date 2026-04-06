"""Okahu Cloud tracing via monocle-apptrace.

Uses setup_monocle_telemetry with OkahuSpanExporter passed explicitly via
span_processors — this gives us:
  1. Correct Okahu endpoint/format/auth (OkahuSpanExporter)
  2. Proper monocle span attributes (workflow_name, monocle.span.type, etc.)
     that the Okahu portal needs to display workflow traces
  3. Auto-instrumentation of AsyncCompletions (Groq calls traced automatically)

Degrades silently to a no-op when OKAHU_API_KEY is absent.
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Sequence

from app.config import settings

logger = logging.getLogger(__name__)

_tracer_initialized = False
_otel_tracer = None


class _LoggingOkahuExporter:
    """Thin wrapper around OkahuSpanExporter that logs at INFO level.

    Used to confirm spans are reaching Okahu without having to enable DEBUG.
    """

    def __init__(self, inner):
        self._inner = inner
        self._export_count = 0

    def export(self, spans: Sequence) -> Any:
        import json as _json
        from monocle_apptrace.instrumentation.common.constants import MONOCLE_SDK_VERSION

        monocle_spans = [s for s in spans if s.attributes.get(MONOCLE_SDK_VERSION)]
        self._export_count += 1
        logger.info(
            "Okahu export #%d — total=%d monocle=%d all_names=%s",
            self._export_count,
            len(spans),
            len(monocle_spans),
            [s.name for s in spans],
        )
        # Log span tree (name, trace_id, parent_span_id, type) to diagnose linkage.
        if self._export_count <= 5 and monocle_spans:
            for s in monocle_spans[:8]:
                try:
                    ctx = s.context
                    parent = s.parent
                    logger.info(
                        "  span: name=%r type=%s trace=%016x parent=%s",
                        s.name,
                        (s.attributes or {}).get("span.type", (s.attributes or {}).get("entity.1.type", "?")),
                        ctx.trace_id if ctx else 0,
                        "%016x" % parent.span_id if parent else "ROOT",
                    )
                except Exception as exc:
                    logger.warning("Could not log span context: %s", exc)
            try:
                sample = _json.loads(monocle_spans[0].to_json())
                # Log resource attrs + monocle span attrs — the fields Okahu uses to route traces
                logger.info(
                    "Okahu span sample — resource=%s attrs=%s events=%s",
                    sample.get("resource", {}).get("attributes", {}),
                    {k: v for k, v in (sample.get("attributes") or {}).items()
                     if any(k.startswith(p) for p in ("workflow", "monocle", "entity", "span.type", "service"))},
                    [(e.get("name"), list((e.get("attributes") or {}).keys()))
                     for e in (sample.get("events") or [])[:4]],
                )
            except Exception as exc:
                logger.warning("Could not serialise sample span: %s", exc)

        result = self._inner.export(spans)
        logger.info("Okahu export #%d result: %s", self._export_count, result)
        return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._inner.shutdown()


def _test_okahu_connectivity(api_key: str) -> None:
    """Fire a quick connectivity check at startup so we know Okahu is reachable."""
    try:
        import requests as _req
        resp = _req.post(
            "https://ingest.okahu.co/api/v1/trace/ingest",
            json={"batch": []},
            headers={"Content-Type": "application/json", "x-api-key": api_key},
            timeout=8,
        )
        logger.info(
            "Okahu connectivity check — status=%d body=%.200s",
            resp.status_code,
            resp.text,
        )
    except Exception as exc:
        logger.warning("Okahu connectivity check failed: %s", exc)


def _init_tracer() -> None:
    global _tracer_initialized, _otel_tracer
    if _tracer_initialized:
        return

    if not settings.okahu_api_key:
        logger.warning("OKAHU_API_KEY not set — Okahu Cloud tracing disabled.")
        _tracer_initialized = True
        return

    try:
        os.environ.setdefault("OKAHU_API_KEY", settings.okahu_api_key)

        # Verify the API key can actually reach Okahu before we set up the whole
        # pipeline — this surfaces auth/network problems immediately in startup logs.
        _test_okahu_connectivity(settings.okahu_api_key)

        from monocle_apptrace.exporters.okahu.okahu_exporter import OkahuSpanExporter
        from monocle_apptrace.instrumentation.common import setup_monocle_telemetry
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        okahu_exporter = OkahuSpanExporter()
        logging_exporter = _LoggingOkahuExporter(okahu_exporter)

        # Pass OkahuSpanExporter via span_processors so monocle uses the
        # correct endpoint/auth while still adding all monocle workflow attributes
        # and auto-instrumenting AsyncCompletions (Groq calls).
        setup_monocle_telemetry(
            workflow_name=settings.okahu_service_name,
            span_processors=[BatchSpanProcessor(logging_exporter)],
        )

        from opentelemetry import trace
        _otel_tracer = trace.get_tracer("medicalrag")

        logger.info(
            "Okahu Cloud tracing initialised — workflow=%s",
            settings.okahu_service_name,
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
