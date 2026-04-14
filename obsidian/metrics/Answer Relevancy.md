---
tags: [metrics, ragas, relevancy]
---

# Answer Relevancy

> **"Does the answer actually address the clinical question?"**

---

## Definition

RAGAS Answer Relevancy scores whether the response is relevant to the input question — regardless of whether it's grounded in context.

**Score**: 0.0 → 1.0  
**Method**: Embed the answer and the question, compute cosine similarity. Also uses LLM to generate synthetic questions from the answer and measures alignment.

---

## What It Measures

Unlike [[metrics/Faithfulness|Faithfulness]] (source-grounded?) and [[metrics/Context Precision|Context Precision]] (docs relevant?), Answer Relevancy asks:

> "If I showed someone only the answer, would they think it was answering the original question?"

---

## Clinical Interpretation

- **High** (> 0.75): The diagnosis directly addresses the presented symptoms
- **Medium** (0.5–0.75): Answer is related but may be tangential or overly hedged
- **Low** (< 0.5): Answer doesn't address the question (off-topic, generic, or template-like)

---

## Interesting Tension With Faithfulness

In the meningitis case:
- Initial: faithfulness=0.06, answer_relevancy=0.75
- Reflection: faithfulness=0.91, answer_relevancy=0.60

**Interpretation**: The reflection agent made the answer more grounded (faithfulness +0.85) but slightly less direct/confident (answer_relevancy -0.16). The answer became more careful/hedged after self-critique — reasonable for clinical reasoning.

This trade-off is surfaced by [[metrics/Reflection Delta|Reflection Delta]].

---

## Our Results

| Case | Initial AR | Reflection AR | Δ |
|---|---|---|---|
| Meningitis | 0.75 | 0.60 | -0.16 |
| ACS | 0.80+ | — | — |

The initial output is consistently more assertive (higher AR) while reflection adds caveats (lower AR). Both are valid clinical behaviors.

---

*[[🏠 Home|← Home]]*
