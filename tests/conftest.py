"""Shared test fixtures."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.schemas import (
    CaseRequest,
    DiagnosisEntry,
    DiagnosisStageResult,
    HistorySchema,
    RetrievedDocument,
    VitalsSchema,
)


@pytest.fixture()
def sample_case() -> CaseRequest:
    return CaseRequest(
        case_id=uuid.uuid4(),
        symptoms=["chest pain", "shortness of breath", "fever"],
        vitals=VitalsSchema(bp="130/85", hr=102, temp=38.5),
        history=HistorySchema(smoker=True, prior_conditions=["hypertension"]),
        labs={"wbc": 14.2, "troponin": 0.08},
    )


@pytest.fixture()
def sample_documents() -> list[RetrievedDocument]:
    doc_id = uuid.uuid4()
    return [
        RetrievedDocument(
            id=doc_id,
            content="Pneumonia presents with fever, productive cough, and consolidation on X-ray.",
            source="Harrison's",
            disease_category="respiratory",
            evidence_type="textbook",
            score=0.92,
        )
    ]


@pytest.fixture()
def sample_stage_result(sample_documents) -> DiagnosisStageResult:
    doc_id = sample_documents[0].id
    return DiagnosisStageResult(
        stage="initial",
        diagnoses=[
            DiagnosisEntry(
                condition="Community-acquired pneumonia",
                confidence="high",
                evidence_ids=[doc_id],
                reasoning="Fever, cough, and consolidation are consistent with CAP.",
            )
        ],
        reasoning="Evidence strongly supports pneumonia.",
        evidence_ids=[doc_id],
        needs_reretrival=False,
    )
