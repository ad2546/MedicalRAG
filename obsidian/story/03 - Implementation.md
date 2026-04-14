---
tags: [story, implementation, code]
---

# Implementation — Wiring Observability

> **Everything was iterative. Traces revealed bugs. Bugs got fixed. Repeat.**

---

## Step 1 — Okahu Cloud Connection

```python
# tracing_service.py
setup_monocle_telemetry(
    workflow_name="medicalChatbot",          # must match Okahu portal app name
    span_processors=[BatchSpanProcessor(OkahuSpanExporter())],
    wrapper_methods=OPENAI_METHODS,          # only wrap LLM calls
    union_with_default_methods=False,        # not FastAPI routes
)
```

**Import order matters**: `tracing_service` must be imported **before** any LLM client is constructed, so monocle patches `AsyncOpenAI` before it's instantiated.

**Connectivity check at startup**: We fire an empty batch to `https://ingest.okahu.co/api/v1/trace/ingest` and log the HTTP status — instant confirmation the key works.

---

## Step 2 — Retrieval Metrics

```python
# Every pipeline run emits a retrieval_metrics span
ret_metrics = retrieval_metrics_service.compute(documents)
tracing_service.trace_retrieval_metrics(trace_id, ret_metrics.to_dict())
```

Fields: `hit_rate`, `avg_score`, `top_score_bucket`, `doc_count`, `latency_ms`

**Alert trigger**: If `hit_rate == 0.0` or `avg_score < 0.35` → emit a `retrieval_quality_alert` span.

---

## Step 3 — LLM-as-Judge Evaluation

```python
# evaluation_service.py — 3 custom metrics via Groq
faithfulness = ...        # are claims supported by retrieved docs?
context_relevancy = ...   # are retrieved docs relevant to symptoms?
answer_relevancy = ...    # does answer address the clinical question?
```

**Bug found here**: The faithfulness prompt was too strict — it required every claim to be literally quoted from context. Clinical diagnoses are *inferences*, not citations. We relaxed the prompt to allow "consistent with evidence" reasoning. → [[fixes/Faithfulness Prompt Fix|Faithfulness Prompt Fix]]

---

## Step 4 — RAGAS Per-Agent Evaluation

[[metrics/Faithfulness|RAGAS]] runs as a background task, scoring each agent independently:

```python
# pipeline.py
_t = asyncio.create_task(
    self._run_and_trace_ragas(...)
)
_background_tasks.add(_t)          # prevent GC
_t.add_done_callback(_background_tasks.discard)
```

**Bug found here**: Background tasks were silently dying — Python's asyncio GC'd them if no reference was held. → [[fixes/Task GC Fix|Task GC Fix]]

**RAGAS LLM setup**:
```python
ChatOpenAI(
    model="llama-3.3-70b-versatile",
    openai_api_base="https://api.groq.com/openai/v1",
    max_tokens=2048,     # 512 was too small — faithfulness metric truncated
    temperature=0.0,
)
```

**Embedding**: Reuses the app's `sentence-transformers all-MiniLM-L6-v2` — no external API call needed.

---

## Step 5 — Groq Key Rotation

```python
# llm_service.py — _GroqKeyRotator
class _GroqKeyRotator:
    async def call(self, **kwargs):
        for attempt in range(n_keys):
            try:
                return await clients[idx].chat.completions.create(**kwargs)
            except Exception as exc:
                if self._is_rate_limit(exc):   # "429" in message
                    rotate to next key
                    continue
                raise   # non-429: propagate immediately
```

**Keys**: GROQ_API_KEY → _2 → _3 → _4 (truncated keys auto-skipped at < 40 chars)

→ [[fixes/Groq Key Rotation|Full details]]

---

## Step 6 — Export RAGAS to Okahu

```python
# tracing_service.py — 5 spans per pipeline run
ragas.retrieval   → context_precision
ragas.initial     → faithfulness, answer_relevancy  
ragas.reflection  → faithfulness, answer_relevancy
ragas.final       → all three + overall
ragas.reflection_delta  → Δ faithfulness, Δ answer_relevancy
```

`ragas.regression_detected = True` if any delta < 0.

---

## Timeline of Discoveries

```
Run 1  → 500 error: session race condition          → [[fixes/Session Race Condition]]
Run 2  → faithfulness = 0.0 every case              → [[fixes/Faithfulness Prompt Fix]]
Run 3  → RAGAS background task never completes      → [[fixes/Task GC Fix]]
Run 4  → max_tokens=512 truncates faithfulness      → bump to 2048
Run 5  → key 4 invalid (truncated in .env)          → length check at build time
Run 6  → RAGAS complete: reflection delta = +0.85   → ✅ pipeline validated
```

---

*[[🏠 Home|← Home]]* | *[[story/04 - Test Results|→ Test Results]]*
