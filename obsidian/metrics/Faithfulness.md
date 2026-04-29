---
tags: [metrics, ragas, faithfulness]
---

# Faithfulness

> **"Is every claim in the answer supported by the retrieved context?"**

---

## Definition

RAGAS Faithfulness measures whether the LLM's response stays grounded in the provided documents — not hallucinating beyond what the evidence supports.

**Score**: 0.0 → 1.0  
**Formula**: `statements_supported / total_statements`

---

## How It's Computed

RAGAS runs 2 LLM calls internally:

1. **Decompose**: Split the answer into atomic statements
   > "The patient likely has bacterial meningitis" → statement 1  
   > "CSF glucose < 45 supports bacterial etiology" → statement 2

2. **Verify**: For each statement, does the retrieved context support it?

**This is why `max_tokens=2048` is needed** — the decomposition step can be verbose.

---

## Our Journey With This Metric

### Phase 1 — Always 0.0 (broken)
The evaluation prompt required every claim to be *literally present* in the context text. Clinical diagnoses are inferences — "Pneumonia" is not literally in a respiratory pathophysiology abstract.

**Fix**: [[04 - Fixes|Relaxed the custom LLM-as-judge prompt]]

### Phase 2 — RAGAS Faithfulness Truncated
`max_tokens=512` caused RAGAS faithfulness LLM call to truncate mid-response.  
**Fix**: `max_tokens=2048`

### Phase 3 — Real Scores
After both fixes, faithfulness became meaningful:

| Case | Initial | Reflection | Δ |
|---|---|---|---|
| Meningitis | 0.06 | 0.91 | **+0.85** |
| ACS | 0.7 | — | — |
| Jaundice | 0.8 | — | — |

---

## What Low Faithfulness Means

| Score | Interpretation |
|---|---|
| < 0.3 | LLM is hallucinating — diagnoses not supported by evidence |
| 0.3–0.6 | Partial grounding — some claims unsupported |
| 0.6–0.8 | Good grounding — most claims traceable to context |
| > 0.8 | Excellent — answer tightly bound to evidence |

---

## Relationship to Other Metrics

- High faithfulness + low [[metrics/Answer Relevancy|answer relevancy]] → well-grounded but off-topic
- Low faithfulness + high answer relevancy → plausible but potentially hallucinated
- Both high → ideal clinical output

→ [[metrics/Reflection Delta|Reflection Delta]] shows how much faithfulness improved after self-critique

---

*[[🏠 Home|← Home]]*
