---
tags: [hub, medicalrag, okahu, rag]
cssclasses: [home]
---

# MedicalRAG — Making a RAG Physicians Can Trust

> **The story of taking a black-box medical RAG pipeline, adding Okahu Cloud observability + RAGAS per-agent evaluation, finding the hallucinations and groundedness gaps, and fixing them — all measurable.**

---

## The Narrative (5 chapters)

```
Plan  →  Okahu Logging  →  Issues Found  →  Fixes  →  Final Result
```

| # | Chapter | Focus |
|---|---------|-------|
| 01 | [[01 - Project Plan\|Project Plan]] | Goal: groundedness, hallucination mitigation, clinical trust |
| 02 | [[02 - Okahu Logging\|Okahu Logging]] | monocle + Okahu Cloud + custom spans |
| 03 | [[03 - Issues Found\|Issues Found]] | Hallucinations, low groundedness, retrieval gaps — surfaced by traces |
| 04 | [[04 - Fixes\|Fixes]] | Prompt fixes, task GC, key rotation, RAGAS resilience |
| 05 | [[05 - Final Result\|Final Result]] | Measurable groundedness. Hallucinations detectable. Per-agent quality. |

---

## Headline Numbers

| Before Observability | After Observability |
|---------------------|---------------------|
| Faithfulness: **0.0** (broken/invisible) | Faithfulness: **0.70–0.91** per case |
| Reflection value: unknown | Reflection Δ: **+0.85** (meningitis) |
| Hallucination detection: impossible | `eval.faithfulness < 0.5` span alert |
| Per-agent quality: black box | 5 RAGAS spans per pipeline run |
| Rate limit: 1 key → outage | 4-key rotation + validation |
| Test coverage: 40% | **80.28% (167 tests)** |

---

## Evaluation Metrics (supporting)

| Metric | Measures |
|--------|----------|
| [[metrics/Faithfulness\|Faithfulness]] | Is the diagnosis grounded in retrieved evidence? |
| [[metrics/Context Precision\|Context Precision]] | Are retrieved docs relevant to the case? |
| [[metrics/Answer Relevancy\|Answer Relevancy]] | Does the answer address the clinical question? |
| [[metrics/Reflection Delta\|Reflection Delta]] | Did self-critique improve quality? |

---

## Clinical Cases (evidence)

| Case | Condition | Key Signal |
|------|-----------|-----------|
| [[cases/Case 1 - Cardiac\|Case 1]] | STEMI / ACS | 2 LLM spans, fast path, CP=1.00 |
| [[cases/Case 2 - B-Symptoms\|Case 2]] | TB / Lymphoma | Re-retrieval triggered (3 spans) |
| [[cases/Case 3 - Pediatric Fever\|Case 3]] | Mono / Rash | Leptospirosis false positive caught |
| [[cases/Case 4 - Cache Hit\|Case 4]] | cached response | 0 LLM spans — visible |
| [[cases/Case 5 - Multi-System\|Case 5]] | HFrEF + CKD | ACS gap surfaced |

---

## The Core Lesson

> **You cannot improve what you cannot measure.**

Without Okahu + RAGAS: RAG is a black box. Either it worked or it didn't.  
With Okahu + RAGAS: every span is a diagnostic signal. Every hallucination is visible. Every agent is individually accountable.

→ Start at [[01 - Project Plan|Project Plan]]
