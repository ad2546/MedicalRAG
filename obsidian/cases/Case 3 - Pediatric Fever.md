---
tags: [case, pediatric, mono, false-positive]
---

# Case 3 — Pediatric Fever + Rash

---

## Input

**Symptoms**: high fever, diffuse rash, sore throat, swollen lymph nodes  
**Vitals**: none provided  
**Labs**: none

---

## Output

| Diagnosis | Confidence |
|---|---|
| Infectious Mononucleosis | high |
| **Leptospirosis** | medium (⚠️ questionable) |
| Viral infection | low |

---

## Trace Signals

| Signal | Value | Interpretation |
|---|---|---|
| Okahu spans | 8 monocle | **4 LLM calls** — extra reflection round |
| [[metrics/Context Precision\|Context Precision]] | 0.60 | Retrieved docs had noise |
| Missing vitals | Handled gracefully | No crash, reasonable diagnosis |

---

## The Leptospirosis Problem

Leptospirosis is a tropical bacterial infection — a stretch for a typical pediatric rash + sore throat presentation (which is strongly suggestive of mono or strep).

**Root cause**: The retrieval pulled documents about tropical fevers alongside mono docs. The LLM included it as a medium-confidence differential because the cosine similarity was just high enough.

Context Precision = 0.60 confirms it: 40% of retrieved docs were off-target.

**Fix needed**: Seed more pediatric-specific fever + rash documents to push down tropical fever false positives for this presentation.

---

## No Vitals — Graceful Degradation

The pipeline handled missing vitals correctly:
- VitalsSchema defaults to `None` for all fields
- LLM prompt receives `"Vitals: {}"` 
- Diagnosis proceeds on symptoms alone

This behavior is worth testing explicitly — it's a realistic clinical scenario.

→ [[05 - Final Result|Back to Final Result]]

---

*[[🏠 Home|← Home]]*
