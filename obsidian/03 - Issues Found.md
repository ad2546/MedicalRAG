---
tags: [issues, hallucinations, groundedness, okahu]
---

# 03 — RAG Issues Analysed Through Okahu

> **Hallucinations, ungrounded confidence, retrieval gaps — every issue we found was surfaced by looking at traces, not reading code.**

---

## The 5 Core Issues

All discovered by inspecting Okahu traces + RAGAS scores across real clinical cases.

---

## Issue 1 — Pipeline Was Hallucinating Invisibly

### Symptom in Okahu
Inference spans returned plausible-sounding diagnoses. Zero way to tell if the LLM was reasoning from retrieved docs or inventing from pretraining.

### What RAGAS Revealed
First run of [[metrics/Faithfulness|faithfulness metric]] across 5 clinical cases:

| Case                     | Faithfulness |
| ------------------------ | ------------ |
| Cardiac (STEMI)          | **0.00**     |
| B-symptoms (TB/Lymphoma) | **0.00**     |
| Pediatric fever          | **0.00**     |
| Multi-system (HFrEF+CKD) | **0.00**     |
| Meningitis               | **0.00**     |

**Every case scored 0.0 faithfulness.** Either every response was pure hallucination, or the evaluation was broken.

### Root Cause
Evaluation prompt was too strict. It required every claim in the diagnosis to be literally quoted from retrieved context. Clinical diagnoses are **inferences** (symptoms X + Y + Z → condition), not citations.

> "Pneumonia" is not literally stated in a PubMed abstract about respiratory infections — but it is the correct clinical inference.

### Impact
Without fixing this, we had no way to distinguish real hallucinations from appropriate clinical reasoning. Every response looked equally "ungrounded" to our monitoring.

Fix: [[04 - Fixes#Fix 1 — Relaxed Faithfulness Prompt]]

---

## Issue 2 — Reflection Agent's Value Was Unmeasured

### Symptom in Okahu
The reflection agent ran on every request. Sometimes it changed the diagnosis, sometimes it didn't. Was this self-critique improving quality or adding noise and latency?

### What RAGAS Revealed
After fixing the faithfulness prompt, we ran the meningitis case end-to-end:

| Stage | Faithfulness | Ans. Relevancy |
|-------|--------------|----------------|
| Initial | **0.06** | 0.75 |
| Reflection | **0.91** | 0.60 |
| **Δ Delta** | **+0.85** | -0.16 |

Initial diagnosis was barely grounded (0.06 = severely hallucinated). After self-critique, reflection output was tightly bound to evidence (0.91).

**Reflection was saving us from shipping ungrounded diagnoses.** But we had no way to see this before RAGAS delta tracking.

### Impact
- Confirmed reflection is a core quality mechanism, not a nice-to-have
- [[metrics/Reflection Delta|delta]] is now a primary health metric
- `ragas.regression_detected=true` flags cases where reflection made things worse

---

## Issue 3 — Retrieval Quality Was Opaque

### Symptom in Okahu
pgvector returned 5 docs. They were fed to the LLM. If the LLM said "pneumonia," was it grounded in good docs or bad docs?

### What Custom Spans Revealed
Added `retrieval_metrics` span to every trace. Across 466 queries:

| Metric | Value |
|--------|-------|
| Avg cosine similarity | **0.498** |
| Hit rate ≥0.70 (good match) | **0%** |
| Hit rate ≥0.50 (fair match) | 87% |
| Best cosine ever | 0.725 |

**Retrieval is stuck at "marginal."** Top doc rarely crosses 0.5 similarity. The LLM was being fed weakly-relevant evidence on ~13% of queries.

Visible in Okahu per-trace as `retrieval.top_score_bucket: poor|fair|good|excellent` — most traces show `fair`, rare `good`.

### Example: Case 3 (Pediatric Fever + Rash)
- Context precision: **0.60** (vs 1.00 for cardiac, meningitis)
- Result: **Leptospirosis** returned as medium-confidence differential for a pediatric viral rash presentation — clinically unlikely
- Okahu `retrieval_metrics` span confirmed poor doc match; peds fever corpus is sparse

### Impact
Low retrieval quality = LLM forced to hallucinate or overgeneralize. Okahu made the gap visible per-case.

Fix direction: seed more disease-specific corpora. See [[cases/Case 3 - Pediatric Fever]].

---

## Issue 4 — Silent Background Task Failures

### Symptom in Okahu
Logs said `"RAGAS metrics initialised"`. Then… nothing. No `ragas.*` spans ever appeared in Okahu. RAGAS was silently dying mid-execution.

### What Log Correlation Revealed
```
INFO  RAGAS metrics initialised with Groq + sentence-transformers
INFO  Okahu export #12 — total=4 monocle=4 span_names=[openai.*]
# no ragas.* spans ever emitted
```

### Root Cause
Python asyncio GC'd the background `Task` object before it completed:

```python
asyncio.create_task(self._run_and_trace_ragas(...))   # returned Task not held
# Python's GC: "no references, delete" → task cancelled mid-LLM-call
```

### Impact
RAGAS evaluation was effectively off. We had no quality data until we held task references explicitly.

Fix: [[04 - Fixes#Fix 2 — Background Task GC Protection]]

---

## Issue 5 — Rate Limits Caused Cascading Outages

### Symptom in Okahu
Sudden spike of `Error code: 429` on inference spans. Whole pipeline stalled for 20+ minutes. One Groq API key at daily TPD (Tokens Per Day) limit = full service outage.

### What Key-Rotation Logging Revealed
When we built a multi-key rotator, new failure surfaced:
```
Error code: 401 - Invalid API Key
```

One of the 4 keys in `.env` was truncated (31 chars vs expected ~56). Rotator cycled into the bad key and failed.

### Impact
- Single-key deployment = single point of failure
- Silent key corruption = rotation helps nothing
- RAGAS evaluation needs its own fast-fail rate-limit logic (different from main pipeline)

Fix: [[04 - Fixes#Fix 3 — Groq Key Rotation + Validation]]

---

## Summary: What Okahu Surfaced

| Issue | Without Okahu | With Okahu |
|-------|---------------|-----------|
| Hallucinations | Undetectable | faithfulness < 0.5 alert |
| Reflection value | Unknown | delta = +0.85 per case |
| Retrieval quality | Unknown | bucket score per trace |
| Background task failure | Silent | zero `ragas.*` spans = task died |
| Rate limits | Mysterious downtime | 429 span + key rotation trail |

Every issue → a measurement → a targeted fix.

---

*[[🏠 Home|← Home]]* | *[[02 - Okahu Logging|← Okahu Logging]]* | *[[04 - Fixes|→ Fixes]]*
