"""Extended tests for tracing_service — trace_retrieval_metrics, trace_evaluation,
trace_ragas_evaluation. Complements test_tracing_service.py."""

from unittest.mock import MagicMock, patch
import pytest


def _patch_tracer(monkeypatch):
    """Return (fake_tracer, fake_span) with monkeypatch applied."""
    import app.services.tracing_service as ts
    fake_span = MagicMock()
    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = fake_span
    monkeypatch.setattr(ts, "_otel_tracer", fake_tracer)
    return fake_tracer, fake_span


# ---------------------------------------------------------------------------
# trace_retrieval_metrics
# ---------------------------------------------------------------------------

class TestTraceRetrievalMetrics:
    def test_emits_span_with_metric_attributes(self, monkeypatch):
        fake_tracer, fake_span = _patch_tracer(monkeypatch)
        from app.services.tracing_service import tracing_service

        metrics = {
            "retrieval.doc_count": 5,
            "retrieval.avg_score": 0.82,
            "retrieval.hit_rate": 1.0,
            "retrieval.top_score_bucket": "excellent",
        }
        tracing_service.trace_retrieval_metrics("trace-1", metrics)

        fake_tracer.start_span.assert_called_with("retrieval_metrics")
        fake_span.end.assert_called()

    def test_no_alert_span_when_quality_is_good(self, monkeypatch):
        fake_tracer, fake_span = _patch_tracer(monkeypatch)
        from app.services.tracing_service import tracing_service

        metrics = {
            "retrieval.hit_rate": 1.0,
            "retrieval.avg_score": 0.9,
            "retrieval.top_score_bucket": "excellent",
        }
        tracing_service.trace_retrieval_metrics("trace-2", metrics)

        # Only the metrics span, no alert span
        assert fake_tracer.start_span.call_count == 1

    def test_alert_span_emitted_on_zero_hit_rate(self, monkeypatch):
        fake_tracer, _ = _patch_tracer(monkeypatch)
        # Each start_span call returns a fresh mock span
        fake_tracer.start_span.side_effect = [MagicMock(), MagicMock()]
        from app.services.tracing_service import tracing_service

        metrics = {
            "retrieval.hit_rate": 0.0,
            "retrieval.avg_score": 0.2,
            "retrieval.top_score_bucket": "poor",
        }
        tracing_service.trace_retrieval_metrics("trace-3", metrics)

        # Two spans: retrieval_metrics + retrieval_quality_alert
        assert fake_tracer.start_span.call_count == 2
        second_call_name = fake_tracer.start_span.call_args_list[1][0][0]
        assert "alert" in second_call_name

    def test_alert_on_low_avg_score(self, monkeypatch):
        fake_tracer, _ = _patch_tracer(monkeypatch)
        fake_tracer.start_span.side_effect = [MagicMock(), MagicMock()]
        from app.services.tracing_service import tracing_service

        metrics = {"retrieval.hit_rate": 1.0, "retrieval.avg_score": 0.30}
        tracing_service.trace_retrieval_metrics("trace-4", metrics)

        assert fake_tracer.start_span.call_count == 2

    def test_no_op_when_tracer_is_none(self, monkeypatch, caplog):
        import app.services.tracing_service as ts
        monkeypatch.setattr(ts, "_otel_tracer", None)
        from app.services.tracing_service import tracing_service
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.services.tracing_service"):
            tracing_service.trace_retrieval_metrics("t", {"retrieval.hit_rate": 1.0})

        assert "RETRIEVAL_METRICS" in caplog.text


# ---------------------------------------------------------------------------
# trace_evaluation
# ---------------------------------------------------------------------------

