"""Reflection Agent — critiques the initial diagnosis and triggers re-retrieval if needed."""

import logging
import uuid

from app.models.schemas import CaseRequest, DiagnosisEntry, DiagnosisStageResult, RetrievedDocument
from app.services.llm_service import llm_service
from app.services.tracing_service import tracing_service
from app.utils import is_valid_uuid

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior clinical reviewer critiquing a differential diagnosis.
You must return ONLY a JSON object with this exact structure:
{
  "diagnoses": [
    {
      "condition": "<revised or confirmed condition>",
      "confidence": "<low|medium|high>",
      "evidence_ids": ["<doc_id_uuid>", ...],
      "reasoning": "<1-2 sentence reasoning>"
    }
  ],
  "reasoning": "<detailed critique and revision rationale>",
  "needs_reretrival": false,
  "missing_evidence_hint": null
}

Your job:
1. Critique each initial diagnosis for accuracy and evidence support.
2. Elevate conditions that are under-weighted; demote ones that are over-weighted.
3. If critical evidence is missing, set needs_reretrival=true and describe what to search for.
4. evidence_ids must reference only IDs present in the provided documents.
5. confidence must be exactly one of: low, medium, high.
"""


class ReflectionAgent:
    """Critiques an initial diagnosis and optionally signals need for re-retrieval."""

    async def run(
        self,
        case: CaseRequest,
        initial_result: DiagnosisStageResult,
        documents: list[RetrievedDocument],
        trace_id: str | None = None,
    ) -> DiagnosisStageResult:
        initial_summary = "\n".join(
            f"- {d.condition} [{d.confidence}]: {d.reasoning}"
            for d in initial_result.diagnoses
        )
        docs_dict = [doc.model_dump() for doc in documents]
        base_context = llm_service.build_case_context(
            {"symptoms": case.symptoms, "vitals": case.vitals.model_dump(), "history": case.history.model_dump(), "labs": case.labs},
            docs_dict,
        )
        user_prompt = (
            f"{base_context}\n\n"
            f"INITIAL DIAGNOSIS:\n{initial_summary}\n\n"
            f"INITIAL REASONING:\n{initial_result.reasoning}\n\n"
            "Please critique and revise the differential diagnosis."
        )

        async with tracing_service.span(trace_id or "none", "reflection_agent"):
            result = await llm_service.chat(_SYSTEM_PROMPT, user_prompt)

        content = result["content"]
        tracing_service.trace_event(
            trace_id=trace_id or "none",
            event_name="reflection",
            inputs={
                "initial_diagnoses": [d.condition for d in initial_result.diagnoses],
                "doc_count": len(documents),
            },
            outputs={
                "revised_diagnoses": content.get("diagnoses", []),
                "needs_reretrival": content.get("needs_reretrival"),
                "usage": result["usage"],
            },
        )

        diagnoses = [
            DiagnosisEntry(
                condition=d["condition"],
                confidence=d["confidence"],
                evidence_ids=[uuid.UUID(eid) for eid in d.get("evidence_ids", []) if is_valid_uuid(eid)],
                reasoning=d.get("reasoning"),
            )
            for d in content.get("diagnoses", [])
        ]

        return DiagnosisStageResult(
            stage="reflection",
            diagnoses=diagnoses,
            reasoning=content.get("reasoning", ""),
            evidence_ids=[uuid.UUID(eid) for d in content.get("diagnoses", []) for eid in d.get("evidence_ids", []) if is_valid_uuid(eid)],
            needs_reretrival=content.get("needs_reretrival", False),
            missing_evidence_hint=content.get("missing_evidence_hint"),
        )


reflection_agent = ReflectionAgent()
