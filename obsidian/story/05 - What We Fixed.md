---
tags: [story, fixes, debugging]
---

# What We Fixed — Bugs Found Through Observability

> **Every bug was discovered by looking at traces, not reading code.**

---

## Bug 1 — Session Race Condition (500 Error)

**Found via**: HTTP 500 on first test run  
**Root cause**: `asyncio.create_task()` passed a request-scoped SQLAlchemy session into a background task. FastAPI closed the session when the HTTP response was sent; the background task then used it.

```
sqlalchemy.exc.IllegalStateChangeError:
Method 'close()' can't be called here;
method '_connection_for_bind()' is already in progress
```

→ [[fixes/Session Race Condition|Full details + fix]]

---

## Bug 2 — Faithfulness = 0.0 Everywhere

**Found via**: Okahu `eval.faithfulness` spans consistently showing 0.0  
**Root cause**: The evaluation prompt required every claim to be literally supported by context text. Clinical diagnoses are *inferences*, not direct quotes.

> "Pneumonia" is not literally stated in a retrieved abstract about community-acquired respiratory infections — but it's the correct clinical inference.

→ [[fixes/Faithfulness Prompt Fix|Full details + fix]]

---

## Bug 3 — Background Tasks Silently Dying

**Found via**: RAGAS initialized ("RAGAS metrics initialised") but no scores ever appeared  
**Root cause**: Python asyncio GC'd the task object before it completed. `asyncio.create_task()` returns a `Task` — if no reference is held, it gets garbage collected mid-execution.

```python
# BROKEN — task can be GC'd
asyncio.create_task(self._run_and_trace_ragas(...))

# FIXED — held in module-level set
_t = asyncio.create_task(self._run_and_trace_ragas(...))
_background_tasks.add(_t)
_t.add_done_callback(_background_tasks.discard)
```

→ [[fixes/Task GC Fix|Full details + fix]]

---

## Bug 4 — RAGAS Truncated Faithfulness

**Found via**: Warning: `"The LLM generation was not completed. Please increase the max_tokens"`  
**Root cause**: RAGAS faithfulness metric chains multiple LLM calls internally (statement decomposition + verification). With `max_tokens=512`, the LLM ran out of tokens mid-response.

**Fix**: Bumped to `max_tokens=2048` for the RAGAS ChatOpenAI instance.

---

## Bug 5 — Invalid API Key After Rotation

**Found via**: `Error code: 401 - Invalid API Key` after key 1 rate-limited  
**Root cause**: `GROQ_API_KEY_4` in `.env` was truncated (31 chars, Groq keys are ~56 chars). The rotator built a client with the invalid key and rotated into it.

**Fix**: Skip keys shorter than 40 chars at build time, log a warning:

```python
if stripped and len(stripped) >= 40:
    clients.append(AsyncOpenAI(...))
elif stripped:
    logger.warning("Groq key ignored — looks truncated (%d chars)", len(stripped))
```

→ [[fixes/Groq Key Rotation|Full details]]

---

## What the Traces Revealed (Without Code Reading)

| Observation in Okahu | Diagnosis |
|---|---|
| 0 monocle spans | Cache hit — no inference |
| 3 LLM spans instead of 2 | Reflection triggered re-retrieval |
| `eval.faithfulness = 0.0` always | Prompt too strict |
| RAGAS init logged, no scores | Task GC'd before completion |
| 429 on key 1, 401 on key 4 | Truncated key in .env |

---

*[[🏠 Home|← Home]]* | *[[story/06 - Outcomes|→ Outcomes]]*
