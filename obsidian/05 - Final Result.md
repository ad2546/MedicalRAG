---
tags: [results, outcomes, groundedness, quality]
---

# 05 — Final Result

> **Measurable groundedness. Detectable hallucinations. Visible quality per agent.**

---

## Before vs After

| Dimension | Before Okahu + RAGAS | After |
|-----------|---------------------|-------|
| Faithfulness (groundedness) | **0.00** (broken/invisible) | **0.70–0.91** per case |
| Reflection improvement | Unmeasured | **+0.85 delta** (meningitis case) |
| Retrieval relevance | Unknown | bucket + score per trace |
| Hallucination detection | Impossible | `eval.faithfulness < 0.5` span |
| Per-agent quality | Black box | 5 RAGAS spans per pipeline run |
| API resilience | 1 key → outage | 4-key rotation with validation |
| Background task reliability | Silent failures | Tracked in `_background_tasks` |
| Rate limit handling | 10-min hang | <60s fast-fail + graceful `-1.0` |
| Test coverage | 40% | **80.28% (167 tests)** |

---

## Groundedness — The Core Win

### Meningitis Case (live-verified)

Patient: severe headache, fever, neck stiffness, photophobia.

| Stage | [[metrics/Faithfulness\|Faithfulness]] | [[metrics/Answer Relevancy\|Ans. Relevancy]] | [[metrics/Context Precision\|Context Precision]] |
|-------|---------|---------|---------|
| Retrieval | — | — | **1.00** |
| Initial | 0.06 | 0.75 | — |
| Reflection | **0.91** | 0.60 | — |
| Final | **0.91** | 0.62 | 1.00 |
| **Δ Delta** | **+0.85** | -0.16 | — |

**What this tells us**:
- Initial diagnosis was nearly pure hallucination (0.06 — LLM was speculating)
- Reflection agent rescued it by re-grounding to retrieved evidence (0.91)
- Without `reflection_delta`, we would ship the hallucinated initial output
- **Reflection is the safety net that makes this pipeline clinically acceptable**

---

## Hallucination Detection in Production

Every pipeline call now emits these evaluation spans. When faithfulness drops below threshold, the trace is instantly flagged:

```
eval.faithfulness = 0.34
eval.passed       = false
eval.threshold    = 0.5
eval.reason       = "Diagnosis mentions Amoxicillin 500mg TID which
                     is not present in retrieved documents. Other
                     claims are consistent with evidence."
```

Physicians reviewing the trace see exactly which claim was ungrounded and why.

**Okahu dashboard**: filter traces where `eval.faithfulness < 0.5` → every suspected hallucination in one view.

---

## Agent-Level Quality Visibility

For every request:

```
ragas.retrieval       context_precision = 1.00
ragas.initial         faithfulness = 0.06    answer_relevancy = 0.75
ragas.reflection      faithfulness = 0.91    answer_relevancy = 0.60
ragas.final           faithfulness = 0.91    overall = 0.83
ragas.reflection_delta
    ragas.delta.faithfulness     = +0.85    ← reflection saved the case
    ragas.delta.answer_relevancy = -0.16    ← minor trade-off
    ragas.regression_detected    = false
```

Each agent's contribution is **isolated and measurable**.

---

## 5 Clinical Cases — Evidence

| Case | Condition | LLM Calls | Signal |
|------|-----------|-----------|--------|
| [[cases/Case 1 - Cardiac\|1]] | STEMI / ACS | 2 | Fast path, CP=1.00 |
| [[cases/Case 2 - B-Symptoms\|2]] | TB / Lymphoma | 3 | Re-retrieval triggered, AR=1.00 |
| [[cases/Case 3 - Pediatric Fever\|3]] | Mono / Rash | 4 | Leptospirosis FP caught, CP=0.60 |
| [[cases/Case 4 - Cache Hit\|4]] | (cached) | **0** | Cache hit visible in Okahu |
| [[cases/Case 5 - Multi-System\|5]] | HFrEF + CKD | 5 | ACS gap surfaced, CP=0.80 |

Each case surfaced a different property:
- Cache behavior (Case 4 — 0 inference spans)
- Re-retrieval flow (Case 2 — 3 spans not 2)
- Retrieval weakness (Case 3 — pediatric corpus sparse)
- Comorbidity gap (Case 5 — cardiology+nephrology interaction missed)

---

## Response Quality of Each Agent

| Agent | Measured By | Current State |
|-------|-------------|---------------|
| **Retrieval** | [[metrics/Context Precision\|Context Precision]] | 1.00 on mainline cases, 0.60 on edge (peds) |
| **Diagnosis (initial)** | [[metrics/Faithfulness\|Faithfulness]] | 0.06–0.85 (variable, often needs reflection) |
| **Reflection** | [[metrics/Reflection Delta\|Δ delta]] | +0.85 best case, regressions flagged |
| **Final** | All metrics | 0.83 overall (meningitis) |

**Verdict**: reflection agent is the quality safety net. Initial diagnosis alone is unreliable. Final output after reflection is clinically acceptable (≥0.70 faithfulness).

---

## Known Remaining Gaps

Surfaced by Okahu, targeted for next iteration:

1. **Case 5 ACS gap** — troponin 0.8 not flagged alongside HFrEF. Cardio+nephro comorbidity docs underrepresented in the 721-doc corpus.
2. **Case 3 pediatric FP** — Leptospirosis for viral rash is a stretch. Need peds-fever corpus.
3. **Retrieval ceiling** — avg cosine 0.498, hit rate ≥0.70 = 0% of queries. Consider hybrid BM25 + dense, or `top_k=8`.
4. **No Okahu alerting** — scores captured, but no threshold alerts wired (e.g., `reflection_delta < -0.2` should page).

Each is a concrete, measurable target — not a vague "improve quality."

---

## The Core Lesson

> **Observability turned a black-box RAG into a measurable system. Measurements turned into targeted fixes. Fixes turned into clinically meaningful quality.**

Before: *"Does the pipeline work?"* → Yes/No  
After: *"Was this specific response grounded?"* → 0.91 faithfulness, +0.85 reflection delta, retrieval CP=1.00

Every number is actionable. Every span is a diagnostic signal. Every hallucination is visible.

---

## Supporting Material

- **Metrics**: [[metrics/Faithfulness]] | [[metrics/Context Precision]] | [[metrics/Answer Relevancy]] | [[metrics/Reflection Delta]]
- **Cases**: [[cases/Case 1 - Cardiac|Case 1]] | [[cases/Case 2 - B-Symptoms|Case 2]] | [[cases/Case 3 - Pediatric Fever|Case 3]] | [[cases/Case 4 - Cache Hit|Case 4]] | [[cases/Case 5 - Multi-System|Case 5]]

---

*[[🏠 Home|← Home]]* | *[[04 - Fixes|← Fixes]]*
