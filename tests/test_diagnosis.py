"""Tests for Diagnosis Agent and Reflection Agent."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.diagnosis_agent import DiagnosisAgent
from app.agents.reflection_agent import ReflectionAgent
from app.models.schemas import DiagnosisStageResult

MOCK_LLM_DIAGNOSIS = {
    "diagnoses": [
        {
            "condition": "Community-acquired pneumonia",
            "confidence": "high",
            "evidence_ids": [],
            "reasoning": "Fever and cough consistent with CAP.",
        }
    ],
    "reasoning": "Evidence supports pneumonia.",
    "needs_reretrival": False,
    "missing_evidence_hint": None,
}

MOCK_LLM_REFLECTION_RERETRIEVE = {
    "diagnoses": [
        {
            "condition": "Pulmonary embolism",
            "confidence": "medium",
            "evidence_ids": [],
            "reasoning": "Sudden dyspnoea warrants PE workup.",
        }
    ],
    "reasoning": "Initial diagnosis overlooked PE; more evidence needed.",
    "needs_reretrival": True,
    "missing_evidence_hint": "PE guidelines and D-dimer interpretation",
}


@pytest.mark.asyncio
async def test_diagnosis_agent_returns_stage_result(sample_case, sample_documents):
    agent = DiagnosisAgent()

    with patch(
        "app.agents.diagnosis_agent.llm_service.chat",
        new_callable=AsyncMock,
        return_value={"content": MOCK_LLM_DIAGNOSIS, "usage": {"total_tokens": 100}},
    ):
        result = await agent.run(case=sample_case, documents=sample_documents, stage="initial")

    assert isinstance(result, DiagnosisStageResult)
    assert result.stage == "initial"
    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].condition == "Community-acquired pneumonia"
    assert result.diagnoses[0].confidence == "high"
    assert result.needs_reretrival is False


@pytest.mark.asyncio
async def test_diagnosis_agent_sets_needs_reretrival(sample_case, sample_documents):
    mock_response = {**MOCK_LLM_DIAGNOSIS, "needs_reretrival": True, "missing_evidence_hint": "PE evidence"}
    agent = DiagnosisAgent()

    with patch(
        "app.agents.diagnosis_agent.llm_service.chat",
        new_callable=AsyncMock,
        return_value={"content": mock_response, "usage": {"total_tokens": 80}},
    ):
        result = await agent.run(case=sample_case, documents=sample_documents)

    assert result.needs_reretrival is True
    assert result.missing_evidence_hint == "PE evidence"


@pytest.mark.asyncio
async def test_reflection_agent_signals_reretrival(sample_case, sample_documents, sample_stage_result):
    agent = ReflectionAgent()

    with patch(
        "app.agents.reflection_agent.llm_service.chat",
        new_callable=AsyncMock,
        return_value={"content": MOCK_LLM_REFLECTION_RERETRIEVE, "usage": {"total_tokens": 120}},
    ):
        result = await agent.run(
            case=sample_case,
            initial_result=sample_stage_result,
            documents=sample_documents,
        )

    assert result.stage == "reflection"
    assert result.needs_reretrival is True
    assert "PE" in result.missing_evidence_hint


@pytest.mark.asyncio
async def test_reflection_agent_no_reretrival(sample_case, sample_documents, sample_stage_result):
    mock_response = {**MOCK_LLM_DIAGNOSIS, "needs_reretrival": False}
    agent = ReflectionAgent()

    with patch(
        "app.agents.reflection_agent.llm_service.chat",
        new_callable=AsyncMock,
        return_value={"content": mock_response, "usage": {"total_tokens": 90}},
    ):
        result = await agent.run(
            case=sample_case,
            initial_result=sample_stage_result,
            documents=sample_documents,
        )

    assert result.needs_reretrival is False
