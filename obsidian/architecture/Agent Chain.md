---
tags: [architecture, agents, RAG]
---

# Agent Chain — 4-Stage Reasoning

> Sequential, self-refining. Each agent's output is the next agent's input.

---

## The Chain

```
Patient Case
     │
     ▼
┌─────────────────────┐
│   Retrieval Agent   │  pgvector cosine similarity → top-5 docs
└─────────────────────┘
     │ list[RetrievedDocument]
     ▼
┌─────────────────────┐
│   Diagnosis Agent   │  LLM call → initial differential diagnosis
└─────────────────────┘
     │ DiagnosisStageResult (initial)
     ▼
┌─────────────────────┐
│  Reflection Agent   │  LLM self-critique → refined diagnosis
└─────────────────────┘  (may re-trigger retrieval if needed)
     │ DiagnosisStageResult (reflection)
     ▼
┌─────────────────────┐
│  Validator Agent    │  Guardrails: UUID check, format validation
└─────────────────────┘
     │ DiagnosisResponse
     ▼
Final Output
```

---

## Retrieval Agent

**File**: `app/agents/retrieval_agent.py`  
**What it does**: Embeds symptoms with `sentence-transformers all-MiniLM-L6-v2`, queries pgvector HNSW index.

```sql
SELECT id, content, source, disease_category, evidence_type,
       1 - (embedding <=> $1::vector) AS score
FROM documents
WHERE embedding IS NOT NULL
ORDER BY embedding <=> $1::vector
LIMIT $2
```

**Observability**: Hit rate, avg cosine score, top_score_bucket → [[observability/Span Types|retrieval_metrics span]]  
**RAGAS**: [[metrics/Context Precision|Context Precision]] — are these docs actually relevant?

---

## Diagnosis Agent

**File**: `app/agents/diagnosis_agent.py`  
**What it does**: Sends retrieved docs + patient case to Groq LLM. Returns structured JSON with conditions, confidence, evidence_ids, reasoning.

**Token pressure point**: `MAX_DOCS_PER_PROMPT=3`, `MAX_DOC_CHARS=2000` — tunable to reduce TPM usage.

**RAGAS**: [[metrics/Faithfulness|Faithfulness]] — are claims grounded in retrieved docs?  
**RAGAS**: [[metrics/Answer Relevancy|Answer Relevancy]] — does the diagnosis address the symptoms?

---

## Reflection Agent

**File**: `app/agents/reflection_agent.py`  
**What it does**: Reviews the initial diagnosis. Self-critiques evidence quality. Sets `needs_reretrival=True` if more evidence is needed → triggers a second retrieval with a hint.

**Okahu signal**: 
- 2 LLM spans = reflection accepted the initial diagnosis
- 3+ LLM spans = re-retrieval was triggered

**RAGAS**: [[metrics/Reflection Delta|Δ delta]] — did self-critique improve faithfulness and relevancy?

**Key metric (meningitis case)**: initial faithfulness=0.06 → reflection faithfulness=0.91 → **delta +0.85**

---

## Validator Agent

**File**: `app/agents/validator_agent.py`  
**What it does**: Synchronous (no LLM call). Filters invalid UUIDs from `evidence_ids`. Ensures minimum conditions returned.

**No LLM** = no monocle span. Validation is pure Python logic.

---

## Related

- [[architecture/Pipeline|Pipeline]] — orchestrates this chain
- [[observability/Monocle Apptrace|Monocle]] — auto-instruments LLM calls
- [[metrics/Reflection Delta|Reflection Delta]] — measures self-improvement

---

*[[🏠 Home|← Home]]*
