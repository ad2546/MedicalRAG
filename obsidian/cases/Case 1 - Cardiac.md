---
tags: [case, cardiac, ACS, STEMI]
---

# Case 1 — Classic Cardiac (STEMI)

---

## Input

**Symptoms**: chest pain, diaphoresis, left arm radiation, nausea  
**Vitals**: HR 110, BP 150/95, Temp 37.0  
**History**: smoker, hypertension  
**Labs**: troponin 2.4, CK-MB 45

---

## Output

| Diagnosis | Confidence |
|---|---|
| Acute Coronary Syndrome | high |
| Acute Myocardial Infarction | high |
| Gastrointestinal issue | low (false positive) |

---

## Trace Signals

| Signal | Value | Interpretation |
|---|---|---|
| Okahu spans | 4 monocle | 2 LLM calls (diagnosis + reflection) |
| Path | Fast path | High confidence → no re-retrieval |
| [[metrics/Context Precision\|Context Precision]] | 1.00 | Retrieval pulled cardiology docs |
| [[metrics/Faithfulness\|Faithfulness]] | 0.7 | Good grounding post-fix |

---

## Observations

- **2 LLM calls** = reflection accepted initial diagnosis (high confidence threshold not breached)
- GI issue false positive: likely from "nausea" symptom matching GI docs — acceptable in differential
- ACS correctly identified despite troponin being primary lab

→ [[story/04 - Test Results|Back to Test Results]]

---

*[[🏠 Home|← Home]]*
