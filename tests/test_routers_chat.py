"""Tests for /chat router."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import UserQuota, consume_user_request_quota
from app.database import get_db
from app.main import app
from app.models.db_models import Case, DiagnosisOutput


def _fake_case(case_id: uuid.UUID) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    case.symptoms = {"items": ["fever", "cough"]}
    case.vitals = {"hr": 90}
    case.history = {}
    case.labs = {}
    return case


def _fake_output(case_id: uuid.UUID, stage: str = "final") -> MagicMock:
    output = MagicMock(spec=DiagnosisOutput)
    output.case_id = case_id
    output.stage = stage
    output.diagnosis = {"diagnoses": [{"condition": "Pneumonia", "confidence": "high"}]}
    output.reasoning = "Evidence supports pneumonia."
    output.created_at = MagicMock()
    return output


class TestChatRouter:
    @pytest.mark.asyncio
    async def test_chat_returns_reply(self):
        case_id = uuid.uuid4()
        fake_case = _fake_case(case_id)
        fake_output = _fake_output(case_id)

        mock_db = AsyncMock()
        case_result = MagicMock()
        case_result.scalar_one_or_none.return_value = fake_case

        outputs_result = MagicMock()
        outputs_result.scalars.return_value.all.return_value = [fake_output]

        mock_db.execute = AsyncMock(side_effect=[case_result, outputs_result])

        async def override_db():
            yield mock_db

        async def override_quota():
            return UserQuota(user_id=uuid.uuid4(), email="t@t.com", remaining_requests=3)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[consume_user_request_quota] = override_quota

        with patch(
            "app.routers.chat.llm_service.chat_messages",
            new_callable=AsyncMock,
            return_value={"reply": "AI response", "usage": {"total_tokens": 50}},
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    f"/chat/{case_id}",
                    json={"messages": [{"role": "user", "content": "What is the diagnosis?"}]},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "AI response"
        assert data["remaining_requests"] == 3

    @pytest.mark.asyncio
    async def test_chat_case_not_found_returns_404(self):
        case_id = uuid.uuid4()
        mock_db = AsyncMock()
        case_result = MagicMock()
        case_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=case_result)

        async def override_db():
            yield mock_db

        async def override_quota():
            return UserQuota(user_id=uuid.uuid4(), email="t@t.com", remaining_requests=5)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[consume_user_request_quota] = override_quota

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/chat/{case_id}",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_chat_llm_error_returns_502(self):
        case_id = uuid.uuid4()
        fake_case = _fake_case(case_id)

        mock_db = AsyncMock()
        case_result = MagicMock()
        case_result.scalar_one_or_none.return_value = fake_case
        outputs_result = MagicMock()
        outputs_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[case_result, outputs_result])

        async def override_db():
            yield mock_db

        async def override_quota():
            return UserQuota(user_id=uuid.uuid4(), email="t@t.com", remaining_requests=5)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[consume_user_request_quota] = override_quota

        with patch(
            "app.routers.chat.llm_service.chat_messages",
            new_callable=AsyncMock,
            side_effect=Exception("Groq unavailable"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    f"/chat/{case_id}",
                    json={"messages": [{"role": "user", "content": "hello"}]},
                )

        app.dependency_overrides.clear()
        assert response.status_code == 502
