---
tags: [case, HFrEF, CKD, ACS, multi-system]
---

# Case 5 — Multi-System (HFrEF + CKD + ACS Risk)

---

## Input

**Symptoms**: chest tightness, SOB, bilateral edema, decreased urine output, blurry vision  
**History**: DM2, CKD stage 3, hypertension  
**Labs**: troponin 0.8, creatinine 3.2, eGFR 22, K+ 5.8, BNP 1200

---

## Output

| Diagnosis | Confidence |
|---|---|
| Heart failure with reduced ejection fraction (HFrEF) | high |
| CKD exacerbation | high |
| Hypertensive emergency | medium |

---

## Trace Signals

| Signal | Value | Interpretation |
|---|---|---|
| Okahu spans | **10 monocle spans** | 5 LLM calls — most complex case |
| [[metrics/Context Precision\|Context Precision]] | 0.80 | Good retrieval, not perfect |
| Token usage | Highest of all cases | Complex multi-system reasoning |

---

## The ACS Gap

**Troponin 0.8** (above normal upper limit ~0.04 ng/mL) was not explicitly flagged as ACS in the differential.

HFrEF was correctly identified (BNP 1200 is very high, bilateral edema supports), but in a multi-system presentation with elevated troponin, ACS should be in the differential.

**Root cause**: The knowledge base likely has fewer documents on the intersection of heart failure + ACS + CKD comorbidity. The retrieval pulled excellent heart failure and CKD documents but fewer combined cardio-renal syndrome + ACS documents.

**Fix**: Seed more cardiology + nephrology comorbidity articles — especially troponin-in-CKD and cardiorenal syndrome documents.

---

## 5 LLM Calls — Maximum Reasoning

This is the most expensive trace:
1. Initial diagnosis
2. Reflection critique
3. Re-retrieval (insufficient evidence for complex comorbidity)
4. Second diagnosis pass
5. Second reflection

Every extra LLM call is visible in Okahu as another `workflow` span. The pipeline is working as designed — it's spending more compute on a harder case.

→ [[story/04 - Test Results|Back to Test Results]]

---

*[[🏠 Home|← Home]]*
