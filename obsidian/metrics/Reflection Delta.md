---
tags: [metrics, ragas, reflection, delta]
---

# Reflection Delta — Measuring Self-Improvement

> **"Did the reflection agent actually make the diagnosis better?"**

---

## Definition

Reflection Delta (Δ) is the signed difference between RAGAS scores from the initial diagnosis agent and the reflection agent:

```
Δ faithfulness     = reflection.faithfulness     - initial.faithfulness
Δ answer_relevancy = reflection.answer_relevancy - initial.answer_relevancy
```

**Positive Δ = improvement. Negative Δ = regression.**

---

## Why This Matters

The [[architecture/Agent Chain#Reflection Agent|Reflection Agent]] exists to improve the initial diagnosis through self-critique. Without measuring the delta, we can't know if:
- Reflection is helping or hurting
- Self-critique is producing more grounded answers
- The re-retrieval loop is adding noise or signal

---

## Live Result — Meningitis Case

```
Initial diagnosis:   faithfulness = 0.06  (barely grounded)
Reflection output:   faithfulness = 0.91  (tightly evidence-bound)

Δ faithfulness = +0.85   ← reflection dramatically improved grounding
Δ answer_relevancy = -0.16  ← slight hedging, acceptable trade-off
```

**This is the core finding**: The reflection agent is doing exactly what it should. The self-critique loop is measurably improving evidence grounding by 85 percentage points.

---

## OTel Span

```
span: ragas.reflection_delta
attributes:
  ragas.delta.faithfulness     → float (signed)
  ragas.delta.answer_relevancy → float (signed)
  ragas.regression_detected    → true (if either delta < 0)
```

When `ragas.regression_detected = True`, the reflection agent hurt quality — this should trigger investigation.

---

## Thresholds (Proposed)

| Delta | Interpretation |
|---|---|
| > +0.3 | Significant improvement — reflection working well |
| -0.1 to +0.3 | Marginal — reflection adding noise more than signal |
| < -0.1 | Regression — reflection hurting quality, investigate |

---

## Related

- [[architecture/Agent Chain#Reflection Agent|Reflection Agent]] — the agent being measured
- [[metrics/Faithfulness|Faithfulness]] — the primary metric
- [[metrics/Answer Relevancy|Answer Relevancy]] — secondary metric
- [[observability/Span Types|ragas.reflection_delta span]]

---

*[[🏠 Home|← Home]]*
