"""Extended router tests — diagnosis GET with data, cases POST error path,
cases streaming, cache stats endpoint."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import UserQuota, consume_user_request_quota, get_current_user
from app.database import get_db
from app.main import app

DISCLAIMER = "Not a medical diagnosis; consult a clinician before making any clinical decisions."


def _fake_quota(remaining: int = 5) -> UserQuota:
    return UserQuota(user_id=uuid.uuid4(), email="t@t.com", remaining_requests=remaining)


def _make_db_with_outputs(case_id: uuid.UUID):
    """DB mock that returns 3 DiagnosisOutput rows (initial, reflection, final)."""
    mock_db = AsyncMock()

    def _make_output(stage: str):
        row = MagicMock()
        row.stage = stage
        row.diagnosis = {
            "diagnoses": [
                {
                    "condition": "Pneumonia",
                    "confidence": "high",
                    "evidence_ids": [str(uuid.uuid4())],
                    "reasoning": "Fever consistent with CAP.",
                }
            ]
        }
        row.reasoning = "Evidence supports pneumonia."
        row.created_at = MagicMock()
        return row

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [
        _make_output("initial"),
        _make_output("reflection"),
        _make_output("final"),
    ]
    mock_db.execute = AsyncMock(return_value=result_mock)
    return mock_db


class TestDiagnosisRouter:
    @pytest.mark.asyncio
    async def test_get_diagnosis_found_returns_all_stages(self):
        case_id = uuid.uuid4()
        mock_db = _make_db_with_outputs(case_id)

        async def override_db():
            yield mock_db

        async def override_user():
            return MagicMock(id=uuid.uuid4(), email="t@t.com")

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/diagnosis/{case_id}")

        app.dependency_overrides.clear()
        assert response.status_code == 200
        data = response.json()
        assert len(data["initial_diagnosis"]) == 1
        assert len(data["reflection_diagnosis"]) == 1
        assert len(data["final_diagnosis"]) == 1
        assert data["initial_diagnosis"][0]["condition"] == "Pneumonia"
        assert "disclaimer" in data


class TestCasesRouter:
    @pytest.mark.asyncio
    async def test_pipeline_error_returns_500(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def override_db():
            yield mock_db

        async def override_quota():
            return _fake_quota()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[consume_user_request_quota] = override_quota

        with patch("app.routers.cases.pipeline.run",
                   new_callable=AsyncMock,
                   side_effect=Exception("pipeline exploded")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/case",
                    json={"symptoms": ["fever"], "vitals": {}, "history": {}, "labs": {}},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_streaming_endpoint_returns_200(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def override_db():
            yield mock_db

        async def override_quota():
            return _fake_quota()

        async def fake_streaming(*args, **kwargs):
            yield 'event: stage\ndata: {"name": "retrieval", "status": "done"}\n\n'
            yield 'event: complete\ndata: {"case_id": "00000000-0000-0000-0000-000000000000"}\n\n'

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[consume_user_request_quota] = override_quota

        with patch("app.routers.cases.pipeline.run_streaming", side_effect=fake_streaming):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/case/stream",
                    json={"symptoms": ["fever"], "vitals": {}, "history": {}, "labs": {}},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 200
        assert "retrieval" in response.text


class TestCacheStatsEndpoint:
    @pytest.mark.asyncio
    async def test_cache_stats_returns_dict(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/cache/stats")

        assert response.status_code == 200
        data = response.json()
        assert "caches" in data
        assert "global_rate_limit" in data


class TestDocumentsRouter:
    @pytest.mark.asyncio
    async def test_get_document_not_found_returns_404(self):
        doc_id = uuid.uuid4()
        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        async def override_db():
            yield mock_db

        async def override_user():
            return MagicMock(id=uuid.uuid4())

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/documents/{doc_id}")

        app.dependency_overrides.clear()
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_document_found_returns_citation(self):
        doc_id = uuid.uuid4()
        fake_doc = MagicMock()
        fake_doc.id = doc_id
        fake_doc.content = "Pneumonia evidence text"
        fake_doc.source = "Harrison's"
        fake_doc.disease_category = "respiratory"
        fake_doc.evidence_type = "textbook"

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = fake_doc
        mock_db.execute = AsyncMock(return_value=result_mock)

        async def override_db():
            yield mock_db

        async def override_user():
            return MagicMock(id=uuid.uuid4())

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/documents/{doc_id}")

        app.dependency_overrides.clear()
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Pneumonia evidence text"
        assert data["source"] == "Harrison's"


class TestRagasEvaluationServiceFallback:
    """Test the RagasEvaluationService when RAGAS is not available."""

    @pytest.mark.asyncio
    async def test_returns_default_result_when_ragas_unavailable(self, monkeypatch):
        import app.services.ragas_evaluation_service as rs
        monkeypatch.setattr(rs, "_RAGAS_AVAILABLE", False)

        from app.services.ragas_evaluation_service import RagasEvaluationService
        svc = RagasEvaluationService()

        result = await svc.evaluate_pipeline(
            symptoms=["fever"],
            contexts=["some context"],
            initial_answer="initial dx",
            reflection_answer="reflection dx",
            final_answer="final dx",
        )

        assert result.initial.faithfulness == -1.0
        assert result.final.overall == -1.0

    def test_reflection_delta_not_computed_when_scores_negative(self):
        from app.services.ragas_evaluation_service import RagasEvaluationResult
        result = RagasEvaluationResult()  # all scores -1.0
        delta = result.reflection_delta
        assert delta["ragas.delta.faithfulness"] == -999.0

    def test_reflection_delta_positive_improvement(self):
        from app.services.ragas_evaluation_service import AgentRagasScore, RagasEvaluationResult
        result = RagasEvaluationResult(
            initial=AgentRagasScore("initial", faithfulness=0.3, answer_relevancy=0.5),
            reflection=AgentRagasScore("reflection", faithfulness=0.9, answer_relevancy=0.8),
        )
        delta = result.reflection_delta
        assert abs(delta["ragas.delta.faithfulness"] - 0.6) < 0.001
        assert abs(delta["ragas.delta.answer_relevancy"] - 0.3) < 0.001

    def test_agent_ragas_score_overall_skips_negatives(self):
        from app.services.ragas_evaluation_service import AgentRagasScore
        score = AgentRagasScore("test", faithfulness=0.8, answer_relevancy=-1.0)
        assert score.overall == 0.8

    def test_ragas_evaluation_result_to_dict_has_all_keys(self):
        from app.services.ragas_evaluation_service import (
            AgentRagasScore,
            RagasEvaluationResult,
        )
        result = RagasEvaluationResult(
            retrieval=AgentRagasScore("retrieval", context_precision=0.9),
            initial=AgentRagasScore("initial", faithfulness=0.7),
            reflection=AgentRagasScore("reflection", faithfulness=0.9),
            final=AgentRagasScore("final", faithfulness=0.9, context_precision=1.0),
        )
        d = result.to_dict()
        assert "ragas.retrieval.context_precision" in d
        assert "ragas.delta.faithfulness" in d
        assert "ragas.final.overall" in d
