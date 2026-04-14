---
tags: [hub, medicalrag, observability]
cssclasses: [home]
---

# MedicalRAG — AI Observability with Okahu Cloud

> **How do you debug a black box?** You add visibility. This vault documents how we instrumented a medical RAG pipeline with [[observability/Okahu Cloud|Okahu Cloud]] and iterated to better performance — all driven by what the traces told us.

---

## The Story

```
Black Box RAG  →  Instrumented Pipeline  →  Measurable Quality
```

| Chapter | What Happened |
|---|---|
| [[story/01 - The Problem\|The Problem]] | RAG pipeline with no visibility |
| [[story/02 - The Solution\|The Solution]] | Okahu Cloud + monocle-apptrace |
| [[story/03 - Implementation\|Implementation]] | Wiring spans, RAGAS, key rotation |
| [[story/04 - Test Results\|Test Results]] | 5 clinical cases, live traces |
| [[story/05 - What We Fixed\|What We Fixed]] | Bugs found through observability |
| [[story/06 - Outcomes\|Outcomes]] | Before vs after: measurable gains |

---

## The System

```
User → FastAPI → [[architecture/Pipeline|Pipeline]] → [[architecture/Agent Chain|4 Agents]] → Diagnosis
                              ↓
                    [[observability/Okahu Cloud|Okahu Cloud]] ← spans ← [[observability/Monocle Apptrace|monocle]]
```

- [[architecture/Pipeline|DiagnosisPipeline]] — orchestrates 4 agents in sequence
- [[architecture/Agent Chain|Agent Chain]] — Retrieval → Diagnosis → Reflection → Validator
- [[architecture/Services|Services]] — LLM, Embedding, Cache, Tracing, Evaluation
- [[architecture/Database|Database]] — PostgreSQL + pgvector (HNSW cosine similarity)

---

## Evaluation Metrics

> RAGAS runs after every pipeline call — grading each agent independently.

| Metric | Measures | Stage |
|---|---|---|
| [[metrics/Context Precision|Context Precision]] | Are retrieved docs relevant? | [[architecture/Agent Chain#Retrieval Agent\|Retrieval]] |
| [[metrics/Faithfulness|Faithfulness]] | Is answer grounded in evidence? | [[architecture/Agent Chain#Diagnosis Agent\|Diagnosis]] |
| [[metrics/Answer Relevancy|Answer Relevancy]] | Does answer address the question? | All stages |
| [[metrics/Reflection Delta|Reflection Delta]] | Did self-critique improve quality? | [[architecture/Agent Chain#Reflection Agent\|Reflection]] |

---

## 5 Test Cases

| Case | Condition | Notable Signal |
|---|---|---|
| [[cases/Case 1 - Cardiac\|Case 1]] | STEMI / ACS | 2 LLM spans, fast path |
| [[cases/Case 2 - B-Symptoms\|Case 2]] | TB / Lymphoma | Re-retrieval triggered |
| [[cases/Case 3 - Pediatric Fever\|Case 3]] | Mono / Rash | False positive caught |
| [[cases/Case 4 - Cache Hit\|Case 4]] | Cache HIT | 0 LLM spans — visible! |
| [[cases/Case 5 - Multi-System\|Case 5]] | HFrEF + CKD | ACS gap found |

---

## Fixes Applied

- [[fixes/Session Race Condition|Session Race Condition]] — asyncio background task bug
- [[fixes/Faithfulness Prompt Fix|Faithfulness Prompt Fix]] — eval was too strict for clinical reasoning
- [[fixes/Groq Key Rotation|Groq Key Rotation]] — 4-key pool with automatic failover
- [[fixes/RAGAS Integration|RAGAS Integration]] — per-agent scoring, RAGAS + Groq + local embeddings
- [[fixes/Task GC Fix|Task GC Fix]] — background tasks were silently dying

---

## Key Numbers

| Before | After |
|---|---|
| faithfulness = 0.0 (all cases) | faithfulness = 0.7–0.9 (tuned) |
| 1 API key → rate limit = down | 4-key pool → seamless rotation |
| Agents invisible to Okahu | 4 RAGAS spans + reflection delta |
| Background tasks GC'd silently | Held in `_background_tasks` set |

---

*[[obsidian/🏠 Home|← Back to Home]]*