class TestTraceEvaluation:
    def test_emits_span_per_positive_metric(self, monkeypatch):
        fake_tracer, _ = _patch_tracer(monkeypatch)
        fake_tracer.start_span.side_effect = [MagicMock() for _ in range(5)]
        from app.services.tracing_service import tracing_service

        scores = {
            "eval.faithfulness": 0.8,
            "eval.context_relevancy": 0.7,
            "eval.answer_relevancy": 0.9,
            "eval.faithfulness_reason": "grounded",
            "eval.context_relevancy_reason": "relevant",
            "eval.answer_relevancy_reason": "complete",
        }
        tracing_service.trace_evaluation("trace-5", scores)

        # One span per metric with score >= 0: faithfulness, context_relevancy, answer_relevancy
        assert fake_tracer.start_span.call_count == 3

    def test_skips_span_for_uncomputed_metrics(self, monkeypatch):
        fake_tracer, fake_span = _patch_tracer(monkeypatch)
        from app.services.tracing_service import tracing_service

        # Only faithfulness computed; others -1.0 → skipped
        scores = {
            "eval.faithfulness": 0.8,
            "eval.context_relevancy": -1.0,
            "eval.answer_relevancy": -1.0,
        }
        tracing_service.trace_evaluation("trace-6", scores)

        assert fake_tracer.start_span.call_count == 1

    def test_no_op_when_tracer_is_none(self, monkeypatch, caplog):
        import app.services.tracing_service as ts
        monkeypatch.setattr(ts, "_otel_tracer", None)
        from app.services.tracing_service import tracing_service
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.services.tracing_service"):
            tracing_service.trace_evaluation("t", {"eval.faithfulness": 0.8})

        assert "EVALUATION" in caplog.text


# ---------------------------------------------------------------------------
# trace_ragas_evaluation
# ---------------------------------------------------------------------------

class TestTraceRagasEvaluation:
    def _make_ragas_result(self):
        from app.services.ragas_evaluation_service import (
            AgentRagasScore,
            RagasEvaluationResult,
        )
        return RagasEvaluationResult(
            retrieval=AgentRagasScore("retrieval", context_precision=0.9),
            initial=AgentRagasScore("initial", faithfulness=0.5, answer_relevancy=0.7),
            reflection=AgentRagasScore("reflection", faithfulness=0.9, answer_relevancy=0.8),
            final=AgentRagasScore("final", faithfulness=0.9, answer_relevancy=0.8, context_precision=1.0),
        )

    def test_emits_five_spans(self, monkeypatch):
        fake_tracer, _ = _patch_tracer(monkeypatch)
        fake_tracer.start_span.side_effect = [MagicMock() for _ in range(6)]
        from app.services.tracing_service import tracing_service

        result = self._make_ragas_result()
        tracing_service.trace_ragas_evaluation("trace-7", result)

        # 4 stage spans + 1 delta span = 5
        assert fake_tracer.start_span.call_count == 5

    def test_regression_flag_set_when_delta_negative(self, monkeypatch):
        fake_tracer, _ = _patch_tracer(monkeypatch)
        spans = [MagicMock() for _ in range(6)]
        fake_tracer.start_span.side_effect = spans
        from app.services.tracing_service import tracing_service
        from app.services.ragas_evaluation_service import AgentRagasScore, RagasEvaluationResult

        # Reflection faithfulness lower than initial → regression
        result = RagasEvaluationResult(
            retrieval=AgentRagasScore("retrieval"),
            initial=AgentRagasScore("initial", faithfulness=0.8),
            reflection=AgentRagasScore("reflection", faithfulness=0.3),  # regression
            final=AgentRagasScore("final"),
        )
        tracing_service.trace_ragas_evaluation("trace-8", result)

        # The delta span (last one) should have ragas.regression_detected=True set
        delta_span = spans[4]
        set_attr_calls = {c.args[0]: c.args[1] for c in delta_span.set_attribute.call_args_list}
        assert set_attr_calls.get("ragas.regression_detected") is True

    def test_no_op_when_tracer_is_none(self, monkeypatch, caplog):
        import app.services.tracing_service as ts
        monkeypatch.setattr(ts, "_otel_tracer", None)
        from app.services.tracing_service import tracing_service
        import logging

        result = self._make_ragas_result()
        with caplog.at_level(logging.DEBUG, logger="app.services.tracing_service"):
            tracing_service.trace_ragas_evaluation("t", result)

        assert "RAGAS_EVAL" in caplog.text
