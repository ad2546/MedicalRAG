---
tags: [case, cache, performance]
---

# Case 4 — Cache HIT

---

## Input

**Symptoms**: identical to [[cases/Case 1 - Cardiac|Case 1]] (chest pain, diaphoresis, left arm radiation, nausea)

---

## Output

Identical to Case 1. Returned in < 10ms.

---

## Trace Signals

| Signal | Value | Interpretation |
|---|---|---|
| Okahu spans | **0 monocle spans** | No LLM calls made |
| Response time | < 10ms | Instant from LRU cache |
| Cache key | hash(symptoms + vitals + labs) | |

---

## Why This Is Powerful for Observability

The cache hit pattern is **immediately visible** in Okahu without any special instrumentation:

```
Normal trace:    workflow → openai.AsyncCompletions × 2 → ...
Cache hit trace: (nothing) → audit span only
```

When a product manager asks "how many requests are served from cache vs hitting the LLM?" — the answer is right there in Okahu: count traces with zero `openai.AsyncCompletions` spans.

---

## Cache Architecture

```python
# cache_service.py
cache_key = cache_service.case_key(
    symptoms,          # list[str]
    vitals.model_dump(),
    labs or {}
)
cached = cache_service.get_case(cache_key)
if cached is not None:
    return DiagnosisResponse(**cached)
```

LRU cache, in-memory. For production: swap to Redis for multi-process sharing.

---

## Cost Implication

Case 1 cost: ~8,000 tokens (5 docs × 400 tokens + prompt + completion)  
Case 4 cost: **0 tokens** — served from cache

Over 100 requests with 30% cache hit rate: saves ~2,400 tokens per cache hit → significant on free-tier Groq (100k TPD limit).

→ [[05 - Final Result|Back to Final Result]]

---

*[[🏠 Home|← Home]]*
