---
tags: [architecture, services, infrastructure]
---

# Services Layer

> Singleton services injected into agents. No service creates its own dependencies.

---

## Service Map

```
llm_service          → Groq / OCI inference + JSON repair
embedding_service    → sentence-transformers (local, no API)
cache_service        → LRU + rate limiter
tracing_service      → OTel → Okahu Cloud
evaluation_service   → LLM-as-judge (faithfulness, context_rel, answer_rel)
retrieval_metrics    → hit rate, avg score, latency
ragas_evaluation     → RAGAS per-agent scores (background)
```

---

## LLM Service

**File**: `app/services/llm_service.py`

Supports two providers via `LLM_PROVIDER` env var:
- `groq` (default) — OpenAI-compatible, fast, free tier
- `oci` — OCI Generative AI native SDK

**Key feature**: [[fixes/Groq Key Rotation|4-key rotation]] — cycles through GROQ_API_KEY → _2 → _3 → _4 on 429.

```python
_groq_rotator = _GroqKeyRotator()   # module-level singleton
response = await _groq_rotator.call(**kwargs)
```

**JSON repair**: If the LLM returns malformed JSON, `json_repair` attempts to fix it before raising.

---

## Tracing Service

**File**: `app/services/tracing_service.py`

Initializes monocle at import time (before any LLM client is built):

```python
setup_monocle_telemetry(
    workflow_name="medicalChatbot",
    wrapper_methods=OPENAI_METHODS,
)
```

Methods:
- `trace_event()` — custom named event span
- `trace_retrieval_metrics()` — retrieval quality span
- `trace_evaluation()` — LLM-as-judge score spans
- `trace_ragas_evaluation()` — 5 RAGAS spans per run
- `span()` — async context manager for agent wrapping

→ [[observability/Okahu Cloud|Okahu Cloud]] | [[observability/Span Types|Span Types]]

---

## RAGAS Evaluation Service

**File**: `app/services/ragas_evaluation_service.py`

Lazy-initialized (only when `ENABLE_RAGAS_EVALUATION=true`).

Key design decisions:
- `nest_asyncio.apply` monkey-patched to no-op (uvloop compatibility)
- `LLMContextPrecisionWithoutReference` (no ground truth needed)
- `_EmbeddingServiceWrapper` reuses app's sentence-transformers
- `max_tokens=2048` for faithfulness chain
- Per-metric retry on 429 via `_groq_rotator`

→ [[metrics/Faithfulness|Faithfulness]] | [[metrics/Context Precision|Context Precision]] | [[metrics/Reflection Delta|Reflection Delta]]

---

*[[🏠 Home|← Home]]*
