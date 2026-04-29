"""Tests for the Retrieval Agent."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.retrieval_agent import RetrievalAgent
from app.models.schemas import RetrievedDocument


@pytest.mark.asyncio
async def test_retrieval_agent_returns_documents(sample_case):
    """RetrievalAgent.run should return a list of RetrievedDocument."""
    doc_id = uuid.uuid4()
    mock_docs = [
        RetrievedDocument(
            id=doc_id,
            content="Pneumonia evidence",
            source="test",
            disease_category="respiratory",
            evidence_type="guideline",
            score=0.9,
        )
    ]

    agent = RetrievalAgent()

    with (
        patch.object(agent, "_vector_search", new_callable=AsyncMock, return_value=mock_docs),
        patch.object(agent, "_log_retrieval", new_callable=AsyncMock),
    ):
        db_mock = AsyncMock()
        docs = await agent.run(db=db_mock, case_id=sample_case.case_id, symptoms=sample_case.symptoms)

    assert len(docs) == 1
    assert docs[0].id == doc_id
    assert docs[0].score == 0.9


@pytest.mark.asyncio
async def test_retrieval_agent_passes_hint(sample_case):
    """When a hint is provided it should be appended to the query."""
    from app.services.embedding_service import embedding_service

    with patch.object(embedding_service, "embed", return_value=[0.0] * 384), \
         patch.object(embedding_service, "build_query", wraps=embedding_service.build_query) as mock_build:

        agent = RetrievalAgent()
        db_mock = AsyncMock()

        with (
            patch.object(agent, "_vector_search", new_callable=AsyncMock, return_value=[]),
            patch.object(agent, "_log_retrieval", new_callable=AsyncMock),
        ):
            await agent.run(
                db=db_mock,
                case_id=sample_case.case_id,
                symptoms=sample_case.symptoms,
                hint="Look for PE guidelines",
            )

        call_args = mock_build.call_args
        assert "Look for PE guidelines" in call_args[1].get("hint", "") or \
               (call_args[0] and "Look for PE guidelines" in str(call_args))


@pytest.mark.asyncio
async def test_retrieval_agent_empty_results(sample_case):
    """Agent should handle zero results gracefully."""
    agent = RetrievalAgent()

    with (
        patch.object(agent, "_vector_search", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_log_retrieval", new_callable=AsyncMock),
    ):
        docs = await agent.run(db=AsyncMock(), case_id=sample_case.case_id, symptoms=sample_case.symptoms)

    assert docs == []
