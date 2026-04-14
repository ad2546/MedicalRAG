"""Tests for tracing_service.py

Three levels:
  1. Unit  — mock out all external deps (OkahuSpanExporter, requests, monocle)
  2. Smoke — verify spans are created and reach the exporter (no real network)
  3. Manual integration note at the bottom for live Okahu verification
"""

import logging
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_span(name="test.span", has_monocle=True, parent_span_id=None):
    """Build a minimal fake OTel span sufficient for _LoggingOkahuExporter."""
    span = MagicMock()
    span.name = name
    span.attributes = {"monocle_apptrace.version": "0.7.7"} if has_monocle else {}

    ctx = MagicMock()
    ctx.trace_id = 0xDEADBEEF00000001
    span.context = ctx

    if parent_span_id:
        parent = MagicMock()
        parent.span_id = parent_span_id
        span.parent = parent
    else:
        span.parent = None

    span.to_json.return_value = '{"resource": {"attributes": {}}, "attributes": {}, "events": []}'
    return span


# ---------------------------------------------------------------------------
# 1. _test_okahu_connectivity
# ---------------------------------------------------------------------------

class TestOkahuConnectivity:
    def test_logs_status_on_success(self, caplog):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.text = ""

        with patch("requests.post", return_value=mock_resp):
            # Import after patching so module-level side effects don't interfere
            from app.services.tracing_service import _test_okahu_connectivity
            with caplog.at_level(logging.INFO, logger="app.services.tracing_service"):
                _test_okahu_connectivity("fake-key")

        assert "204" in caplog.text

    def test_logs_warning_on_network_error(self, caplog):
        with patch("requests.post", side_effect=ConnectionError("timeout")):
            from app.services.tracing_service import _test_okahu_connectivity
            with caplog.at_level(logging.WARNING, logger="app.services.tracing_service"):
                _test_okahu_connectivity("fake-key")

        assert "connectivity check failed" in caplog.text.lower()

    def test_posts_to_correct_endpoint(self):
        mock_resp = MagicMock(status_code=204, text="")
        with patch("requests.post", return_value=mock_resp) as mock_post:
            from app.services.tracing_service import _test_okahu_connectivity
            _test_okahu_connectivity("my-api-key")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "ingest.okahu.co" in args[0]
        assert kwargs["headers"]["x-api-key"] == "my-api-key"
        assert kwargs["json"] == {"batch": []}


# ---------------------------------------------------------------------------
# 2. _LoggingOkahuExporter
# ---------------------------------------------------------------------------

class TestLoggingOkahuExporter:
    def _make_exporter(self):
        from app.services.tracing_service import _LoggingOkahuExporter
        inner = MagicMock()
        inner.export.return_value = "SUCCESS"
        return _LoggingOkahuExporter(inner), inner

    def test_delegates_to_inner_exporter(self):
        exporter, inner = self._make_exporter()
        spans = [_make_fake_span()]
        result = exporter.export(spans)
        inner.export.assert_called_once_with(spans)
        assert result == "SUCCESS"

    def test_counts_monocle_spans_correctly(self, caplog):
        exporter, _ = self._make_exporter()
        spans = [
            _make_fake_span("span.a", has_monocle=True),
            _make_fake_span("span.b", has_monocle=False),  # non-monocle
            _make_fake_span("span.c", has_monocle=True),
        ]
        with caplog.at_level(logging.INFO, logger="app.services.tracing_service"):
            exporter.export(spans)

        assert "total=3" in caplog.text
        assert "monocle=2" in caplog.text

    def test_logs_root_vs_child_spans(self, caplog):
        exporter, _ = self._make_exporter()
        root_span = _make_fake_span("workflow", parent_span_id=None)
        child_span = _make_fake_span("inference", parent_span_id=0xABCDEF12)

        with caplog.at_level(logging.INFO, logger="app.services.tracing_service"):
            exporter.export([root_span, child_span])

        # Simplified logging: monocle span names are now logged in span_names list
        assert "workflow" in caplog.text
        assert "inference" in caplog.text

    def test_force_flush_delegates(self):
        exporter, inner = self._make_exporter()
        inner.force_flush.return_value = True
        assert exporter.force_flush(5000) is True
        inner.force_flush.assert_called_once_with(5000)

    def test_shutdown_delegates(self):
        exporter, inner = self._make_exporter()
        exporter.shutdown()
        inner.shutdown.assert_called_once()

    def test_only_logs_detail_for_first_five_batches(self, caplog):
        exporter, _ = self._make_exporter()
        spans = [_make_fake_span()]

        with caplog.at_level(logging.INFO, logger="app.services.tracing_service"):
            for _ in range(7):
                caplog.clear()
                exporter.export(spans)

        # After 5 batches the detail block is skipped — only the summary line logged
        assert "span: name=" not in caplog.text


