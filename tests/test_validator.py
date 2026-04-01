"""Tests for the Validator Agent."""

import uuid

import pytest

from app.agents.validator_agent import ValidatorAgent
from app.models.schemas import DiagnosisEntry, DiagnosisStageResult


def _make_stage(stage: str, condition: str = "Pneumonia", confidence: str = "high") -> DiagnosisStageResult:
    doc_id = uuid.uuid4()
    return DiagnosisStageResult(
        stage=stage,
        diagnoses=[
            DiagnosisEntry(
                condition=condition,
                confidence=confidence,
                evidence_ids=[doc_id],
                reasoning="Test reasoning.",
            )
        ],
        reasoning="Test.",
        evidence_ids=[doc_id],
    )


def test_validator_passes_valid_output():
    case_id = uuid.uuid4()
    agent = ValidatorAgent()
    result = agent.run(
        case_id=case_id,
        initial=_make_stage("initial"),
        reflection=_make_stage("reflection"),
        final=_make_stage("final"),
    )
    assert result.valid is True
    assert result.data is not None
    assert result.data.disclaimer != ""
    assert "consult" in result.data.disclaimer.lower()


def test_validator_rejects_invalid_confidence():
    case_id = uuid.uuid4()
    agent = ValidatorAgent()
    result = agent.run(
        case_id=case_id,
        initial=_make_stage("initial", confidence="very_high"),  # invalid
        reflection=_make_stage("reflection"),
        final=_make_stage("final"),
    )
    # Validation errors recorded but response still built from sanitised data
    assert any("invalid confidence" in e for e in result.errors)


def test_validator_errors_on_empty_diagnoses():
    case_id = uuid.uuid4()
    agent = ValidatorAgent()

    empty_stage = DiagnosisStageResult(
        stage="final",
        diagnoses=[],
        reasoning="Nothing found.",
        evidence_ids=[],
    )
    result = agent.run(
        case_id=case_id,
        initial=_make_stage("initial"),
        reflection=_make_stage("reflection"),
        final=empty_stage,
    )
    assert any("final" in e for e in result.errors)


def test_validator_disclaimer_present():
    case_id = uuid.uuid4()
    agent = ValidatorAgent()
    result = agent.run(
        case_id=case_id,
        initial=_make_stage("initial"),
        reflection=_make_stage("reflection"),
        final=_make_stage("final"),
    )
    assert result.data.disclaimer == (
        "Not a medical diagnosis; consult a clinician before making any clinical decisions."
    )
