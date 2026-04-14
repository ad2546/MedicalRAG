---
tags: [story, outcomes, results]
---

# Outcomes — Before vs After

> **Observability turned guesses into measurements. Measurements turned into improvements.**

---

## Quantified Improvements

| Dimension | Before | After |
|---|---|---|
| [[metrics/Faithfulness\|Faithfulness score]] | 0.0 (broken eval) | 0.7–0.91 per case |
| [[metrics/Reflection Delta\|Reflection improvement]] | Unmeasured | +0.85 faithfulness delta |
| API resilience | 1 key → rate limit = outage | 3-key rotation → seamless |
| Background task reliability | Silent failures | Tracked in `_background_tasks` |
| Retrieval quality | Unknown | hit_rate, avg_score, bucket per trace |
| Agent visibility | Black box | 5 RAGAS spans per pipeline run |

---

## What Okahu Cloud Showed Us

### Cache Hit Pattern
Zero `openai.AsyncCompletions` spans = instant cache hit detection. No code needed — just look at the trace span count.

### Re-Retrieval Pattern
3 LLM spans (instead of 2) = [[architecture/Agent Chain#Reflection Agent|reflection agent]] decided retrieval was insufficient. Visible in Okahu's trace timeline as a 3rd `workflow` span.

### Reflection Quality
[[metrics/Reflection Delta|RAGAS delta]] on every run. When `ragas.delta.faithfulness > 0`, reflection improved grounding. When negative, it regressed — `ragas.regression_detected = True` flags it.

### Token Cost By Case
| Case | LLM Calls | Signal |
|---|---|---|
| [[cases/Case 4 - Cache Hit\|Cache hit]] | 0 | Free response |
| [[cases/Case 1 - Cardiac\|High confidence]] | 2 | Fast path |
| [[cases/Case 2 - B-Symptoms\|Re-retrieval]] | 3 | Extra evidence fetch |
| [[cases/Case 3 - Pediatric Fever\|Low confidence]] | 4 | Double reflection |
| [[cases/Case 5 - Multi-System\|Complex comorbidity]] | 5 | Maximum reasoning |

---

## What Changed in the Code

| What | Why We Changed It | Triggered By |
|---|---|---|
| Session management | Race condition → 500 | First test run |
| Eval prompt | 0.0 faithfulness across all cases | Okahu span data |
| Background task refs | Silent RAGAS failures | Missing log lines |
| RAGAS max_tokens | Truncated LLM generation | Warning in logs |
| Key rotation | Rate limit outage | Live traffic |
| Key validation | 401 after rotation | Post-rotation 401 error |

---

## The Core Lesson

> **You cannot improve what you cannot measure.**

A RAG pipeline without observability has two modes: working and broken. With [[observability/Okahu Cloud|Okahu Cloud]] + [[metrics/Faithfulness|RAGAS]], it has a full spectrum:

```
retrieval_cp=0.0  →  retrieval_cp=1.0   (docs are relevant)
faithfulness=0.0  →  faithfulness=0.91  (answers are grounded)
delta=-0.3        →  delta=+0.85        (reflection is helping)
```

Every number is actionable. Every span is a diagnostic signal.

---

## What's Next

- Seed cardiology + nephrology comorbidity docs → fix [[cases/Case 5 - Multi-System|Case 5]] ACS gap
- Add pediatric fever corpus → fix [[cases/Case 3 - Pediatric Fever|Case 3]] Leptospirosis FP
- Set threshold alerts in Okahu when `reflection_delta < -0.2` (regression monitoring)
- A/B test retrieval strategies with RAGAS context precision as the objective metric

---

*[[🏠 Home|← Home]]*
