---
tags: [fix, asyncio, gc, background-tasks]
---

# Fix — Background Task Garbage Collection

> **"RAGAS initialized" appeared in logs. Then nothing. The task was dead.**

---

## The Symptom

Server logs showed:

```
INFO: RAGAS metrics initialised with Groq + sentence-transformers
```

Then silence. No scores. No errors. No completion log.

---

## The Cause

Python asyncio: **a `Task` object that has no live references gets garbage collected**, even if it's still running.

```python
# BROKEN — task can be GC'd mid-execution
asyncio.create_task(self._run_and_trace_ragas(...))
```

`create_task()` returns a `Task` object. If we don't store it, the GC can collect it when memory pressure occurs — silently cancelling the coroutine.

The Python docs warn about this explicitly, but it's easy to miss.

---

## The Fix

Module-level set holds strong references:

```python
# pipeline.py — top of file
_background_tasks: set[asyncio.Task] = set()

# usage
_t = asyncio.create_task(coro)
_background_tasks.add(_t)
_t.add_done_callback(_background_tasks.discard)   # auto-cleanup on completion
```

The `add_done_callback` ensures the set doesn't grow unboundedly — tasks remove themselves when they finish.

---

## Applied To

All three background tasks in [[architecture/Pipeline|pipeline.run()]]:
- LLM-as-judge evaluation
- RAGAS per-agent evaluation  
- Write audit (PipelineAudit)

And the cache-hit audit write:
- `_write_audit()` in the early-return cache hit path

---

## The Observation Flow

```
Before fix:
  create_task() → task object created → no reference → GC'd → silent

After fix:
  create_task() → stored in _background_tasks → runs to completion
  → done_callback → removed from set → RAGAS scores appear in logs
```

---

## Lesson

> **`asyncio.create_task()` without storing the result is a footgun.**

Always store background task references if you care about their completion. Use a module-level set + `add_done_callback(set.discard)` for automatic cleanup.

---

*[[🏠 Home|← Home]]*
