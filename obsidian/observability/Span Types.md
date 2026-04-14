---
tags: [observability, spans, opentelemetry]
---

# Span Types — Full Inventory

> Every span emitted by MedicalRAG, with source and Okahu visibility.

---

## Auto-Instrumented (monocle)

| Span Name | Source | Okahu View |
|---|---|---|
| `openai.resources.chat.completions.AsyncCompletions.create` | Every Groq LLM call | ✅ Inference span |
| `workflow` | monocle wrapper | ✅ Workflow span |

These are the core visibility layer — every inference call appears automatically.

---

## Custom Spans (tracing_service)

### Retrieval Metrics
```
span: retrieval_metrics
type: retrieval
attributes:
  retrieval.hit_rate        → 0.0 – 1.0
  retrieval.avg_score       → avg cosine similarity
  retrieval.top_score_bucket → "0.0-0.3" | "0.3-0.5" | "0.5-0.7" | "0.7+"
  retrieval.doc_count       → int
```

Alert sub-span fires when `hit_rate == 0.0` or `avg_score < 0.35`:
```
span: retrieval_quality_alert
type: retrieval
attributes:
  alert.type: low_retrieval_quality
```

---

### LLM-as-Judge Evaluation
```
span: eval.{metric}    (one per metric)
type: evaluation
attributes:
  eval.name       → "faithfulness" | "context_relevancy" | "answer_relevancy"
  eval.score      → float [0,1]
  eval.passed     → bool (threshold 0.5)
  eval.reason     → LLM explanation string
```

---

### RAGAS Per-Agent Evaluation
```
span: ragas.retrieval
span: ragas.initial
span: ragas.reflection
span: ragas.final
  type: evaluation
  eval.framework: ragas
  ragas.faithfulness      → float
  ragas.answer_relevancy  → float
  ragas.context_precision → float
  ragas.overall           → avg of computed metrics

span: ragas.reflection_delta
  ragas.delta.faithfulness     → Δ float (positive = improved)
  ragas.delta.answer_relevancy → Δ float
  ragas.regression_detected    → bool (if any delta < 0)
```

---

## Span Count by Case Type

| Scenario | monocle spans | Custom spans |
|---|---|---|
| Cache hit | 0 | 1 (audit only) |
| Standard (2 LLM calls) | 4 | retrieval + eval + 5x ragas |
| Re-retrieval (3 LLM calls) | 6 | same |
| Extra reflection (4 LLM calls) | 8 | same |

---

## Related

- [[observability/Okahu Cloud|Okahu Cloud]] — receives all spans
- [[observability/Monocle Apptrace|Monocle]] — instruments the LLM calls
- [[observability/Trace Flow|Trace Flow]] — lifecycle of a single request

---

*[[🏠 Home|← Home]]*
