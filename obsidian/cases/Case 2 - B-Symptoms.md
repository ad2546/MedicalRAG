---
tags: [case, TB, lymphoma, re-retrieval]
---

# Case 2 — Vague B-Symptoms (Re-Retrieval Triggered)

---

## Input

**Symptoms**: fatigue, weight loss, night sweats, low-grade fever  
**Labs**: WBC 11.2, LDH 320

---

## Output

| Stage | Diagnosis | Confidence |
|---|---|---|
| Initial | Tuberculosis | medium |
| Initial | Lymphoma | medium |
| Final | Tuberculosis | high |
| Final | Lymphoma | medium |
| Final | Hypothyroidism | low |

---

## Trace Signals

| Signal | Value | Interpretation |
|---|---|---|
| Okahu spans | 6 monocle | **3 LLM calls** |
| Path | Re-retrieval | Reflection decided initial retrieval insufficient |
| [[metrics/Answer Relevancy\|Answer Relevancy]] | 1.00 | Answer fully addressed symptoms |

---

## Key Finding: Re-Retrieval Is Visible

3 LLM calls instead of 2 = the [[architecture/Agent Chain#Reflection Agent|Reflection Agent]] set `needs_reretrival=True`.

In Okahu's trace timeline, this appears as a third `workflow` span — distinct from the 2-call pattern of Case 1. This makes the pipeline's reasoning process transparent: you can see *when* and *why* it decided to go back for more evidence.

---

## `hit_rate = 0.0` Alert

The retrieval metrics span flagged this case:
- `retrieval.hit_rate = 0.0` — no document scored ≥ 0.5 cosine similarity
- This triggered `retrieval_quality_alert` span in Okahu
- The re-retrieval was the correct response — the first pass was weak

→ [[story/04 - Test Results|Back to Test Results]]

---

*[[🏠 Home|← Home]]*
