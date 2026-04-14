---
tags: [fix, ragas, evaluation, integration]
---

# RAGAS Integration

> **Per-agent scoring with no ground truth required. Runs in the background on every pipeline call.**

---

## Design Decisions

### 1. Reference-Free Metrics Only

Standard RAGAS requires a `reference` (ground truth) answer. We don't have ground truth for every clinical case.

**Solution**: Use `LLMContextPrecisionWithoutReference` — scores relevance based on the question alone.

### 2. Local Embeddings

RAGAS `AnswerRelevancy` needs an embedder. Instead of calling an external API:

```python
class _EmbeddingServiceWrapper(BaseRagasEmbeddings):
    def embed_query(self, text):
        return embedding_service.embed(text)   # reuse local model
```

Same `sentence-transformers all-MiniLM-L6-v2` used by retrieval — no extra dependencies, no API cost.

### 3. nest_asyncio Neutralized

```python
# Must happen BEFORE any ragas import
import nest_asyncio as _nest_asyncio
_nest_asyncio.apply = lambda loop=None: None
```

`ragas.executor` calls `nest_asyncio.apply()` at import time. uvloop (used by uvicorn) rejects this. Monkey-patching before import avoids the error entirely.

### 4. 4 Stages Evaluated Concurrently

```python
retrieval_task, initial_task, reflection_task, final_task = await asyncio.gather(
    self._eval_retrieval(...),
    self._eval_stage("initial", ...),
    self._eval_stage("reflection", ...),
    self._eval_stage("final", ...),
    return_exceptions=True,
)
```

All 4 stages run in parallel — RAGAS evaluation adds ~60–90s latency (background), not 4× that.

---

## Metrics Per Stage

| Stage | Faithfulness | Ans. Relevancy | Context Precision |
|---|---|---|---|
| Retrieval | — | — | ✅ |
| Initial | ✅ | ✅ | — |
| Reflection | ✅ | ✅ | — |
| Final | ✅ | ✅ | ✅ |

---

## max_tokens Journey

- First attempt: `max_tokens=512` → faithfulness truncated → score -1.0
- RAGAS faithfulness decomposes answer into statements, then verifies each — verbose output
- Fix: `max_tokens=2048` → scores compute correctly

---

## Related

- [[metrics/Faithfulness|Faithfulness]] — primary metric
- [[metrics/Context Precision|Context Precision]] — retrieval quality
- [[metrics/Reflection Delta|Reflection Delta]] — cross-stage comparison
- [[fixes/Task GC Fix|Task GC Fix]] — background task reliability

---

*[[🏠 Home|← Home]]*
