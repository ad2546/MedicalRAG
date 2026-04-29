"""Tests for evaluation_service.py — EvaluationService."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.evaluation_service import EvaluationResult, EvaluationService


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------

class TestEvaluationResult:
    def test_to_dict_has_all_keys(self):
        r = EvaluationResult(faithfulness=0.8, context_relevancy=0.7, answer_relevancy=0.9)
        d = r.to_dict()
        assert "eval.faithfulness" in d
        assert "eval.context_relevancy" in d
        assert "eval.answer_relevancy" in d
        assert "eval.faithfulness_reason" in d

    def test_overall_score_mean_of_positives(self):
        r = EvaluationResult(faithfulness=0.8, context_relevancy=0.6, answer_relevancy=1.0)
        assert abs(r.overall_score - (0.8 + 0.6 + 1.0) / 3) < 0.001

    def test_overall_score_ignores_negative_one(self):
        r = EvaluationResult(faithfulness=0.8, context_relevancy=-1.0, answer_relevancy=-1.0)
        assert r.overall_score == 0.8

    def test_overall_score_returns_minus_one_when_all_uncomputed(self):
        r = EvaluationResult()
        assert r.overall_score == -1.0

    def test_to_dict_rounds_scores(self):
        r = EvaluationResult(faithfulness=1 / 3)
        d = r.to_dict()
        assert d["eval.faithfulness"] == round(1 / 3, 4)


# ---------------------------------------------------------------------------
# EvaluationService._build_context_text
# ---------------------------------------------------------------------------

class TestBuildContextText:
    def test_concatenates_docs(self):
        svc = EvaluationService()
        result = svc._build_context_text(["doc one", "doc two"])
        assert "doc one" in result
        assert "doc two" in result

    def test_truncates_to_max_chars(self):
        svc = EvaluationService()
        big_doc = "x" * 5000
        result = svc._build_context_text([big_doc])
        assert len(result) <= 4100  # _MAX_CONTEXT_CHARS=4000 + label overhead

    def test_empty_contexts(self):
        svc = EvaluationService()
        assert svc._build_context_text([]) == ""


# ---------------------------------------------------------------------------
# EvaluationService.evaluate
# ---------------------------------------------------------------------------

def _mock_llm_response(score: float, reason: str = "test reason") -> MagicMock:
    """Build a mock OpenAI response object."""
    msg = MagicMock()
    msg.content = json.dumps({"score": score, "reason": reason})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


class TestEvaluationServiceEvaluate:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("app.services.evaluation_service.settings.groq_api_key", "")
        svc = EvaluationService()
        result = await svc.evaluate(["fever"], ["doc"], "answer")
        assert result.faithfulness == -1.0
        assert result.context_relevancy == -1.0
        assert result.answer_relevancy == -1.0

    @pytest.mark.asyncio
    async def test_evaluate_returns_scores_from_llm(self):
        svc = EvaluationService()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_llm_response(0.85, "grounded in evidence")
        )

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.evaluate(
                symptoms=["chest pain", "fever"],
                contexts=["Pneumonia context passage"],
                answer="Community-acquired pneumonia (high): fever and consolidation",
            )

        assert result.faithfulness == 0.85
        assert result.context_relevancy == 0.85
        assert result.answer_relevancy == 0.85

    @pytest.mark.asyncio
    async def test_evaluate_clamps_score_above_one(self):
        svc = EvaluationService()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_llm_response(1.5)
        )

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.evaluate(["fever"], ["ctx"], "answer")

        assert result.faithfulness <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_clamps_score_below_zero(self):
        svc = EvaluationService()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_llm_response(-0.5)
        )

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.evaluate(["fever"], ["ctx"], "answer")

        assert result.faithfulness >= 0.0

    @pytest.mark.asyncio
    async def test_evaluate_handles_llm_exception_gracefully(self):
        svc = EvaluationService()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("network error")
        )

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.evaluate(["fever"], ["ctx"], "answer")

        # All metrics degrade to -1.0 on error
        assert result.faithfulness == -1.0
        assert result.context_relevancy == -1.0
        assert result.answer_relevancy == -1.0

    @pytest.mark.asyncio
    async def test_evaluate_handles_malformed_json(self):
        svc = EvaluationService()
        mock_client = AsyncMock()
        bad_msg = MagicMock()
        bad_msg.content = "not json at all"
        bad_choice = MagicMock()
        bad_choice.message = bad_msg
        bad_response = MagicMock()
        bad_response.choices = [bad_choice]

        mock_client.chat.completions.create = AsyncMock(return_value=bad_response)

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.evaluate(["fever"], ["ctx"], "answer")

        assert result.faithfulness == -1.0
