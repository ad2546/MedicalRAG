"""Tests for llm_service.py — _GroqKeyRotator, LLMService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm_service import LLMService, _GroqKeyRotator


# ---------------------------------------------------------------------------
# _GroqKeyRotator
# ---------------------------------------------------------------------------

def _make_rotator(keys: list[str]) -> _GroqKeyRotator:
    """Build a rotator with pre-built mock AsyncOpenAI clients."""
    rotator = _GroqKeyRotator()
    clients = []
    for _ in keys:
        c = MagicMock()
        c.api_key = _
        c.chat = MagicMock()
        c.chat.completions = MagicMock()
        c.chat.completions.create = AsyncMock()
        clients.append(c)
    rotator._clients = clients
    return rotator


def _make_openai_response(content: str = '{"diagnoses": []}') -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    usage.total_tokens = 30
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestGroqKeyRotator:
    @pytest.mark.asyncio
    async def test_success_on_first_key(self):
        rotator = _make_rotator(["key1", "key2"])
        rotator._clients[0].chat.completions.create = AsyncMock(
            return_value=_make_openai_response()
        )
        result = await rotator.call(model="m", messages=[], temperature=0.1, max_tokens=100)
        assert result is not None
        rotator._clients[0].chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_rotates_to_next_key_on_429(self):
        rotator = _make_rotator(["key1", "key2"])
        rate_err = Exception("429 rate_limit_exceeded")
        success_response = _make_openai_response()
        rotator._clients[0].chat.completions.create = AsyncMock(side_effect=rate_err)
        rotator._clients[1].chat.completions.create = AsyncMock(return_value=success_response)

        result = await rotator.call(model="m", messages=[], temperature=0.1, max_tokens=100)
        assert result == success_response

    @pytest.mark.asyncio
    async def test_raises_when_all_keys_exhausted(self):
        rotator = _make_rotator(["key1", "key2"])
        rate_err = Exception("429 rate limit")
        for client in rotator._clients:
            client.chat.completions.create = AsyncMock(side_effect=rate_err)

        with pytest.raises(Exception, match="429"):
            await rotator.call(model="m", messages=[], temperature=0.1, max_tokens=100)

    @pytest.mark.asyncio
    async def test_non_429_propagates_immediately(self):
        rotator = _make_rotator(["key1", "key2"])
        server_err = Exception("500 internal server error")
        rotator._clients[0].chat.completions.create = AsyncMock(side_effect=server_err)

        with pytest.raises(Exception, match="500"):
            await rotator.call(model="m", messages=[], temperature=0.1, max_tokens=100)

        # key2 should never be tried
        rotator._clients[1].chat.completions.create.assert_not_called()

    def test_is_rate_limit_detects_429_string(self):
        assert _GroqKeyRotator._is_rate_limit(Exception("429 too many requests")) is True

    def test_is_rate_limit_detects_rate_limit_exceeded(self):
        assert _GroqKeyRotator._is_rate_limit(Exception("rate_limit_exceeded")) is True

    def test_is_rate_limit_false_for_other_errors(self):
        assert _GroqKeyRotator._is_rate_limit(Exception("connection refused")) is False

    def test_skips_truncated_keys(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key", "short")
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_2", "")
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_3", "")
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_4", "")
        rotator = _GroqKeyRotator()
        with pytest.raises(RuntimeError, match="No Groq API keys"):
            rotator._build_clients()

    def test_accepts_valid_length_key(self, monkeypatch):
        valid_key = "g" * 56
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key", valid_key)
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_2", "")
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_3", "")
        monkeypatch.setattr("app.services.llm_service.settings.groq_api_key_4", "")
        # AsyncOpenAI is imported locally inside _build_clients — patch at openai module level
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            rotator = _GroqKeyRotator()
            clients = rotator._build_clients()
        assert len(clients) == 1


# ---------------------------------------------------------------------------
# LLMService._extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_parses_plain_json(self):
        svc = LLMService()
        assert svc._extract_json('{"key": "val"}') == {"key": "val"}

    def test_strips_markdown_fences(self):
        svc = LLMService()
        raw = "```json\n{\"key\": \"val\"}\n```"
        assert svc._extract_json(raw) == {"key": "val"}

    def test_strips_json_fence_without_language(self):
        svc = LLMService()
        raw = "```\n{\"key\": \"val\"}\n```"
        assert svc._extract_json(raw) == {"key": "val"}

    def test_extracts_json_with_surrounding_text(self):
        svc = LLMService()
        raw = 'Here is the result: {"score": 0.9} done.'
        result = svc._extract_json(raw)
        assert result["score"] == 0.9

    def test_repairs_malformed_json(self):
        svc = LLMService()
        # Trailing comma — invalid JSON, but json_repair should handle
        raw = '{"key": "val",}'
        result = svc._extract_json(raw)
        assert result["key"] == "val"

    def test_raises_on_unrecoverable_json(self):
        svc = LLMService()
        with pytest.raises(Exception):
            svc._extract_json("not json at all #### $$$$")


# ---------------------------------------------------------------------------
# LLMService.chat / chat_messages
# ---------------------------------------------------------------------------

class TestLLMServiceChat:
    @pytest.mark.asyncio
    async def test_chat_calls_groq_and_returns_content(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()
        mock_result = {"text": '{"diagnoses": []}', "usage": {"total_tokens": 50}}

        with patch.object(svc, "_achat_groq", new_callable=AsyncMock, return_value=mock_result):
            result = await svc.chat(
                system_prompt="You are a doctor.",
                user_prompt="Patient has fever.",
                response_format="json_object",
            )

        assert result["content"] == {"diagnoses": []}
        assert result["usage"]["total_tokens"] == 50

    @pytest.mark.asyncio
    async def test_chat_text_format_returns_raw_text(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()
        mock_result = {"text": "plain text response", "usage": {}}

        with patch.object(svc, "_achat_groq", new_callable=AsyncMock, return_value=mock_result):
            result = await svc.chat(
                system_prompt="sys",
                user_prompt="usr",
                response_format="text",
            )

        assert result["content"] == "plain text response"

    @pytest.mark.asyncio
    async def test_chat_messages_returns_reply(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.llm_provider", "groq")
        svc = LLMService()
        mock_result = {"text": "AI reply", "usage": {"total_tokens": 30}}

        with patch.object(svc, "_achat_groq", new_callable=AsyncMock, return_value=mock_result):
            result = await svc.chat_messages(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["reply"] == "AI reply"
        assert result["usage"]["total_tokens"] == 30


# ---------------------------------------------------------------------------
# LLMService.build_case_context
# ---------------------------------------------------------------------------

class TestBuildCaseContext:
    def test_includes_symptoms(self):
        svc = LLMService()
        case = {"symptoms": ["fever", "cough"], "vitals": {}, "history": {}, "labs": {}}
        docs = []
        ctx = svc.build_case_context(case, docs)
        assert "fever" in ctx
        assert "cough" in ctx

    def test_truncates_doc_content(self):
        svc = LLMService()
        case = {"symptoms": [], "vitals": {}, "history": {}, "labs": {}}
        long_doc = {"id": "1", "content": "x" * 5000, "disease_category": "resp", "evidence_type": "t"}
        ctx = svc.build_case_context(case, [long_doc], max_doc_chars=100)
        # Content section should be truncated
        assert ctx.count("x") <= 100

    def test_limits_number_of_docs(self):
        svc = LLMService()
        case = {"symptoms": [], "vitals": {}, "history": {}, "labs": {}}
        docs = [
            {"id": str(i), "content": f"doc{i}", "disease_category": "c", "evidence_type": "t"}
            for i in range(5)
        ]
        ctx = svc.build_case_context(case, docs, max_docs=2)
        assert "doc0" in ctx
        assert "doc1" in ctx
        assert "doc4" not in ctx
