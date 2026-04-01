"""Pipeline orchestrator — wires all four agents into the full RAG loop."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.diagnosis_agent import diagnosis_agent
from app.agents.reflection_agent import reflection_agent
from app.agents.retrieval_agent import retrieval_agent
from app.agents.validator_agent import validator_agent
from app.config import settings
from app.models.db_models import DiagnosisOutput
from app.models.schemas import CaseRequest, DiagnosisResponse, DiagnosisStageResult
from app.services.tracing_service import tracing_service

logger = logging.getLogger(__name__)


class DiagnosisPipeline:
    """
    Full pipeline:
      1. Hybrid retrieval (pgvector)
      2. Initial diagnosis (LLM)
      3. Reflection + optional re-retrieval (LLM)
      4. Guardrails validation
      5. Persist all stages and return structured response
    """

    async def run(self, db: AsyncSession, case: CaseRequest, case_id: uuid.UUID) -> DiagnosisResponse:
        trace_id = tracing_service.new_trace_id()
        logger.info("Pipeline started — case=%s trace=%s", case_id, trace_id)

        # ── Step 1: Initial retrieval ────────────────────────────────────────
        documents = await retrieval_agent.run(
            db=db,
            case_id=case_id,
            symptoms=case.symptoms,
            trace_id=trace_id,
        )

        # ── Step 2: Initial diagnosis ────────────────────────────────────────
        initial_result = await diagnosis_agent.run(
            case=case,
            documents=documents,
            stage="initial",
            trace_id=trace_id,
        )
        await self._persist_stage(db, case_id, initial_result)

        # ── Step 3: Reflection + conditional re-retrieval ────────────────────
        reflection_result = await self._run_reflection_loop(
            db, case, case_id, initial_result, documents, trace_id
        )
        await self._persist_stage(db, case_id, reflection_result)

        # ── Step 4: Final diagnosis = last reflection output ─────────────────
        final_result = DiagnosisStageResult(
            stage="final",
            diagnoses=reflection_result.diagnoses,
            reasoning=reflection_result.reasoning,
            evidence_ids=reflection_result.evidence_ids,
        )
        await self._persist_stage(db, case_id, final_result)

        # ── Step 5: Validate & return ────────────────────────────────────────
        validation = validator_agent.run(case_id, initial_result, reflection_result, final_result)

        tracing_service.trace_event(
            trace_id=trace_id,
            event_name="pipeline_complete",
            inputs={"case_id": str(case_id)},
            outputs={
                "valid": validation.valid,
                "errors": validation.errors,
                "final_conditions": [d.condition for d in final_result.diagnoses],
            },
        )

        if not validation.valid:
            logger.warning("Validation issues for case %s: %s", case_id, validation.errors)

        return validation.data

    async def _run_reflection_loop(
        self,
        db: AsyncSession,
        case: CaseRequest,
        case_id: uuid.UUID,
        initial: DiagnosisStageResult,
        documents: list,
        trace_id: str,
    ) -> DiagnosisStageResult:
        current_docs = documents
        current_result = initial

        for round_num in range(1, settings.max_reflection_rounds + 1):
            reflection = await reflection_agent.run(
                case=case,
                initial_result=current_result,
                documents=current_docs,
                trace_id=trace_id,
            )

            if not reflection.needs_reretrival:
                return reflection

            # Re-retrieval triggered
            logger.info(
                "Re-retrieval triggered (round %d) — hint: %s",
                round_num,
                reflection.missing_evidence_hint,
            )
            current_docs = await retrieval_agent.run(
                db=db,
                case_id=case_id,
                symptoms=case.symptoms,
                hint=reflection.missing_evidence_hint,
                trace_id=trace_id,
            )
            current_result = reflection

        return current_result

    async def _persist_stage(
        self,
        db: AsyncSession,
        case_id: uuid.UUID,
        stage_result: DiagnosisStageResult,
    ) -> None:
        output = DiagnosisOutput(
            case_id=case_id,
            stage=stage_result.stage,
            diagnosis={"diagnoses": [d.model_dump(mode="json") for d in stage_result.diagnoses]},
            reasoning=stage_result.reasoning,
        )
        db.add(output)
        await db.commit()


pipeline = DiagnosisPipeline()
