"""Extended tests for LLMService._achat_groq internals and cache integration."""

import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services.llm_service import LLMService


def _make_groq_response(content: str = '{"result": "ok"}',
                        prompt_tokens: int = 10,
                        completion_tokens: int = 20) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


class TestAchatGroq:
    @pytest.mark.asyncio
    async def test_returns_text_and_usage_on_success(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()
        mock_response = _make_groq_response('{"diagnoses": []}', 15, 25)

        with patch("app.services.llm_service._groq_rotator.call",
                   new_callable=AsyncMock,
                   return_value=mock_response):
            result = await svc._achat_groq(
                model_id="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.1,
                max_tokens=512,
            )

        assert result["text"] == '{"diagnoses": []}'
        assert result["usage"]["prompt_tokens"] == 15
        assert result["usage"]["completion_tokens"] == 25
        assert result["usage"]["total_tokens"] == 40

    @pytest.mark.asyncio
    async def test_caches_result_and_returns_on_hit(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()
        mock_response = _make_groq_response()

        with patch("app.services.llm_service._groq_rotator.call",
                   new_callable=AsyncMock,
                   return_value=mock_response) as mock_call:
            messages = [{"role": "user", "content": "cached question"}]
            # First call
            r1 = await svc._achat_groq("model", messages, 0.1, 100)
            # Second call — should use cache
            r2 = await svc._achat_groq("model", messages, 0.1, 100)

        # Rotator called only once; second served from cache
        mock_call.assert_called_once()
        assert r1["text"] == r2["text"]

    @pytest.mark.asyncio
    async def test_adds_response_format_when_json_object(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()

        captured_kwargs = {}

        async def capture_call(**kwargs):
            captured_kwargs.update(kwargs)
            return _make_groq_response()

        with patch("app.services.llm_service._groq_rotator.call", side_effect=capture_call):
            await svc._achat_groq(
                model_id="model",
                messages=[],
                temperature=0.0,
                max_tokens=100,
                response_format="json_object",
            )

        assert captured_kwargs.get("response_format") == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_no_response_format_kwarg_when_not_json(self, monkeypatch):
        svc = LLMService()
        captured_kwargs = {}

        async def capture_call(**kwargs):
            captured_kwargs.update(kwargs)
            return _make_groq_response()

        with patch("app.services.llm_service._groq_rotator.call", side_effect=capture_call):
            await svc._achat_groq("model", [], 0.0, 100, response_format=None)

        assert "response_format" not in captured_kwargs

    @pytest.mark.asyncio
    async def test_handles_none_usage_gracefully(self, monkeypatch):
        svc = LLMService()
        resp = _make_groq_response()
        resp.usage = None

        with patch("app.services.llm_service._groq_rotator.call",
                   new_callable=AsyncMock,
                   return_value=resp):
            result = await svc._achat_groq("model", [{"role": "user", "content": "x"}],
                                            0.0, 100)

        assert result["usage"]["total_tokens"] == 0


class TestAchatDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_groq_by_default(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()

        with patch.object(svc, "_achat_groq", new_callable=AsyncMock,
                          return_value={"text": "r", "usage": {}}) as mock_groq, \
             patch.object(svc, "_achat_oci", new_callable=AsyncMock) as mock_oci:
            await svc._achat("model", [], 0.1, 100)

        mock_groq.assert_called_once()
        mock_oci.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_to_oci_when_configured(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "oci")
        svc = LLMService()

        with patch.object(svc, "_achat_oci", new_callable=AsyncMock,
                          return_value={"text": "r", "usage": {}}) as mock_oci, \
             patch.object(svc, "_achat_groq", new_callable=AsyncMock) as mock_groq:
            await svc._achat("model", [], 0.1, 100)

        mock_oci.assert_called_once()
        mock_groq.assert_not_called()
