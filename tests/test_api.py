"""Integration tests for the FastAPI endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import UserQuota, consume_user_request_quota, get_current_user
from app.database import get_db
from app.main import app
from app.models.schemas import (
    DiagnosisEntry,
    DiagnosisResponse,
)

DISCLAIMER = "Not a medical diagnosis; consult a clinician before making any clinical decisions."

MOCK_PIPELINE_RESPONSE = DiagnosisResponse(
    case_id=uuid.uuid4(),
    initial_diagnosis=[
        DiagnosisEntry(condition="Pneumonia", confidence="high", evidence_ids=[], reasoning="Fever and cough.")
    ],
    reflection_diagnosis=[
        DiagnosisEntry(condition="Pneumonia", confidence="high", evidence_ids=[], reasoning="Confirmed.")
    ],
    final_diagnosis=[
        DiagnosisEntry(condition="Pneumonia", confidence="high", evidence_ids=[], reasoning="Confirmed.")
    ],
    disclaimer=DISCLAIMER,
)


@pytest.fixture()
def case_payload():
    return {
        "symptoms": ["chest pain", "fever", "cough"],
        "vitals": {"bp": "120/80", "hr": 95, "temp": 38.2},
        "history": {"smoker": False, "prior_conditions": []},
        "labs": {"wbc": 12.5},
    }


@pytest.mark.asyncio
async def test_post_case_returns_diagnosis(case_payload):
    async def fake_consume_quota():
        return UserQuota(user_id=uuid.uuid4(), email="test@example.com", remaining_requests=4)

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    with patch("app.routers.cases.pipeline.run", new_callable=AsyncMock, return_value=MOCK_PIPELINE_RESPONSE):
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[consume_user_request_quota] = fake_consume_quota

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/case", json=case_payload)

    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert "final_diagnosis" in data
    assert "disclaimer" in data
    assert data["remaining_requests"] == 4


@pytest.mark.asyncio
async def test_get_diagnosis_not_found():
    fake_id = uuid.uuid4()
    mock_db = _empty_db()

    async def override_get_db():
        yield mock_db

    async def override_current_user():
        return MagicMock(id=uuid.uuid4(), email="test@example.com")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/diagnosis/{fake_id}")

    app.dependency_overrides.clear()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _empty_db():
    mock_db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result_mock)
    return mock_db
