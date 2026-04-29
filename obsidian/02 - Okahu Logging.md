---
tags: [okahu, observability, tracing, monocle]
---

# 02 — Okahu Logging

> **Every agent, every inference, every retrieval — visible in real time on Okahu Cloud.**

---

## Architecture

```
MedicalRAG pipeline
   │
   ├─ monocle-apptrace auto-instruments every openai.* call
   │    └─→ OTel span (entity.1.type=inference.openai)
   │
   ├─ tracing_service emits custom spans
   │    ├─→ retrieval_metrics  (hit_rate, avg_score, bucket)
   │    ├─→ eval.{metric}       (LLM-as-judge scores)
   │    └─→ ragas.{stage}       (per-agent RAGAS)
   │
   └─→ BatchSpanProcessor → OkahuSpanExporter → Okahu Cloud
       POST https://ingest.okahu.co/api/v1/trace/ingest
       x-api-key: {OKAHU_API_KEY}
```

**workflow_name = `medicalChatbot`** — must match Okahu portal app name exactly. Wrong name → spans silently routed elsewhere.

---

## Setup (tracing_service.py)

```python
setup_monocle_telemetry(
    workflow_name="medicalChatbot",
    span_processors=[BatchSpanProcessor(OkahuSpanExporter())],
    wrapper_methods=OPENAI_METHODS,       # only wrap LLM calls
    union_with_default_methods=False,     # NOT FastAPI routes
)
```

**Import order is critical**. `tracing_service` must be imported **before** any `AsyncOpenAI` is constructed, so monocle can patch the SDK before instantiation.

**Startup connectivity check**: fire empty batch to ingest URL, log HTTP status. Instant confirmation Okahu is reachable.

---

## Span Inventory

### Auto-instrumented (monocle)

| Span | Source | Okahu View |
|------|--------|------------|
| `openai.resources.chat.completions.AsyncCompletions.create` | Every Groq call | Inference span |
| `workflow` | monocle wrapper | Workflow span |

### Custom (tracing_service)

**Retrieval quality**:
```
span: retrieval_metrics       (type=retrieval)
  retrieval.hit_rate           → 0.0–1.0
  retrieval.avg_score          → avg cosine
  retrieval.top_score_bucket   → "excellent|good|fair|poor"
  retrieval.doc_count

span: retrieval_quality_alert (fired when hit_rate=0 OR avg_score<0.35)
  alert.type: low_retrieval_quality
```

**LLM-as-judge**:
```
span: eval.{metric}           (type=evaluation)
  eval.name        → faithfulness | context_relevancy | answer_relevancy
  eval.score       → 0.0–1.0
  eval.passed      → bool (threshold 0.5)
  eval.reason      → LLM explanation
```

**RAGAS per-agent** (see [[01 - Project Plan|agent chain]]):
```
span: ragas.retrieval  ragas.initial  ragas.reflection  ragas.final
  eval.framework: ragas
  ragas.faithfulness
  ragas.answer_relevancy
  ragas.context_precision
  ragas.overall

span: ragas.reflection_delta
  ragas.delta.faithfulness      → Δ positive = improved
  ragas.delta.answer_relevancy  → Δ
  ragas.regression_detected     → bool (any delta<0)
```

---

## Trace Lifecycle

```
t=0ms    POST /case arrives → trace_id = uuid4()
t=5ms    monocle starts inference span (1st LLM call)
t=2.1s   monocle ends inference span → queued
t=2.2s   tracing_service emits retrieval_metrics span
t=18s    HTTP 200 returned to client                 ← fast path
t=18s    BatchSpanProcessor flushes → Okahu ingest
t=23s    spans visible in Okahu portal

t=18s+   RAGAS background task starts (non-blocking)
t=75s    RAGAS completes 4 stages sequentially
t=80s    5 ragas.* spans exported
t=85s    RAGAS visible in portal
```

Batch export: max 512 spans, 5s interval, force flush on shutdown.

---

## Key Visual Signals in Okahu

| Pattern | Meaning |
|---------|---------|
| **0 inference spans** | Cache hit — no LLM calls |
| **2 inference spans** | Standard path (initial + reflection) |
| **3+ inference spans** | Reflection triggered re-retrieval |
| **Inference span +Rate Limit** | Key rotation happened |
| **`retrieval_quality_alert`** | Retrieval pulled weakly-relevant docs |
| **`ragas.regression_detected`** | Reflection made diagnosis worse |

---

## Why monocle + Okahu

**monocle**: zero-code instrumentation of OpenAI SDK. One `setup_monocle_telemetry()` call auto-wraps every LLM call.

**Okahu**: LLM-aware OTel backend. Displays inference spans with token counts, latency, model, and custom attributes in a clinical-friendly timeline (not generic distributed tracing UI).

Together: full visibility from HTTP request → retrieval → LLM → response in one dashboard.

---

## What This Enabled

Before Okahu:
- "Pipeline returned a diagnosis" → ??? → ship it

After Okahu:
- "Pipeline returned a diagnosis" → see all 5 retrieved docs, their cosine scores, token counts for each LLM call, RAGAS faithfulness per agent, reflection delta → **fix whichever agent is weakest**

Detailed next: [[03 - Issues Found]]

---

*[[🏠 Home|← Home]]* | *[[01 - Project Plan|← Plan]]* | *[[03 - Issues Found|→ Issues Found]]*
