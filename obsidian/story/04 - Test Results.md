---
tags: [story, results, testing]
---

# Test Results — 5 Clinical Cases

> **Each case revealed something different about the pipeline. The traces told the story.**

---

## Test Setup

- 721 documents seeded (PubMed abstracts + clinical knowledge base)
- pgvector HNSW index, cosine similarity
- Groq `llama-3.3-70b-versatile`
- `ENABLE_EVALUATION=true`, `ENABLE_RAGAS_EVALUATION=true`
- Okahu service: `medicalChatbot`

---

## [[cases/Case 1 - Cardiac|Case 1 — Classic Cardiac (STEMI)]]

**Symptoms**: chest pain, diaphoresis, left arm radiation, nausea  
**Labs**: troponin 2.4, CK-MB 45

| Output | Value |
|---|---|
| Diagnoses | ACS [high], AMI [high], GI issue [low] |
| Okahu spans | 4 monocle (2 LLM calls) |
| [[metrics/Faithfulness\|Faithfulness]] | 0.7 (post-fix) |
| [[metrics/Context Precision\|Context Precision]] | 1.00 |
| Path | Fast path — reflection didn't re-retrieve |

**Signal**: 2 LLM calls = confidence was high enough to skip re-retrieval. Visible in Okahu as exactly 2 `openai.AsyncCompletions` spans.

---

## [[cases/Case 2 - B-Symptoms|Case 2 — Vague B-Symptoms (re-retrieval triggered)]]

**Symptoms**: fatigue, weight loss, night sweats, low-grade fever  
**Labs**: WBC 11.2, LDH 320

| Output | Value |
|---|---|
| Diagnoses | TB [high], Lymphoma [medium], Hypothyroidism [low] |
| Okahu spans | 6 monocle (3 LLM calls) |
| [[metrics/Answer Relevancy\|Answer Relevancy]] | 1.00 |
| Path | Re-retrieval triggered by reflection agent |

**Signal**: 3 LLM calls = reflection decided retrieval was insufficient and re-queried with a hint. The extra span is the re-retrieval diagnosis. This is the pipeline working as designed.

---

## [[cases/Case 3 - Pediatric Fever|Case 3 — Pediatric Fever + Rash]]

**Symptoms**: high fever, diffuse rash, sore throat, swollen lymph nodes  
**Labs**: none

| Output | Value |
|---|---|
| Diagnoses | Mono [high], **Leptospirosis [medium]**, Viral [low] |
| Okahu spans | 8 monocle (4 LLM calls) |
| [[metrics/Context Precision\|Context Precision]] | 0.60 |
| Path | Extra reflection round — low confidence |

**Signal**: Leptospirosis is a stretch for a typical pediatric rash presentation. Low context precision (0.60) confirms retrieval pulled loosely-matched documents. Needs more pediatric fever documents seeded.

---

## [[cases/Case 4 - Cache Hit|Case 4 — Cache HIT]]

**Symptoms**: identical to Case 1

| Output | Value |
|---|---|
| Diagnoses | Same as Case 1 (cached) |
| Okahu spans | **0 LLM spans** |
| Path | Cache hit — no inference |

**Signal**: Zero `openai.AsyncCompletions` spans in Okahu = instantly identifiable cache hit pattern. This is powerful for cost monitoring — you can see exactly which requests hit cache vs triggered LLM calls.

---

## [[cases/Case 5 - Multi-System|Case 5 — Multi-System (HFrEF + CKD + ACS risk)]]

**Symptoms**: chest tightness, SOB, bilateral edema, decreased urine output, blurry vision  
**History**: DM2, CKD stage 3, hypertension  
**Labs**: troponin 0.8, creatinine 3.2, eGFR 22, K+ 5.8, BNP 1200

| Output | Value |
|---|---|
| Diagnoses | HFrEF [high], CKD exacerbation [high], Hypertensive emergency [medium] |
| Okahu spans | 10 monocle (5 LLM calls — highest load) |
| [[metrics/Context Precision\|Context Precision]] | 0.80 |
| Gap | **ACS not called despite troponin 0.8** |

**Signal**: The elevated troponin (0.8 — borderline) was not flagged as ACS risk alongside the heart failure diagnosis. This is a retrieval gap — cardiology + nephrology comorbidity documents are underrepresented in the knowledge base. Okahu trace confirmed 5 LLM calls (most expensive case).

---

## RAGAS Summary (Meningitis Case — Live Verified)

> **Bacterial meningitis: severe headache, fever, neck stiffness, photophobia**

| Stage | [[metrics/Faithfulness\|Faithfulness]] | [[metrics/Answer Relevancy\|Ans. Relevancy]] | [[metrics/Context Precision\|Context Precision]] |
|---|---|---|---|
| Retrieval | — | — | 1.00 |
| Initial | 0.06 | 0.75 | — |
| Reflection | **0.91** | 0.60 | — |
| Final | 0.91 | 0.62 | 1.00 |
| **Δ Delta** | **+0.85** | -0.16 | — |

**Key finding**: [[metrics/Reflection Delta|Reflection improved faithfulness by +0.85]]. The initial diagnosis was barely grounded (0.06), but after self-critique the reflection output was tightly bound to evidence (0.91). This is the core value of the reflection agent — **visible and measurable**.

---

*[[🏠 Home|← Home]]* | *[[story/05 - What We Fixed|→ What We Fixed]]*
