---
tags: [plan, rag, medical, goals]
---

# 01 — Project Plan

> **Goal: a medical RAG pipeline physicians can actually trust. Grounded answers, no hallucinations, measurable quality.**

---

## Problem Statement

LLMs hallucinate. In clinical decision support, a hallucinated diagnosis is not a minor bug — it is a patient safety issue. A black-box RAG pipeline returning "pneumonia (high)" with no traceable evidence is worse than no answer at all.

**What physicians need**:
- Every diagnosis backed by retrieved evidence
- Confidence scores calibrated to actual grounding
- Audit trail from symptom → retrieved doc → final condition
- Detectable retrieval failures **before** they reach the clinician

**What we are building**: a RAG system where every response is measurably grounded, and every hallucination is visible.

---

## System Architecture

```
Patient Case
     │
     ▼
┌──────────────────┐
│ Retrieval Agent  │  pgvector HNSW cosine search, top-5 docs
└──────────────────┘
     │ list[RetrievedDocument]
     ▼
┌──────────────────┐
│ Diagnosis Agent  │  Groq LLM → initial differential
└──────────────────┘
     │ DiagnosisStageResult (initial)
     ▼
┌──────────────────┐
│ Reflection Agent │  LLM self-critique → refined diagnosis
└──────────────────┘  may trigger re-retrieval with hint
     │ DiagnosisStageResult (reflection)
     ▼
┌──────────────────┐
│ Validator Agent  │  UUID check, format guardrails (no LLM)
└──────────────────┘
     │
     ▼
Final Response
```

**Stack**:
- FastAPI + asyncpg + pgvector HNSW (384-dim, cosine)
- Groq `llama-3.3-70b-versatile` (inference)
- `sentence-transformers all-MiniLM-L6-v2` (embeddings, local)
- PostgreSQL (documents, cases, diagnosis outputs, audit)

---

## Quality Targets

| Dimension | Target | How Measured |
|-----------|--------|--------------|
| **Groundedness** | ≥0.70 faithfulness | [[metrics/Faithfulness\|RAGAS faithfulness]] |
| **Retrieval relevance** | ≥0.70 context precision | [[metrics/Context Precision\|RAGAS context_precision]] |
| **Answer quality** | ≥0.70 answer relevancy | [[metrics/Answer Relevancy\|RAGAS answer_relevancy]] |
| **Reflection gain** | Positive delta | [[metrics/Reflection Delta\|RAGAS delta]] |
| **Hallucination rate** | <10% cases with faithfulness <0.5 | Okahu alert span |
| **Response latency** | <30s p95 | Okahu span duration |

---

## Failure Modes We Must Catch

| Failure | Example | Detection |
|---------|---------|-----------|
| **Pure hallucination** | LLM invents drug name not in retrieved docs | Low faithfulness (<0.3) |
| **Stale retrieval** | Top-5 docs irrelevant to symptoms | Low context_precision |
| **Confident wrong answer** | "STEMI (high)" with no ECG/troponin evidence | Faithfulness vs confidence gap |
| **Reflection regression** | Reflection makes diagnosis worse | Negative reflection delta |
| **Silent degradation** | Accuracy drops over time, nobody notices | Okahu trend monitoring |

---

## Observability Strategy

Two layers:

```
Layer 1: Execution traces  →  Okahu Cloud
         What ran, when, how long, token cost

Layer 2: Quality scores    →  RAGAS per-agent
         Was output actually good? Grounded? Relevant?
```

Detailed next: [[02 - Okahu Logging]]

---

## Success Criteria

Pipeline is successful when:
1. Every trace in Okahu shows per-agent RAGAS scores
2. Hallucinations flagged automatically via `eval.faithfulness < 0.5` spans
3. Reflection delta trend is positive across 30-day window
4. Retrieval hit_rate ≥0.50 on ≥90% of queries
5. Physicians can click any diagnosis → see which docs grounded it

---

*[[🏠 Home|← Home]]* | *[[02 - Okahu Logging|→ Okahu Logging]]*
