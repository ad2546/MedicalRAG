"""Pipeline orchestrator — wires all four agents into the full RAG loop."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

# Keep strong references to background tasks so they are not GC'd before completion.
_background_tasks: set[asyncio.Task] = set()

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
try:
    from monocle_apptrace import amonocle_trace as _amonocle_trace
    _MONOCLE_AVAILABLE = True
except ImportError:
    _MONOCLE_AVAILABLE = False
    _amonocle_trace = None

from app.agents.diagnosis_agent import diagnosis_agent
from app.agents.reflection_agent import reflection_agent
from app.agents.retrieval_agent import retrieval_agent
from app.agents.validator_agent import validator_agent
from app.config import settings
from app.models.db_models import DiagnosisOutput, PipelineAudit
from app.models.schemas import CaseRequest, DiagnosisResponse, DiagnosisStageResult, RetrievedDocument
from app.services.cache_service import cache_service
from app.services.evaluation_service import evaluation_service
from app.services.ragas_evaluation_service import ragas_evaluation_service
from app.services.retrieval_metrics_service import retrieval_metrics_service
from app.services.tracing_service import tracing_service

logger = logging.getLogger(__name__)


from contextlib import asynccontextmanager

@asynccontextmanager
async def _agent_span(span_name: str, span_type: str = "inference"):
    """Wrap an agent call in a monocle span (visible in Okahu) if monocle is available."""
    if _MONOCLE_AVAILABLE:
        async with _amonocle_trace(
            span_name=span_name,
            attributes={"span.type": span_type},
        ):
            yield
    else:
        yield


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stage_data(result: DiagnosisStageResult) -> dict:
    return {
        "stage": result.stage,
        "diagnoses": [
            {
                "condition": d.condition,
                "confidence": d.confidence,
                "reasoning": d.reasoning,
                "evidence_ids": [str(e) for e in d.evidence_ids],
            }
            for d in result.diagnoses
        ],
        "reasoning": result.reasoning,
    }


class DiagnosisPipeline:
    """
    Full pipeline:
      1. Hybrid retrieval (pgvector)
      2. Initial diagnosis (LLM)
      3. Reflection + optional re-retrieval (LLM)
      4. Guardrails validation
      5. Persist all stages and return structured response
    """

    async def run(
        self,
        db: AsyncSession,
        case: CaseRequest,
        case_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        source: str = "api",
    ) -> DiagnosisResponse:
        trace_id = tracing_service.new_trace_id()
        started_at = datetime.utcnow()
        stage_timings: dict[str, float] = {}
        total_tokens: dict[str, int] = {"prompt": 0, "completion": 0}
        logger.info("Pipeline started — case=%s trace=%s", case_id, trace_id)

        # ── Case-level cache check ───────────────────────────────────────────
        cache_key = cache_service.case_key(case.symptoms, case.vitals.model_dump(), case.labs or {})
        cached = cache_service.get_case(cache_key)
        if cached is not None:
            logger.info("Cache HIT for case=%s", case_id)
            _t = asyncio.create_task(
                self._write_audit(case_id, user_id, trace_id, started_at,
                                  {}, {}, cache_hit=True, source=source)
            )
            _background_tasks.add(_t)
            _t.add_done_callback(_background_tasks.discard)
            return DiagnosisResponse(**cached)

        import time

        # ── Step 1: Initial retrieval ────────────────────────────────────────
        t0 = time.perf_counter()
        async with _agent_span("retrieval_agent", span_type="retrieval"):
            documents = await retrieval_agent.run(
                db=db,
                case_id=case_id,
                symptoms=case.symptoms,
                trace_id=trace_id,
            )
        stage_timings["retrieval"] = round(time.perf_counter() - t0, 3)

        # ── Step 2: Initial diagnosis ────────────────────────────────────────
        t0 = time.perf_counter()
        async with _agent_span("diagnosis_agent", span_type="inference"):
            initial_result = await diagnosis_agent.run(
                case=case,
                documents=documents,
                stage="initial",
                trace_id=trace_id,
            )
        stage_timings["diagnosis"] = round(time.perf_counter() - t0, 3)
        await self._persist_stage(db, case_id, initial_result)

        # ── Step 3: Reflection + conditional re-retrieval ────────────────────
        t0 = time.perf_counter()
        async with _agent_span("reflection_agent", span_type="inference"):
            reflection_result = await self._run_reflection_loop(
                db, case, case_id, initial_result, documents, trace_id
            )
        stage_timings["reflection"] = round(time.perf_counter() - t0, 3)
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

        response = validation.data

        # ── Step 6: Retrieval metrics (synchronous, lightweight) ─────────────
        ret_metrics = retrieval_metrics_service.compute(documents)
        tracing_service.trace_retrieval_metrics(trace_id, ret_metrics.to_dict())
        stage_timings["retrieval_metrics"] = 0.0   # negligible, included for audit

        # ── Step 7: Custom LLM-as-judge evaluation (async, non-blocking) ────────
        if settings.enable_evaluation:
            _t = asyncio.create_task(
                self._run_and_trace_evaluation(
                    trace_id=trace_id,
                    symptoms=case.symptoms,
                    documents=documents,
                    final_diagnoses=final_result.diagnoses,
                )
            )
            _background_tasks.add(_t)
            _t.add_done_callback(_background_tasks.discard)

        # ── Step 8: RAGAS per-agent evaluation (async, non-blocking) ─────────
        if settings.enable_ragas_evaluation:
            _t = asyncio.create_task(
                self._run_and_trace_ragas(
                    trace_id=trace_id,
                    symptoms=case.symptoms,
                    documents=documents,
                    initial_result=initial_result,
                    reflection_result=reflection_result,
                    final_result=final_result,
                )
            )
            _background_tasks.add(_t)
            _t.add_done_callback(_background_tasks.discard)

        # ── Store in case cache + write audit ────────────────────────────────
        cache_service.set_case(cache_key, response.model_dump(mode="json"))
        _t = asyncio.create_task(
            self._write_audit(case_id, user_id, trace_id, started_at,
                              stage_timings, total_tokens, source=source)
        )
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)

        return response

    async def run_streaming(
        self, db: AsyncSession, case: CaseRequest, case_id: uuid.UUID
    ) -> AsyncIterator[str]:
        """Same pipeline as run() but yields SSE frames after each stage."""
        trace_id = tracing_service.new_trace_id()
        logger.info("Pipeline (stream) started — case=%s trace=%s", case_id, trace_id)
        try:
            yield _sse("stage", {"name": "retrieval", "status": "running"})
            documents = await retrieval_agent.run(
                db=db, case_id=case_id, symptoms=case.symptoms, trace_id=trace_id
            )
            yield _sse("stage", {"name": "retrieval", "status": "done", "count": len(documents)})

            yield _sse("stage", {"name": "diagnosis", "status": "running"})
            initial_result = await diagnosis_agent.run(
                case=case, documents=documents, stage="initial", trace_id=trace_id
            )
            await self._persist_stage(db, case_id, initial_result)
            yield _sse("stage", {"name": "diagnosis", "status": "done", **_stage_data(initial_result)})

            yield _sse("stage", {"name": "reflection", "status": "running"})
            reflection_result = await self._run_reflection_loop(
                db, case, case_id, initial_result, documents, trace_id
            )
            await self._persist_stage(db, case_id, reflection_result)
            yield _sse("stage", {"name": "reflection", "status": "done", **_stage_data(reflection_result)})

            final_result = DiagnosisStageResult(
                stage="final",
                diagnoses=reflection_result.diagnoses,
                reasoning=reflection_result.reasoning,
                evidence_ids=reflection_result.evidence_ids,
            )
            await self._persist_stage(db, case_id, final_result)

            validation = validator_agent.run(case_id, initial_result, reflection_result, final_result)
            response = validation.data
            yield _sse("complete", {
                "case_id": str(response.case_id),
                "initial_diagnosis": [
                    {"condition": d.condition, "confidence": d.confidence, "reasoning": d.reasoning,
                     "evidence_ids": [str(e) for e in d.evidence_ids]}
                    for d in response.initial_diagnosis
                ],
                "reflection_diagnosis": [
                    {"condition": d.condition, "confidence": d.confidence, "reasoning": d.reasoning,
                     "evidence_ids": [str(e) for e in d.evidence_ids]}
                    for d in response.reflection_diagnosis
                ],
                "final_diagnosis": [
                    {"condition": d.condition, "confidence": d.confidence, "reasoning": d.reasoning,
                     "evidence_ids": [str(e) for e in d.evidence_ids]}
                    for d in response.final_diagnosis
                ],
                "disclaimer": response.disclaimer,
            })
        except Exception as exc:
            logger.exception("Streaming pipeline error for case %s", case_id)
            yield _sse("error", {"detail": str(exc)})

    async def _run_and_trace_evaluation(
        self,
        trace_id: str,
        symptoms: list[str],
        documents: list,
        final_diagnoses: list,
    ) -> None:
        """
        Run LLM-as-judge evaluation in the background and export scores to Okahu.

        This task runs after the HTTP response is already returned to the caller
        so it never adds latency to the user-facing pipeline.
        """
        try:
            contexts = [d.content for d in documents]
            answer_lines = [
                f"{d.condition} ({d.confidence}): {d.reasoning or ''}"
                for d in final_diagnoses
            ]
            answer = "\n".join(answer_lines)

            result = await evaluation_service.evaluate(
                symptoms=symptoms,
                contexts=contexts,
                answer=answer,
            )
            tracing_service.trace_evaluation(trace_id, result.to_dict())
        except Exception as exc:
            logger.warning("Background evaluation failed (non-fatal): %s", exc)

    async def _run_and_trace_ragas(
        self,
        trace_id: str,
        symptoms: list[str],
        documents: list,
        initial_result: DiagnosisStageResult,
        reflection_result: DiagnosisStageResult,
        final_result: DiagnosisStageResult,
    ) -> None:
        """
        Run RAGAS per-agent evaluation in the background and export scores to Okahu.

        Evaluates retrieval quality, initial diagnosis, reflection improvement,
        and final output independently. Computes reflection Δ delta for monitoring.
        """
        try:
            def _answer_text(stage: DiagnosisStageResult) -> str:
                lines = [
                    f"{d.condition} ({d.confidence}): {d.reasoning or ''}"
                    for d in stage.diagnoses
                ]
                return "\n".join(lines)

            contexts = [d.content for d in documents]
            result = await ragas_evaluation_service.evaluate_pipeline(
                symptoms=symptoms,
                contexts=contexts,
                initial_answer=_answer_text(initial_result),
                reflection_answer=_answer_text(reflection_result),
                final_answer=_answer_text(final_result),
            )
            tracing_service.trace_ragas_evaluation(trace_id, result)
        except Exception as exc:
            logger.warning("Background RAGAS evaluation failed (non-fatal): %s", exc)

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

    async def _write_audit(
        self,
        case_id: uuid.UUID,
        user_id: uuid.UUID | None,
        trace_id: str,
        started_at: datetime,
        stage_timings: dict,
        token_usage: dict,
        cache_hit: bool = False,
        error: str | None = None,
        source: str = "api",
    ) -> None:
        # Opens its own session — the request-scoped db is already closed by the
        # time this background task executes, so we must not reuse it.
        try:
            async with AsyncSessionLocal() as db:
                audit = PipelineAudit(
                    case_id=case_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    stage_timings=stage_timings,
                    token_usage=token_usage,
                    cache_hit=cache_hit,
                    error=error,
                    source=source,
                )
                db.add(audit)
                await db.commit()
        except Exception as exc:
            logger.warning("Audit write failed (non-fatal): %s", exc)


pipeline = DiagnosisPipeline()
