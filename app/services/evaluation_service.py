"""
LLM-as-judge RAG evaluation service.

Runs three evaluation metrics per pipeline call using Groq (via the same
OpenAI-compatible client used by the rest of the application).  All three
metrics execute concurrently and the combined result is returned as a flat
dict suitable for OTel span attributes.

Metrics
───────
faithfulness        (0–1)  Is every claim in the answer supported by the
                           retrieved context? Penalises hallucinations.

context_relevancy   (0–1)  Are the retrieved documents relevant to the
                           patient's presenting symptoms / question?

answer_relevancy    (0–1)  Does the answer directly and completely address
                           the clinical question implied by the symptoms?

The service degrades gracefully:
  • If Groq is unavailable or the LLM returns malformed JSON, the metric
    is set to -1.0 so the caller can distinguish "not computed" from a
    genuine zero score.
  • All errors are logged at WARNING level and never propagate.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS  = 4000   # total chars across all context docs per prompt
_MAX_ANSWER_CHARS   = 1500   # answer snippet sent to evaluator
_EVAL_MODEL         = "llama-3.3-70b-versatile"  # same model, eval mode


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvaluationResult:
    faithfulness: float       = -1.0   # -1 = not computed
    context_relevancy: float  = -1.0
    answer_relevancy: float   = -1.0
    faithfulness_reason: str       = ""
    context_relevancy_reason: str  = ""
    answer_relevancy_reason: str   = ""

    def to_dict(self) -> dict:
        """Return flat dict for OTel span attributes."""
        return {
            "eval.faithfulness":             round(self.faithfulness, 4),
            "eval.context_relevancy":        round(self.context_relevancy, 4),
            "eval.answer_relevancy":         round(self.answer_relevancy, 4),
            "eval.faithfulness_reason":      self.faithfulness_reason,
            "eval.context_relevancy_reason": self.context_relevancy_reason,
            "eval.answer_relevancy_reason":  self.answer_relevancy_reason,
        }

    @property
    def overall_score(self) -> float:
        """Mean of available (≥0) scores. Returns -1 if none computed."""
        scores = [s for s in (self.faithfulness, self.context_relevancy, self.answer_relevancy) if s >= 0]
        return sum(scores) / len(scores) if scores else -1.0


# ---------------------------------------------------------------------------
# Evaluation service
# ---------------------------------------------------------------------------

class EvaluationService:
    """
    Evaluate RAG quality using the Groq LLM as a judge.

    Each metric is a single structured prompt that returns JSON:
        {"score": 0.85, "reason": "short explanation"}

    All three metrics run in parallel via asyncio.gather.
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI | None:
        if not settings.groq_api_key:
            return None
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        symptoms: list[str],
        contexts: list[str],
        answer: str,
    ) -> EvaluationResult:
        """
        Run all three evaluation metrics concurrently.

        Args:
            symptoms: Patient's symptom list (forms the clinical question).
            contexts: Retrieved document contents.
            answer:   Final diagnosis text from the pipeline.

        Returns:
            EvaluationResult with scores 0–1 (or -1 if not computed).
        """
        client = self._get_client()
        if client is None:
            logger.warning("Evaluation skipped — GROQ_API_KEY not set")
            return EvaluationResult()

        question         = f"Patient presents with: {', '.join(symptoms)}"
        context_text     = self._build_context_text(contexts)
        answer_truncated = answer[:_MAX_ANSWER_CHARS]

        faithfulness_task, context_task, answer_task = await asyncio.gather(
            self._score_faithfulness(client, context_text, answer_truncated),
            self._score_context_relevancy(client, question, context_text),
            self._score_answer_relevancy(client, question, answer_truncated),
            return_exceptions=True,
        )

        def _safe(result, default: float = -1.0) -> tuple[float, str]:
            if isinstance(result, Exception):
                logger.warning("Evaluation metric error: %s", result)
                return default, str(result)
            return result

        f_score, f_reason   = _safe(faithfulness_task)
        c_score, c_reason   = _safe(context_task)
        a_score, a_reason   = _safe(answer_task)

        result = EvaluationResult(
            faithfulness=f_score,
            context_relevancy=c_score,
            answer_relevancy=a_score,
            faithfulness_reason=f_reason,
            context_relevancy_reason=c_reason,
            answer_relevancy_reason=a_reason,
        )

        logger.info(
            "Evaluation — faithfulness=%.2f context_relevancy=%.2f answer_relevancy=%.2f overall=%.2f",
            result.faithfulness, result.context_relevancy, result.answer_relevancy,
            result.overall_score,
        )
        return result

    # ------------------------------------------------------------------
    # Individual metric prompts
    # ------------------------------------------------------------------

    async def _score_faithfulness(
        self, client: AsyncOpenAI, context_text: str, answer: str
    ) -> tuple[float, str]:
        """
        Faithfulness: the clinical reasoning must be consistent with and
        grounded in the retrieved context.  Clinical diagnoses are inferences,
        not direct quotes — score whether the reasoning aligns with the
        evidence, not whether every word appears verbatim.
        """
        system = (
            "You are a medical evidence evaluator. Your task is to assess "
            "whether a clinical differential diagnosis is grounded in the provided "
            "context passages. Faithful means the clinical reasoning is consistent "
            "with the evidence — conditions mentioned in the answer should be "
            "supported by related evidence in the context. Minor clinical inferences "
            "beyond the literal text are acceptable; fabricated drug names, invented "
            "statistics, or conditions completely absent from the context are not."
        )
        user = f"""CONTEXT PASSAGES:
{context_text}

DIFFERENTIAL DIAGNOSIS ANSWER:
{answer}

Rate faithfulness from 0.0 to 1.0:
  1.0 = all diagnoses are supported by related evidence in the context
  0.7 = most diagnoses are grounded; one minor unsupported inference
  0.5 = roughly half the diagnoses have context support
  0.2 = few diagnoses are grounded; mostly contradicts or ignores context
  0.0 = diagnoses are entirely fabricated or contradict the context

Respond ONLY with valid JSON (no markdown):
{{"score": <float 0-1>, "reason": "<one sentence explaining the score>"}}"""

        return await self._call_llm(client, system, user)

    async def _score_context_relevancy(
        self, client: AsyncOpenAI, question: str, context_text: str
    ) -> tuple[float, str]:
        """
        Context relevancy: are the retrieved documents useful for answering
        the clinical question?  Low score = retrieval failure.
        """
        system = (
            "You are a medical information retrieval evaluator. Your task is to assess "
            "whether the retrieved documents are relevant to answering the clinical question."
        )
        user = f"""CLINICAL QUESTION:
{question}

RETRIEVED DOCUMENTS:
{context_text}

Rate context relevancy from 0.0 to 1.0:
  1.0 = all documents are directly relevant to the clinical question
  0.5 = some documents are relevant; others are tangential
  0.0 = none of the documents help answer the question

Respond ONLY with valid JSON (no markdown):
{{"score": <float 0-1>, "reason": "<one sentence explaining the score>"}}"""

        return await self._call_llm(client, system, user)

    async def _score_answer_relevancy(
        self, client: AsyncOpenAI, question: str, answer: str
    ) -> tuple[float, str]:
        """
        Answer relevancy: does the answer directly and completely address the
        clinical question?  Vague or evasive answers score lower.
        """
        system = (
            "You are a clinical response evaluator. Your task is to assess whether "
            "a clinical answer directly and completely addresses the presented question."
        )
        user = f"""CLINICAL QUESTION:
{question}

ANSWER:
{answer}

Rate answer relevancy from 0.0 to 1.0:
  1.0 = the answer fully and specifically addresses the question
  0.5 = the answer is partially relevant but misses key aspects
  0.0 = the answer is irrelevant, evasive, or generic

Respond ONLY with valid JSON (no markdown):
{{"score": <float 0-1>, "reason": "<one sentence explaining the score>"}}"""

        return await self._call_llm(client, system, user)

    # ------------------------------------------------------------------
    # LLM call helper
    # ------------------------------------------------------------------

    async def _call_llm(
        self, client: AsyncOpenAI, system: str, user: str
    ) -> tuple[float, str]:
        """
        Call the Groq LLM and parse {"score": float, "reason": str} from
        the response.  Returns (-1.0, error_message) on failure.
        """
        try:
            response = await client.chat.completions.create(
                model=_EVAL_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=0.0,   # deterministic scoring
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
            score  = float(data.get("score", -1.0))
            reason = str(data.get("reason", ""))
            # Clamp to [0, 1]
            score = max(0.0, min(1.0, score))
            return score, reason
        except Exception as exc:
            logger.warning("LLM evaluation call failed: %s", exc)
            return -1.0, str(exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_context_text(self, contexts: list[str]) -> str:
        """
        Concatenate context docs into a single string, truncating so the
        total does not exceed _MAX_CONTEXT_CHARS.
        """
        parts: list[str] = []
        remaining = _MAX_CONTEXT_CHARS
        for i, ctx in enumerate(contexts, start=1):
            snippet = ctx[:remaining]
            parts.append(f"[Doc {i}] {snippet}")
            remaining -= len(snippet)
            if remaining <= 0:
                break
        return "\n\n".join(parts)


evaluation_service = EvaluationService()
