---
tags: [fix, evaluation, prompt-engineering]
---

# Fix — Faithfulness Prompt Too Strict

> **Observability revealed a broken metric. Prompt engineering fixed it.**

---

## The Signal

Every single case in [[story/04 - Test Results|the test suite]] returned `faithfulness = 0.0`.

Not low. Not variable. Exactly zero. Every time.

This is not a measurement — it's a broken prompt.

---

## Root Cause

The custom LLM-as-judge prompt for faithfulness read:

> "Score 1.0 if every claim is directly and explicitly supported by the provided context."

Clinical diagnoses are *inferences*, not citations:
- A retrieved abstract about respiratory tract infections doesn't say "Pneumonia"
- A retrieved abstract about cardiac biomarkers doesn't say "STEMI"
- But a clinician reading those docs would correctly infer those diagnoses

The judge was rating clinical reasoning as hallucination.

---

## The Fix

Relaxed the scoring rubric:

```
1.0 → All reasoning consistent with evidence; inferences are clinically sound
0.7 → Most claims supported; minor unsupported assertions present  
0.5 → Mixed — some claims well-supported, others speculative
0.2 → Most claims beyond what evidence supports
0.0 → Answer contradicts or ignores the provided context
```

Key change: "consistent with evidence" replaces "directly quoted from evidence."

---

## After the Fix

| Case | Before | After |
|---|---|---|
| Cardiac (Case 1) | 0.0 | 0.7 |
| Meningitis | 0.0 | 0.91 (reflection) |

---

## Lesson

> **A metric that always returns zero is a broken metric, not a failing system.**

Observability data is only useful if the metrics are valid. When every data point is identical, it's a signal to check the measurement tool, not just the subject being measured.

---

*[[🏠 Home|← Home]]*
