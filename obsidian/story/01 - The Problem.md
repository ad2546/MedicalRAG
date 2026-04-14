---
tags: [story, problem, observability]
---

# The Problem — Black Box RAG

> **You built a RAG pipeline. Diagnoses come out. But why? And how good are they?**

---

## What We Had

A 4-agent [[architecture/Pipeline|pipeline]] producing medical differential diagnoses:

```
Patient symptoms → [????] → Diagnosis
```

The pipeline worked. But we had no answers to:
- Which documents did the [[architecture/Agent Chain#Retrieval Agent|retrieval agent]] actually pull?
- Did the [[architecture/Agent Chain#Reflection Agent|reflection agent]] improve the diagnosis or make it worse?
- When confidence dropped, was it a bad question or bad retrieval?
- Was the LLM hallucinating or grounding in evidence?

---

## The Invisible Pipeline

| Agent | What It Did | What We Could See |
|---|---|---|
| Retrieval | pgvector similarity search | Nothing — just "5 docs returned" |
| Diagnosis | Groq LLM call | Raw text output |
| Reflection | Self-critique + re-retrieval | Sometimes it looped, sometimes not |
| Validator | Guardrails check | Pass/fail — no detail |

**Result**: When a diagnosis was wrong, we had no data to explain why.

---

## The Specific Blindspots

### 1. Retrieval Quality Unknown
Was cosine similarity > 0.5 on any document? We didn't track it. Low similarity docs were fed to the LLM as if they were good evidence.

### 2. No Cross-Agent Comparison
Initial diagnosis vs reflection output — we logged both but never **scored** the difference. Was reflection making things better or adding noise?

### 3. Token Waste Invisible
We were sending up to 5 documents × 2000 chars each to every LLM call. With rate limits at 100k tokens/day, we had no idea where they were going.

### 4. Single Point of Failure
One Groq API key. One rate limit. One outage point.

---

## Why This Matters for a Medical System

In clinical decision support, **explainability is not optional**:
- A wrong diagnosis needs an audit trail
- Confidence scores need to be backed by evidence
- Retrieval failures need to be detectable before they reach the physician

---

## The Solution

→ [[story/02 - The Solution|Add observability: Okahu Cloud + RAGAS per-agent evaluation]]

---

*[[🏠 Home|← Home]]*
