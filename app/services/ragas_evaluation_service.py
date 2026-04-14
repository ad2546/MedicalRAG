"""
RAGAS per-agent evaluation service.

Evaluates each pipeline stage independently using RAGAS metrics and reports
improvement/regression across the agent chain. All scores are exported to
Okahu Cloud as OTel spans via tracing_service.

Agent stages evaluated
──────────────────────
retrieval_agent   → context_precision
                    (are retrieved docs relevant to the clinical question?)

diagnosis_initial → faithfulness, answer_relevancy
                    (is initial diagnosis grounded in evidence + addresses question?)

reflection_agent  → faithfulness, answer_relevancy + Δ delta vs initial
                    (did self-critique improve grounding and relevance?)

final             → faithfulness, answer_relevancy, context_precision
                    (overall output quality across the full pipeline)

Usage
─────
All evaluation runs are fire-and-forget background tasks. Failures are
logged at WARNING level and never propagate to the caller.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

_RAGAS_AVAILABLE = False
_Faithfulness = None
_AnswerRelevancy = None
_ContextPrecision = None

try:
    # nest_asyncio.apply() is called at ragas.executor import time and fails on
    # uvloop (used by uvicorn/gunicorn). Neutralize it before RAGAS loads —
    # we call metric.single_turn_ascore() directly which is a native coroutine
    # and doesn't need nest_asyncio's nested-loop support.
    import nest_asyncio as _nest_asyncio
    _nest_asyncio.apply = lambda loop=None: None
except ImportError:
    pass

try:
    from ragas.metrics import Faithfulness, AnswerRelevancy, LLMContextPrecisionWithoutReference
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import BaseRagasEmbeddings
    from ragas.dataset_schema import SingleTurnSample
    from langchain_openai import ChatOpenAI
    _RAGAS_AVAILABLE = True
    _Faithfulness = Faithfulness
    _AnswerRelevancy = AnswerRelevancy
    _ContextPrecision = LLMContextPrecisionWithoutReference   # no ground truth needed
except ImportError as _e:
    logger.warning("RAGAS not available — install ragas + langchain-openai: %s", _e)


# ---------------------------------------------------------------------------
# Embedding wrapper — reuses existing sentence-transformers service
# ---------------------------------------------------------------------------

if _RAGAS_AVAILABLE:
    from ragas.embeddings import BaseRagasEmbeddings as _Base

    class _EmbeddingServiceWrapper(_Base):
        """Thin LangChain-compatible wrapper around the app's embedding_service."""

        def embed_query(self, text: str) -> list[float]:
            from app.services.embedding_service import embedding_service
            return embedding_service.embed(text)

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            from app.services.embedding_service import embedding_service
            return [embedding_service.embed(t) for t in texts]

        async def aembed_query(self, text: str) -> list[float]:
            from app.services.embedding_service import embedding_service
            return embedding_service.embed(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            from app.services.embedding_service import embedding_service
            return [embedding_service.embed(t) for t in texts]
else:
    _EmbeddingServiceWrapper = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentRagasScore:
    """RAGAS scores for a single pipeline stage."""
    stage: str
    faithfulness: float = -1.0          # -1 = not computed
    answer_relevancy: float = -1.0
    context_precision: float = -1.0

    def to_dict(self) -> dict:
        return {
            f"ragas.{self.stage}.faithfulness":       round(self.faithfulness, 4),
            f"ragas.{self.stage}.answer_relevancy":   round(self.answer_relevancy, 4),
            f"ragas.{self.stage}.context_precision":  round(self.context_precision, 4),
        }

    @property
    def overall(self) -> float:
        scores = [s for s in (self.faithfulness, self.answer_relevancy, self.context_precision) if s >= 0]
        return round(sum(scores) / len(scores), 4) if scores else -1.0


@dataclass
class RagasEvaluationResult:
    """Full per-agent RAGAS evaluation with reflection improvement delta."""
    retrieval: AgentRagasScore = field(default_factory=lambda: AgentRagasScore("retrieval"))
    initial: AgentRagasScore = field(default_factory=lambda: AgentRagasScore("initial"))
    reflection: AgentRagasScore = field(default_factory=lambda: AgentRagasScore("reflection"))
    final: AgentRagasScore = field(default_factory=lambda: AgentRagasScore("final"))

    @property
    def reflection_delta(self) -> dict[str, float]:
        """Signed improvement from initial → reflection (positive = better)."""
        def delta(a: float, b: float) -> float:
            return round(b - a, 4) if a >= 0 and b >= 0 else -999.0

        return {
            "ragas.delta.faithfulness":     delta(self.initial.faithfulness, self.reflection.faithfulness),
            "ragas.delta.answer_relevancy": delta(self.initial.answer_relevancy, self.reflection.answer_relevancy),
        }

    def to_dict(self) -> dict:
        result = {}
        result.update(self.retrieval.to_dict())
        result.update(self.initial.to_dict())
        result.update(self.reflection.to_dict())
        result.update(self.final.to_dict())
        result.update(self.reflection_delta)
        result["ragas.final.overall"] = self.final.overall
        return result


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RagasEvaluationService:
    """
    Evaluate all four pipeline agent stages using RAGAS metrics.

    LLM: Groq via OpenAI-compatible endpoint (same key as the main pipeline).
    Embeddings: Reuses the app's sentence-transformers embedding_service.
    """

    def __init__(self) -> None:
        self._llm: object | None = None
        self._embeddings: object | None = None
        self._metrics_initialized = False
        self._faithfulness: object | None = None
        self._answer_relevancy: object | None = None
        self._context_precision: object | None = None

    def _init_ragas(self) -> bool:
        """Lazy-initialize RAGAS metrics with Groq LLM + local embeddings."""
        if self._metrics_initialized:
            return bool(_RAGAS_AVAILABLE and self._faithfulness is not None)
        self._metrics_initialized = True

        if not _RAGAS_AVAILABLE:
            return False
        if not settings.groq_api_key:
            logger.warning("RAGAS evaluation skipped — GROQ_API_KEY not set")
            return False

        try:
            llm = LangchainLLMWrapper(self._make_groq_llm())
            embeddings = _EmbeddingServiceWrapper()

            self._faithfulness = _Faithfulness(llm=llm)
            self._answer_relevancy = _AnswerRelevancy(llm=llm, embeddings=embeddings)
            self._context_precision = _ContextPrecision(llm=llm)
            logger.info("RAGAS metrics initialised with Groq + sentence-transformers")
            return True
        except Exception as exc:
            logger.warning("RAGAS init failed: %s", exc)
            return False

    @staticmethod
    def _make_groq_llm(api_key: str | None = None) -> object:
        """Build a ChatOpenAI pointed at Groq. Uses the key rotator's current key."""
        from app.services.llm_service import _groq_rotator
        # Resolve the active key from the rotator's current index
        clients = _groq_rotator._clients_list()
        idx = _groq_rotator._current_idx % len(clients)
        # Extract the api_key from the active AsyncOpenAI client
        active_key = clients[idx].api_key or settings.groq_api_key
        return ChatOpenAI(
            model=settings.groq_model_gen,
            openai_api_key=active_key,
            openai_api_base="https://api.groq.com/openai/v1",
            temperature=0.0,
            max_tokens=2048,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate_pipeline(
        self,
        symptoms: list[str],
        contexts: list[str],
        initial_answer: str,
        reflection_answer: str,
        final_answer: str,
    ) -> RagasEvaluationResult:
        """
        Run per-agent RAGAS evaluation for the full pipeline.

        Args:
            symptoms:          Patient symptoms (forms the clinical question).
            contexts:          Retrieved document contents from retrieval_agent.
            initial_answer:    Diagnosis text from diagnosis_agent (initial stage).
            reflection_answer: Diagnosis text from reflection_agent.
            final_answer:      Final diagnosis text post-validator.

        Returns:
            RagasEvaluationResult with per-agent scores and reflection delta.
        """
        if not self._init_ragas():
            return RagasEvaluationResult()

        question = f"Patient presents with: {', '.join(symptoms)}. What is the differential diagnosis?"

        # Run all four stage evaluations concurrently
        retrieval_task, initial_task, reflection_task, final_task = await asyncio.gather(
            self._eval_retrieval(question, contexts, final_answer),
            self._eval_stage("initial", question, contexts, initial_answer),
            self._eval_stage("reflection", question, contexts, reflection_answer),
            self._eval_stage("final", question, contexts, final_answer),
            return_exceptions=True,
        )

        def _safe(result, stage: str) -> AgentRagasScore:
            if isinstance(result, Exception):
                logger.warning("RAGAS %s evaluation failed: %s", stage, result)
                return AgentRagasScore(stage)
            return result

        result = RagasEvaluationResult(
            retrieval=_safe(retrieval_task, "retrieval"),
            initial=_safe(initial_task, "initial"),
            reflection=_safe(reflection_task, "reflection"),
            final=_safe(final_task, "final"),
        )

        delta = result.reflection_delta
        logger.info(
            "RAGAS — retrieval_cp=%.2f | initial(f=%.2f ar=%.2f) | "
            "reflection(f=%.2f ar=%.2f) | delta(f=%+.2f ar=%+.2f) | final_overall=%.2f",
            result.retrieval.context_precision,
            result.initial.faithfulness, result.initial.answer_relevancy,
            result.reflection.faithfulness, result.reflection.answer_relevancy,
            delta.get("ragas.delta.faithfulness", -999),
            delta.get("ragas.delta.answer_relevancy", -999),
            result.final.overall,
        )
        return result

    # ------------------------------------------------------------------
    # Per-stage evaluators
    # ------------------------------------------------------------------

    async def _eval_retrieval(
        self, question: str, contexts: list[str], answer: str
    ) -> AgentRagasScore:
        """Retrieval stage: context_precision only (no answer quality needed here)."""
        score = AgentRagasScore("retrieval")
        sample = SingleTurnSample(
            user_input=question,
            retrieved_contexts=contexts,
            response=answer,
        )
        score.context_precision = await self._safe_score(self._context_precision, sample, "retrieval.context_precision")
        return score

    async def _eval_stage(
        self, stage: str, question: str, contexts: list[str], answer: str
    ) -> AgentRagasScore:
        """Evaluate faithfulness + answer_relevancy for a diagnosis stage."""
        score = AgentRagasScore(stage)
        sample = SingleTurnSample(
            user_input=question,
            retrieved_contexts=contexts,
            response=answer,
        )

        faithfulness_score, ar_score = await asyncio.gather(
            self._safe_score(self._faithfulness, sample, f"{stage}.faithfulness"),
            self._safe_score(self._answer_relevancy, sample, f"{stage}.answer_relevancy"),
            return_exceptions=True,
        )

        score.faithfulness = faithfulness_score if not isinstance(faithfulness_score, Exception) else -1.0
        score.answer_relevancy = ar_score if not isinstance(ar_score, Exception) else -1.0

        if stage == "final":
            cp_sample = SingleTurnSample(
                user_input=question,
                retrieved_contexts=contexts,
                response=answer,
            )
            score.context_precision = await self._safe_score(self._context_precision, cp_sample, "final.context_precision")

        return score

    async def _safe_score(self, metric, sample: object, label: str) -> float:
        """
        Call a RAGAS metric's async scorer.

        On 429 (Groq rate limit), rebuilds the LLM wrapper with the next rotated
        key and retries once. Returns -1.0 on any unrecoverable failure.
        """
        from app.services.llm_service import _groq_rotator

        for attempt in range(len(_groq_rotator._clients_list())):
            try:
                result = await metric.single_turn_ascore(sample)
                return round(float(result), 4)
            except Exception as exc:
                msg = str(exc).lower()
                is_rate_limit = "429" in msg or "rate_limit" in msg or "rate limit" in msg
                if is_rate_limit and attempt < len(_groq_rotator._clients_list()) - 1:
                    logger.warning(
                        "RAGAS %s hit rate limit — rotating Groq key (attempt %d)",
                        label, attempt + 1,
                    )
                    # Advance the rotator and rebuild metric LLM
                    _groq_rotator._current_idx = (
                        (_groq_rotator._current_idx + 1) % len(_groq_rotator._clients_list())
                    )
                    new_llm = LangchainLLMWrapper(self._make_groq_llm())
                    metric.llm = new_llm
                    continue
                logger.warning("RAGAS metric %s failed: %s", label, exc)
                return -1.0
        return -1.0


ragas_evaluation_service = RagasEvaluationService()
