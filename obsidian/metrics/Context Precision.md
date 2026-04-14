---
tags: [metrics, ragas, retrieval]
---

# Context Precision

> **"Are the retrieved documents actually relevant to the question?"**

---

## Definition

RAGAS `LLMContextPrecisionWithoutReference` asks the LLM whether each retrieved document is relevant to answering the question — without needing a ground truth answer.

**Score**: 0.0 → 1.0  
**Formula**: Weighted average relevance across retrieved docs (position-weighted — top docs count more)

---

## Why "WithoutReference"

The standard `ContextPrecision` metric requires a `reference` (ground truth answer) to compare against. In a clinical setting, we don't have gold-standard answers for every query — so we use the reference-free variant that scores relevance based on the question alone.

---

## What It Reveals

| Score | Meaning | Action |
|---|---|---|
| 1.00 | All retrieved docs are highly relevant | Retrieval working perfectly |
| 0.60–0.80 | Most docs relevant, some noise | Review HNSW index or seeding |
| < 0.50 | Retrieval pulling loosely-matched docs | Seed more targeted documents |

---

## Our Results

| Case | Context Precision | Notes |
|---|---|---|
| [[cases/Case 1 - Cardiac\|Cardiac (STEMI)]] | 1.00 | Perfect retrieval |
| [[cases/Case 3 - Pediatric Fever\|Pediatric Fever]] | 0.60 | Leptospirosis docs off-target |
| Meningitis (live) | 1.00 | All 5 docs were CNS infection docs |

---

## Retrieval Quality Alert

When raw cosine similarity is low, a separate alert fires:

```python
# tracing_service.py
if hit_rate == 0.0 or avg_score < 0.35:
    alert_span = tracer.start_span("retrieval_quality_alert")
```

This is distinct from RAGAS context precision — it's a signal that the vector search itself struggled, even before LLM-based relevance scoring.

---

## Related

- [[architecture/Agent Chain#Retrieval Agent|Retrieval Agent]] — where docs are fetched
- [[metrics/Faithfulness|Faithfulness]] — are answers grounded in those docs?
- [[observability/Span Types|Span Types]] — retrieval metrics span

---

*[[🏠 Home|← Home]]*
