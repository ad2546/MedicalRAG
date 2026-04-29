"""Tests for /workflow router — Bearer auth, run, cache ops."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app


VALID_KEY = "test-workflow-secret"

CASE_PAYLOAD = {
    "symptoms": ["fever", "cough"],
    "vitals": {"hr": 90, "temp": 38.0},
    "history": {},
    "labs": {},
}


def _make_db():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    return mock_db


async def _override_db(mock_db):
    async def _dep():
        yield mock_db
    return _dep


class TestWorkflowAuth:
    @pytest.mark.asyncio
    async def test_missing_bearer_returns_403(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/workflow/run", json=CASE_PAYLOAD)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/workflow/run",
                json=CASE_PAYLOAD,
                headers={"Authorization": "Bearer wrong-key"},
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unconfigured_key_returns_503(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", "")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/workflow/run",
                json=CASE_PAYLOAD,
                headers={"Authorization": "Bearer anything"},
            )

        assert response.status_code == 503


class TestWorkflowRun:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_pipeline(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)
        case_id = uuid.uuid4()
        from app.models.schemas import DiagnosisEntry, DiagnosisResponse

        cached = DiagnosisResponse(
            case_id=case_id,
            initial_diagnosis=[],
            reflection_diagnosis=[],
            final_diagnosis=[],
            disclaimer="test",
        ).model_dump(mode="json")

        mock_db = _make_db()
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        with (
            patch("app.routers.workflow.cache_service.get_case", return_value=cached),
            patch("app.routers.workflow.cache_service.case_key", return_value="key"),
            patch("app.routers.workflow.cache_service.stats", return_value={}),
            patch("app.routers.workflow.pipeline.run", new_callable=AsyncMock) as mock_run,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/workflow/run",
                    json=CASE_PAYLOAD,
                    headers={"Authorization": f"Bearer {VALID_KEY}"},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["cache_hit"] is True
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_run_called_on_cache_miss(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)
        case_id = uuid.uuid4()
        from app.models.schemas import DiagnosisEntry, DiagnosisResponse

        pipeline_response = DiagnosisResponse(
            case_id=case_id,
            initial_diagnosis=[],
            reflection_diagnosis=[],
            final_diagnosis=[],
            disclaimer="test",
        )

        mock_db = _make_db()
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        with (
            patch("app.routers.workflow.cache_service.get_case", return_value=None),
            patch("app.routers.workflow.cache_service.case_key", return_value="key"),
            patch("app.routers.workflow.cache_service.stats", return_value={}),
            patch("app.routers.workflow.pipeline.run", new_callable=AsyncMock, return_value=pipeline_response),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/workflow/run",
                    json=CASE_PAYLOAD,
                    headers={"Authorization": f"Bearer {VALID_KEY}"},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False


class TestWorkflowCacheOps:
    @pytest.mark.asyncio
    async def test_cache_stats_returns_200(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)

        with patch("app.routers.workflow.cache_service.stats", return_value={"size": 0}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/workflow/cache/stats",
                    headers={"Authorization": f"Bearer {VALID_KEY}"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_cache_returns_204(self, monkeypatch):
        monkeypatch.setattr("app.routers.workflow.settings.workflow_api_key", VALID_KEY)
        from app.services.cache_service import cache_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(
                "/workflow/cache",
                headers={"Authorization": f"Bearer {VALID_KEY}"},
            )

        assert response.status_code == 204
