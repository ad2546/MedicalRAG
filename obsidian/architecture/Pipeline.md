---
tags: [architecture, pipeline, orchestration]
---

# DiagnosisPipeline

> The orchestrator. Coordinates 4 agents, manages cache, emits traces, runs background evaluation.

---

## Execution Flow

```
run(db, case, case_id, user_id)
  │
  ├─ 1. Cache check → return cached if hit
  ├─ 2. Retrieval Agent → list[RetrievedDocument]
  ├─ 3. Diagnosis Agent (initial) → DiagnosisStageResult
  ├─ 4. Reflection loop → DiagnosisStageResult
  │      └─ if needs_reretrival: re-run retrieval with hint
  ├─ 5. Validator Agent → DiagnosisResponse
  ├─ 6. Retrieval metrics → trace_retrieval_metrics()
  ├─ 7. LLM-as-judge evaluation (background task)
  ├─ 8. RAGAS per-agent evaluation (background task)
  └─ 9. Write audit → PipelineAudit table
```

**Key file**: `app/pipeline.py`

---

## Background Task Safety

All `asyncio.create_task()` calls are registered in `_background_tasks`:

```python
_t = asyncio.create_task(coro)
_background_tasks.add(_t)
_t.add_done_callback(_background_tasks.discard)
```

Without this, tasks are GC'd mid-execution. → [[fixes/Task GC Fix]]

---

## Streaming Mode

`run_streaming()` yields SSE events after each stage:

```
stage: {name: "retrieval",  status: "done", count: 5}
stage: {name: "diagnosis",  status: "done", ...}
stage: {name: "reflection", status: "done", ...}
final: {diagnoses: [...]}
```

---

## Configuration Knobs

| Setting | Default | Effect |
|---|---|---|
| `MAX_DOCS_PER_PROMPT` | 3 | Docs sent per LLM call |
| `MAX_DOC_CHARS` | 2000 | Chars per document |
| `MAX_REFLECTION_ROUNDS` | 1 | Max re-retrieval loops |
| `REFLECTION_CONFIDENCE_THRESHOLD` | 0.5 | Below this → re-retrieve |

---

## Related

- [[architecture/Agent Chain|Agent Chain]] — the 4 agents
- [[observability/Okahu Cloud|Okahu Cloud]] — trace export
- [[metrics/Faithfulness|RAGAS Evaluation]] — quality scores
- [[fixes/Session Race Condition|Session Race Condition]] — bug found here

---

*[[🏠 Home|← Home]]*