# ---------------------------------------------------------------------------
# 3. TracingService
# ---------------------------------------------------------------------------

class TestTracingService:
    @pytest.fixture(autouse=True)
    def _patch_tracer(self, monkeypatch):
        """Replace the global _otel_tracer with a mock so no real OTel needed."""
        import app.services.tracing_service as ts
        self.fake_span = MagicMock()
        self.fake_tracer = MagicMock()
        self.fake_tracer.start_span.return_value = self.fake_span
        monkeypatch.setattr(ts, "_otel_tracer", self.fake_tracer)

    def test_trace_event_creates_and_ends_span(self):
        from app.services.tracing_service import tracing_service
        tracing_service.trace_event(
            trace_id="t1",
            event_name="retrieval",
            inputs={"query": "chest pain"},
            outputs={"docs": "3"},
        )
        self.fake_tracer.start_span.assert_called_once_with("retrieval")
        self.fake_span.end.assert_called_once()

    def test_trace_event_sets_attributes(self):
        from app.services.tracing_service import tracing_service
        tracing_service.trace_event(
            trace_id="t1",
            event_name="diagnosis",
            inputs={"stage": "initial"},
            outputs={"condition": "pneumonia"},
            metadata={"model": "llama"},
        )
        calls = {c.args[0]: c.args[1] for c in self.fake_span.set_attribute.call_args_list}
        assert calls["input.stage"] == "initial"
        assert calls["output.condition"] == "pneumonia"
        assert calls["meta.model"] == "llama"

    @pytest.mark.asyncio
    async def test_span_context_manager_ends_span(self):
        from app.services.tracing_service import tracing_service
        async with tracing_service.span("t1", "my_step"):
            pass
        self.fake_span.end.assert_called_once()

    @pytest.mark.asyncio
    async def test_span_ends_span_even_on_exception(self):
        from app.services.tracing_service import tracing_service
        with pytest.raises(ValueError):
            async with tracing_service.span("t1", "failing_step"):
                raise ValueError("boom")
        self.fake_span.end.assert_called_once()

    @pytest.mark.asyncio
    async def test_span_sets_custom_attributes(self):
        from app.services.tracing_service import tracing_service
        async with tracing_service.span("t1", "step", attributes={"stage": "2"}):
            pass
        calls = {c.args[0]: c.args[1] for c in self.fake_span.set_attribute.call_args_list}
        assert calls["stage"] == "2"

    def test_new_trace_id_returns_unique_values(self):
        from app.services.tracing_service import tracing_service
        ids = {tracing_service.new_trace_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


# ---------------------------------------------------------------------------
# Manual integration test (run by hand, not in CI)
# ---------------------------------------------------------------------------
# To verify live Okahu connectivity:
#
#   OKAHU_API_KEY=<your-key> OKAHU_SERVICE_NAME=<your-app-id> python - <<'EOF'
#   import os
#   os.environ["OKAHU_API_KEY"] = os.environ["OKAHU_API_KEY"]
#   os.environ["OKAHU_SERVICE_NAME"] = os.environ["OKAHU_SERVICE_NAME"]
#
#   from app.services.tracing_service import _init_tracer, _otel_tracer
#   from opentelemetry.sdk.trace.export import BatchSpanProcessor
#
#   import time
#   with _otel_tracer.start_as_current_span("test.inference") as span:
#       span.set_attribute("monocle_apptrace.version", "0.7.7")
#       span.set_attribute("span.type", "inference")
#       span.set_attribute("entity.1.type", "inference.openai")
#       span.set_attribute("workflow.name", os.environ["OKAHU_SERVICE_NAME"])
#       time.sleep(0.1)
#
#   time.sleep(10)  # wait for BatchSpanProcessor to flush
#   print("Done — check portal.okahu.co")
#   EOF
