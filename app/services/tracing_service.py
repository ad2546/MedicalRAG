"""Okahu Cloud tracing via monocle-apptrace.

Follows the same pattern as the okahu-demos reference apps:
  setup_monocle_telemetry(workflow_name=<app_name>, monocle_exporters_list='okahu')

workflow_name / service.name must match the Okahu app name exactly so that
Okahu's ingestion pipeline routes spans to the correct portal app.

The _LoggingOkahuExporter wrapper gives INFO-level visibility into every
export batch without requiring DEBUG logging.

Degrades silently to a no-op when OKAHU_API_KEY is absent or in pytest.
"""

from __future__ import annotations

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

    Used to confirm spans are reaching Okahu without enabling DEBUG globally.
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
            "Okahu export #%d — total=%d monocle=%d span_names=%s",
            self._export_count,
            len(spans),
            len(monocle_spans),
            [s.name for s in monocle_spans],
        )
        if self._export_count <= 3 and monocle_spans:
            try:
                sample = _json.loads(monocle_spans[0].to_json())
                logger.info(
                    "Okahu span sample — resource=%s workflow=%s type=%s",
                    sample.get("resource", {}).get("attributes", {}).get("service.name"),
                    (sample.get("attributes") or {}).get("workflow.name"),
                    (sample.get("attributes") or {}).get("span.type"),
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
    """Fire a quick connectivity check at startup to confirm Okahu is reachable."""
    try:
        import requests as _req
        resp = _req.post(
            "https://ingest.okahu.co/api/v1/trace/ingest",
            json={"batch": []},
            headers={"Content-Type": "application/json", "x-api-key": api_key},
            timeout=3,
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

    import sys
    if "pytest" in sys.modules:
        logger.debug("Test environment detected — skipping Okahu tracer init.")
        _tracer_initialized = True
        return

    try:
        # Set env vars that monocle + OkahuSpanExporter read internally.
        # MONOCLE_EXPORTER=okahu is also used by the startup diagnostic in main.py.
        os.environ.setdefault("OKAHU_API_KEY", settings.okahu_api_key)
        os.environ["MONOCLE_EXPORTER"] = "okahu"

        _test_okahu_connectivity(settings.okahu_api_key)

        from monocle_apptrace.exporters.okahu.okahu_exporter import OkahuSpanExporter
        from monocle_apptrace.instrumentation.common import setup_monocle_telemetry
        from monocle_apptrace.instrumentation.metamodel.openai.methods import OPENAI_METHODS
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Wrap OkahuSpanExporter with our logging shim for visibility.
        logging_exporter = _LoggingOkahuExporter(OkahuSpanExporter())

        # workflow_name sets both service.name (OTel resource) and workflow.name
        # (span attribute). It must match the Okahu portal app name exactly so
        # that ingested spans route to the correct app.
        #
        # wrapper_methods=OPENAI_METHODS + union_with_default_methods=False ensures
        # only actual LLM/inference calls are traced — not FastAPI HTTP routes.
        setup_monocle_telemetry(
            workflow_name=settings.okahu_service_name,
            span_processors=[BatchSpanProcessor(logging_exporter)],
            wrapper_methods=OPENAI_METHODS,
            union_with_default_methods=False,
        )

        # Belt-and-suspenders: unwrap any FastAPI/Starlette route instrumentors
        # that may have been registered before this call.
        try:
            from opentelemetry.instrumentation.utils import unwrap as _unwrap
            import fastapi.routing as _fr
            _unwrap(_fr.APIRoute, "handle")
        except Exception:
            pass
        try:
            from opentelemetry.instrumentation.utils import unwrap as _unwrap
            import starlette.responses as _sr
            _unwrap(_sr.Response, "__call__")
        except Exception:
            pass

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

    def trace_retrieval_metrics(
        self,
        trace_id: str,
        metrics: dict[str, Any],
    ) -> None:
        """
        Emit retrieval quality metrics as an OTel span to Okahu Cloud.

        The span type is set to 'retrieval' so Okahu groups it with
        the existing retrieval spans in the trace timeline.

        Args:
            trace_id: Pipeline trace identifier.
            metrics:  Flat dict from RetrievalMetrics.to_dict().
        """
        if _otel_tracer is None:
            logger.debug("RETRIEVAL_METRICS trace=%s %s", trace_id, metrics)
            return

        span = _otel_tracer.start_span("retrieval_metrics")
        span.set_attribute("trace_id", trace_id)
        span.set_attribute("span.type", "retrieval")
        for k, v in metrics.items():
            span.set_attribute(k, str(v) if not isinstance(v, (int, float, bool)) else v)
        span.end()

        hit_rate = metrics.get("retrieval.hit_rate", 1.0)
        avg_score = metrics.get("retrieval.avg_score", 1.0)
        if hit_rate == 0.0 or avg_score < 0.35:
            logger.warning(
                "Low retrieval quality — trace=%s hit_rate=%s avg_score=%s bucket=%s. "
                "No document scored ≥0.5 cosine similarity. Consider seeding more relevant documents.",
                trace_id, hit_rate, avg_score, metrics.get("retrieval.top_score_bucket"),
            )
            if _otel_tracer is not None:
                alert_span = _otel_tracer.start_span("retrieval_quality_alert")
                alert_span.set_attribute("trace_id", trace_id)
                alert_span.set_attribute("span.type", "retrieval")
                alert_span.set_attribute("alert.type", "low_retrieval_quality")
                alert_span.set_attribute("retrieval.hit_rate", float(hit_rate))
                alert_span.set_attribute("retrieval.avg_score", float(avg_score))
                alert_span.set_attribute("retrieval.top_score_bucket",
                                         str(metrics.get("retrieval.top_score_bucket", "unknown")))
                alert_span.end()

        logger.info("Traced retrieval metrics — trace=%s doc_count=%s hit_rate=%s",
                    trace_id,
                    metrics.get("retrieval.doc_count"),
                    metrics.get("retrieval.hit_rate"))

    def trace_evaluation(
        self,
        trace_id: str,
        scores: dict[str, Any],
    ) -> None:
        """
        Emit RAG evaluation scores as OTel spans to Okahu Cloud.

        Each metric (faithfulness, context_relevancy, answer_relevancy) is
        emitted as its own 'evaluation' span so Okahu can display them
        individually in the trace detail view.

        Args:
            trace_id: Pipeline trace identifier.
            scores:   Flat dict from EvaluationResult.to_dict().
        """
        if _otel_tracer is None:
            logger.debug("EVALUATION trace=%s %s", trace_id, scores)
            return

        # Metric names that have a numeric score counterpart
        metric_keys = {
            "eval.faithfulness":       "faithfulness",
            "eval.context_relevancy":  "context_relevancy",
            "eval.answer_relevancy":   "answer_relevancy",
        }

        for attr_key, metric_name in metric_keys.items():
            score = scores.get(attr_key, -1.0)
            if float(score) < 0:
                continue  # metric was not computed — skip span

            reason_key = f"{attr_key}_reason"
            reason = scores.get(reason_key, "")

            span = _otel_tracer.start_span(f"eval.{metric_name}")
            span.set_attribute("trace_id",          trace_id)
            span.set_attribute("span.type",         "evaluation")
            span.set_attribute("eval.name",         metric_name)
            span.set_attribute("eval.score",        float(score))
            span.set_attribute("eval.passed",       float(score) >= 0.5)
            span.set_attribute("eval.threshold",    0.5)
            span.set_attribute("eval.reason",       str(reason))
            span.end()

        logger.info(
            "Traced evaluation — trace=%s faithfulness=%s context_rel=%s answer_rel=%s",
            trace_id,
            scores.get("eval.faithfulness"),
            scores.get("eval.context_relevancy"),
            scores.get("eval.answer_relevancy"),
        )

    def trace_ragas_evaluation(
        self,
        trace_id: str,
        result: "RagasEvaluationResult",
    ) -> None:
        """
        Export per-agent RAGAS scores to Okahu Cloud as individual OTel spans.

        Emits one span per agent stage (retrieval, initial, reflection, final)
        plus a summary span containing the reflection improvement delta.

        Args:
            trace_id: Pipeline trace identifier.
            result:   RagasEvaluationResult from ragas_evaluation_service.
        """
        if _otel_tracer is None:
            logger.debug("RAGAS_EVAL trace=%s %s", trace_id, result.to_dict())
            return

        stages = [
            ("ragas.retrieval", result.retrieval),
            ("ragas.initial",   result.initial),
            ("ragas.reflection", result.reflection),
            ("ragas.final",     result.final),
        ]

        for span_name, stage_score in stages:
            span = _otel_tracer.start_span(span_name)
            span.set_attribute("trace_id",   trace_id)
            span.set_attribute("span.type",  "evaluation")
            span.set_attribute("eval.framework", "ragas")
            span.set_attribute("eval.stage", stage_score.stage)
            span.set_attribute("ragas.faithfulness",      stage_score.faithfulness)
            span.set_attribute("ragas.answer_relevancy",  stage_score.answer_relevancy)
            span.set_attribute("ragas.context_precision", stage_score.context_precision)
            span.set_attribute("ragas.overall",           stage_score.overall)
            span.end()

        # Reflection improvement delta span
        delta = result.reflection_delta
        delta_span = _otel_tracer.start_span("ragas.reflection_delta")
        delta_span.set_attribute("trace_id",  trace_id)
        delta_span.set_attribute("span.type", "evaluation")
        delta_span.set_attribute("eval.framework", "ragas")
        for k, v in delta.items():
            if v != -999.0:
                delta_span.set_attribute(k, v)
                # Flag regressions
                if v < 0:
                    delta_span.set_attribute("ragas.regression_detected", True)
        delta_span.end()

        logger.info(
            "RAGAS traced — trace=%s | retrieval_cp=%.2f | "
            "initial(f=%.2f ar=%.2f) | reflection(f=%.2f ar=%.2f) | "
            "delta_f=%s delta_ar=%s | final_overall=%.2f",
            trace_id,
            result.retrieval.context_precision,
            result.initial.faithfulness, result.initial.answer_relevancy,
            result.reflection.faithfulness, result.reflection.answer_relevancy,
            delta.get("ragas.delta.faithfulness"),
            delta.get("ragas.delta.answer_relevancy"),
            result.final.overall,
        )

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
