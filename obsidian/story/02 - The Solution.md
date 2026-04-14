---
tags: [story, solution, okahu, ragas]
---

# The Solution — Okahu Cloud + RAGAS

> **Instrument every agent. Score every output. Export everything to a cloud dashboard.**

---

## Two-Layer Observability Strategy

```
Layer 1: Execution Traces  →  [[observability/Okahu Cloud|Okahu Cloud]]
         (what ran, when, how long, token counts)

Layer 2: Quality Scores   →  [[metrics/Faithfulness|RAGAS metrics]]
         (was the output actually good?)
```

---

## Layer 1 — Okahu Cloud (Execution Tracing)

[[observability/Okahu Cloud|Okahu Cloud]] receives OpenTelemetry spans via [[observability/Monocle Apptrace|monocle-apptrace]].

**What monocle captures automatically:**
- Every `openai.AsyncCompletions.create()` call → a span
- Token usage (prompt + completion)
- Latency per inference
- `workflow` wrapper spans

**What we added manually:**
- `retrieval_metrics` span → hit rate, avg cosine score, doc count
- `eval.*` spans → faithfulness, context_relevancy, answer_relevancy scores
- `ragas.*` spans → per-agent RAGAS scores + reflection delta

**Key insight**: Monocle only auto-wraps actual `openai.*` SDK calls. Custom spans need explicit OTel instrumentation via [[observability/Span Types|tracing_service]].

---

## Layer 2 — RAGAS Per-Agent Evaluation

[[metrics/Faithfulness|RAGAS]] evaluates each pipeline stage independently:

| Stage | Metric | Question |
|---|---|---|
| [[architecture/Agent Chain#Retrieval Agent\|Retrieval]] | [[metrics/Context Precision\|Context Precision]] | Are these docs relevant? |
| [[architecture/Agent Chain#Diagnosis Agent\|Initial Diagnosis]] | [[metrics/Faithfulness\|Faithfulness]] + [[metrics/Answer Relevancy\|Answer Relevancy]] | Grounded? Relevant? |
| [[architecture/Agent Chain#Reflection Agent\|Reflection]] | Same + [[metrics/Reflection Delta\|Δ delta]] | Did it improve? |
| Final Output | All three | Overall quality |

---

## Why This Combination

| Tool | Good At | Not Good At |
|---|---|---|
| Okahu Cloud | Real-time execution visibility, latency, token cost | Semantic quality scoring |
| RAGAS | Deep semantic evaluation of RAG quality | Execution traces |
| Together | Full picture: did it run well AND produce good output? | — |

---

## The Architecture After

```
Pipeline.run()
    │
    ├─ monocle auto-instruments ──→ Okahu Cloud
    │   (every openai.* call)
    │
    ├─ tracing_service ───────────→ Okahu Cloud
    │   (retrieval metrics, eval spans, ragas spans)
    │
    └─ ragas_evaluation_service ──→ background task
        (runs concurrently, non-blocking)
        ↓
        scores logged + exported via tracing_service
```

---

## Next

→ [[story/03 - Implementation|How we wired it all together]]

---

*[[🏠 Home|← Home]]*
