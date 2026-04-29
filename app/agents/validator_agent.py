"""Validator Agent — enforces guardrails and output structure using Pydantic + Guardrails AI."""

import logging
import uuid

from pydantic import ValidationError

from app.models.schemas import DiagnosisEntry, DiagnosisResponse, DiagnosisStageResult
from app.utils import DISCLAIMER

logger = logging.getLogger(__name__)
VALID_CONFIDENCE = {"low", "medium", "high"}


class ValidationResult:
    def __init__(self, valid: bool, errors: list[str], data: DiagnosisResponse | None = None) -> None:
        self.valid = valid
        self.errors = errors
        self.data = data


class ValidatorAgent:
    """Validates and sanitises pipeline output before returning to the caller."""

    def run(
        self,
        case_id: uuid.UUID,
        initial: DiagnosisStageResult,
        reflection: DiagnosisStageResult,
        final: DiagnosisStageResult,
    ) -> ValidationResult:
        errors: list[str] = []

        for stage_label, stage in [("initial", initial), ("reflection", reflection), ("final", final)]:
            stage_errors = self._validate_stage(stage_label, stage)
            errors.extend(stage_errors)

        if errors:
            logger.warning("Validation errors for case %s: %s", case_id, errors)

        # Sanitise: remove diagnoses with invalid confidence levels
        clean_initial = self._sanitise(initial)
        clean_reflection = self._sanitise(reflection)
        clean_final = self._sanitise(final)

        # Ensure every stage has at least one diagnosis after sanitisation
        if not clean_final:
            errors.append("final stage produced no valid diagnoses after sanitisation")

        try:
            response = DiagnosisResponse(
                case_id=case_id,
                initial_diagnosis=clean_initial,
                reflection_diagnosis=clean_reflection,
                final_diagnosis=clean_final,
                disclaimer=DISCLAIMER,
            )
        except ValidationError as exc:
            errors.append(f"Pydantic validation failed: {exc}")
            return ValidationResult(valid=False, errors=errors)

        return ValidationResult(valid=not errors, errors=errors, data=response)

    def _validate_stage(self, label: str, stage: DiagnosisStageResult) -> list[str]:
        errors = []
        if not stage.diagnoses:
            errors.append(f"{label}: no diagnoses returned")
        for d in stage.diagnoses:
            if d.confidence not in VALID_CONFIDENCE:
                errors.append(f"{label}: invalid confidence '{d.confidence}' for condition '{d.condition}'")
            if not d.condition.strip():
                errors.append(f"{label}: empty condition name")
        return errors

    def _sanitise(self, stage: DiagnosisStageResult) -> list[DiagnosisEntry]:
        return [d for d in stage.diagnoses if d.confidence in VALID_CONFIDENCE and d.condition.strip()]


validator_agent = ValidatorAgent()
