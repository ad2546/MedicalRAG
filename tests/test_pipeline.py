"""Tests for DiagnosisPipeline — run(), run_streaming(), reflection loop."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import (
    CaseRequest,
    DiagnosisEntry,
    DiagnosisResponse,
    DiagnosisStageResult,
    HistorySchema,
    RetrievedDocument,
    VitalsSchema,
)
from app.pipeline import DiagnosisPipeline


DISCLAIMER = "Not a medical diagnosis; consult a clinician before making any clinical decisions."


def _make_case(case_id: uuid.UUID | None = None) -> CaseRequest:
    return CaseRequest(
        case_id=case_id or uuid.uuid4(),
        symptoms=["chest pain", "fever"],
        vitals=VitalsSchema(bp="120/80", hr=90, temp=38.0),
        history=HistorySchema(),
        labs={"wbc": 12.0},
    )


def _make_doc() -> RetrievedDocument:
    return RetrievedDocument(
        id=uuid.uuid4(),
        content="Evidence content",
        source="test",
        disease_category="cardiology",
        evidence_type="guideline",
        score=0.88,
    )


def _make_stage(stage: str, needs_reretrival: bool = False) -> DiagnosisStageResult:
    return DiagnosisStageResult(
        stage=stage,
        diagnoses=[
            DiagnosisEntry(
                condition="Pneumonia",
                confidence="high",
                evidence_ids=[uuid.uuid4()],
                reasoning="Fever consistent with pneumonia.",
            )
        ],
        reasoning="Evidence supports diagnosis.",
        evidence_ids=[uuid.uuid4()],
        needs_reretrival=needs_reretrival,
        missing_evidence_hint="More respiratory evidence" if needs_reretrival else None,
    )


def _make_diagnosis_response(case_id: uuid.UUID) -> DiagnosisResponse:
    entry = DiagnosisEntry(
        condition="Pneumonia", confidence="high", evidence_ids=[], reasoning="ok"
    )
    return DiagnosisResponse(
        case_id=case_id,
        initial_diagnosis=[entry],
        reflection_diagnosis=[entry],
        final_diagnosis=[entry],
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Cache hit path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_returns_cached_response_immediately():
    pipeline = DiagnosisPipeline()
    case = _make_case()
    case_id = case.case_id
    cached = _make_diagnosis_response(case_id).model_dump(mode="json")

    mock_db = AsyncMock()

    with (
        patch("app.pipeline.cache_service.get_case", return_value=cached),
        patch("app.pipeline.cache_service.case_key", return_value="key123"),
        patch("app.pipeline.retrieval_agent.run", new_callable=AsyncMock) as mock_ret,
        patch("app.pipeline.DiagnosisPipeline._write_audit", new_callable=AsyncMock),
    ):
        result = await pipeline.run(db=mock_db, case=case, case_id=case_id)

    # retrieval never called on cache hit
    mock_ret.assert_not_called()
    assert result.case_id == case_id


# ---------------------------------------------------------------------------
# Full pipeline path (no re-retrieval)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_full_pipeline_no_reretrival():
    pipeline = DiagnosisPipeline()
    case = _make_case()
    case_id = case.case_id
    docs = [_make_doc()]
    initial = _make_stage("initial")
    reflection = _make_stage("reflection", needs_reretrival=False)

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with (
        patch("app.pipeline.cache_service.get_case", return_value=None),
        patch("app.pipeline.cache_service.case_key", return_value="key123"),
        patch("app.pipeline.cache_service.set_case"),
        patch("app.pipeline.retrieval_agent.run", new_callable=AsyncMock, return_value=docs),
        patch("app.pipeline.diagnosis_agent.run", new_callable=AsyncMock, return_value=initial),
        patch("app.pipeline.reflection_agent.run", new_callable=AsyncMock, return_value=reflection),
        patch("app.pipeline.validator_agent.run") as mock_validator,
        patch("app.pipeline.retrieval_metrics_service.compute") as mock_metrics,
        patch("app.pipeline.tracing_service.trace_event"),
        patch("app.pipeline.tracing_service.trace_retrieval_metrics"),
        patch("app.pipeline.tracing_service.new_trace_id", return_value="trace-123"),
        patch("app.pipeline.DiagnosisPipeline._write_audit", new_callable=AsyncMock),
        patch("app.pipeline.settings.enable_evaluation", False),
        patch("app.pipeline.settings.enable_ragas_evaluation", False),
    ):
        mock_metrics.return_value = MagicMock(to_dict=lambda: {})
        expected_response = _make_diagnosis_response(case_id)
        mock_validator.return_value = MagicMock(valid=True, errors=[], data=expected_response)

        result = await pipeline.run(db=mock_db, case=case, case_id=case_id)

    assert result.case_id == case_id
    assert len(result.final_diagnosis) == 1


# ---------------------------------------------------------------------------
# Re-retrieval triggered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_triggers_reretrival_when_reflection_requests_it():
    pipeline = DiagnosisPipeline()
    case = _make_case()
    case_id = case.case_id
    docs = [_make_doc()]
    initial = _make_stage("initial")
    reflection_needs_more = _make_stage("reflection", needs_reretrival=True)
    reflection_final = _make_stage("reflection", needs_reretrival=False)

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    retrieval_call_count = 0

    async def fake_retrieval(**kwargs):
        nonlocal retrieval_call_count
        retrieval_call_count += 1
        return docs

    reflection_responses = [reflection_needs_more, reflection_final]
    reflection_call_count = 0

    async def fake_reflection(**kwargs):
        nonlocal reflection_call_count
        resp = reflection_responses[min(reflection_call_count, len(reflection_responses) - 1)]
        reflection_call_count += 1
        return resp

    with (
        patch("app.pipeline.cache_service.get_case", return_value=None),
        patch("app.pipeline.cache_service.case_key", return_value="key123"),
        patch("app.pipeline.cache_service.set_case"),
        patch("app.pipeline.retrieval_agent.run", side_effect=fake_retrieval),
        patch("app.pipeline.diagnosis_agent.run", new_callable=AsyncMock, return_value=initial),
        patch("app.pipeline.reflection_agent.run", side_effect=fake_reflection),
        patch("app.pipeline.validator_agent.run") as mock_validator,
        patch("app.pipeline.retrieval_metrics_service.compute") as mock_metrics,
        patch("app.pipeline.tracing_service.trace_event"),
        patch("app.pipeline.tracing_service.trace_retrieval_metrics"),
        patch("app.pipeline.tracing_service.new_trace_id", return_value="trace-456"),
        patch("app.pipeline.DiagnosisPipeline._write_audit", new_callable=AsyncMock),
        patch("app.pipeline.settings.enable_evaluation", False),
        patch("app.pipeline.settings.enable_ragas_evaluation", False),
        patch("app.pipeline.settings.max_reflection_rounds", 1),
    ):
        mock_metrics.return_value = MagicMock(to_dict=lambda: {})
        expected_response = _make_diagnosis_response(case_id)
        mock_validator.return_value = MagicMock(valid=True, errors=[], data=expected_response)

        await pipeline.run(db=mock_db, case=case, case_id=case_id)

    # Retrieval called at least twice (initial + re-retrieval)
    assert retrieval_call_count >= 2


# ---------------------------------------------------------------------------
# _persist_stage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_stage_adds_and_commits():
    pipeline = DiagnosisPipeline()
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    stage = _make_stage("initial")

    await pipeline._persist_stage(mock_db, uuid.uuid4(), stage)

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# run_streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_streaming_yields_sse_events():
    pipeline = DiagnosisPipeline()
    case = _make_case()
    case_id = case.case_id
    docs = [_make_doc()]
    initial = _make_stage("initial")
    reflection = _make_stage("reflection")

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with (
        patch("app.pipeline.retrieval_agent.run", new_callable=AsyncMock, return_value=docs),
        patch("app.pipeline.diagnosis_agent.run", new_callable=AsyncMock, return_value=initial),
        patch("app.pipeline.reflection_agent.run", new_callable=AsyncMock, return_value=reflection),
        patch("app.pipeline.validator_agent.run") as mock_validator,
        patch("app.pipeline.tracing_service.new_trace_id", return_value="trace-stream"),
        patch("app.pipeline.DiagnosisPipeline._persist_stage", new_callable=AsyncMock),
    ):
        expected_response = _make_diagnosis_response(case_id)
        mock_validator.return_value = MagicMock(valid=True, errors=[], data=expected_response)

        events = []
        async for chunk in pipeline.run_streaming(db=mock_db, case=case, case_id=case_id):
            events.append(chunk)

    # Should have retrieval, diagnosis, reflection stage events + complete event
    full_text = "".join(events)
    assert "retrieval" in full_text
    assert "diagnosis" in full_text
    assert "reflection" in full_text
    assert "complete" in full_text


@pytest.mark.asyncio
async def test_run_streaming_yields_error_event_on_exception():
    pipeline = DiagnosisPipeline()
    case = _make_case()
    mock_db = AsyncMock()

    with (
        patch("app.pipeline.retrieval_agent.run", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        patch("app.pipeline.tracing_service.new_trace_id", return_value="trace-err"),
    ):
        events = []
        async for chunk in pipeline.run_streaming(db=mock_db, case=case, case_id=case.case_id):
            events.append(chunk)

    assert any("error" in e for e in events)
