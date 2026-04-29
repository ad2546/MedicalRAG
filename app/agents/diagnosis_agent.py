"""Diagnosis Agent — generates an initial differential diagnosis via LLM."""

import logging
import uuid

from app.models.schemas import CaseRequest, DiagnosisEntry, DiagnosisStageResult, RetrievedDocument
from app.services.llm_service import llm_service
from app.services.tracing_service import tracing_service
from app.utils import is_valid_uuid

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a clinical decision-support assistant generating a differential diagnosis.
You must return ONLY a JSON object with this exact structure:
{
  "diagnoses": [
    {
      "condition": "<condition name>",
      "confidence": "<low|medium|high>",
      "evidence_ids": ["<doc_id_uuid>", ...],
      "reasoning": "<1-2 sentence reasoning>"
    }
  ],
  "reasoning": "<overall reasoning paragraph>",
  "needs_reretrival": false,
  "missing_evidence_hint": null
}

Rules:
- List 3–5 differential diagnoses ranked by likelihood.
- confidence must be exactly one of: low, medium, high.
- evidence_ids must reference only IDs from the provided documents.
- Do NOT fabricate medical claims not supported by the evidence.
- Set needs_reretrival=true and describe missing_evidence_hint if evidence is insufficient.
"""


class DiagnosisAgent:
    """Generates differential diagnoses given a patient case and retrieved evidence."""

    async def run(
        self,
        case: CaseRequest,
        documents: list[RetrievedDocument],
        stage: str = "initial",
        trace_id: str | None = None,
    ) -> DiagnosisStageResult:
        case_dict = case.model_dump()
        docs_dict = [d.model_dump() for d in documents]
        user_prompt = llm_service.build_case_context(
            {**case_dict, "symptoms": case.symptoms},
            docs_dict,
        )

        async with tracing_service.span(trace_id or "none", f"diagnosis_agent_{stage}"):
            result = await llm_service.chat(_SYSTEM_PROMPT, user_prompt)

        content = result["content"]
        tracing_service.trace_event(
            trace_id=trace_id or "none",
            event_name=f"diagnosis_{stage}",
            inputs={"symptoms": case.symptoms, "doc_count": len(documents)},
            outputs={"diagnoses": content.get("diagnoses", []), "usage": result["usage"]},
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
            stage=stage,
            diagnoses=diagnoses,
            reasoning=content.get("reasoning", ""),
            evidence_ids=[uuid.UUID(eid) for d in content.get("diagnoses", []) for eid in d.get("evidence_ids", []) if is_valid_uuid(eid)],
            needs_reretrival=content.get("needs_reretrival", False),
            missing_evidence_hint=content.get("missing_evidence_hint"),
        )


diagnosis_agent = DiagnosisAgent()
