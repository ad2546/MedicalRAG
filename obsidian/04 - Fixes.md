---
tags: [fixes, groundedness, quality]
---

# 04 — Fixes We Made

> **Each fix was triggered by a specific signal in Okahu. No guessing.**

---

## Fix 1 — Relaxed Faithfulness Prompt

**Triggered by**: [[03 - Issues Found#Issue 1 — Pipeline Was Hallucinating Invisibly|Issue 1]] (all cases scored 0.0 faithfulness)

**Before** (too strict):
```
Every claim in the answer MUST be directly supported by
literal text in the context. Any claim not verbatim in
context = hallucination.
```

**After** (clinical-aware):
```
Rate whether each claim is CONSISTENT with retrieved evidence.
Clinical inferences (symptoms A+B+C → condition X) are acceptable
when the evidence supports that inference pattern.

Mark as hallucination only when:
  - Drug names not in retrieved docs
  - Invented numerical values (lab ranges, dosages)
  - Conditions unsupported by any retrieved abstract
```

**Result**: faithfulness went from 0.0 → 0.7–0.91 across clinical cases. Actual hallucinations still flag correctly (e.g., invented drug names score low).

---

## Fix 2 — Background Task GC Protection

**Triggered by**: [[03 - Issues Found#Issue 4 — Silent Background Task Failures|Issue 4]] (RAGAS spans never arrived in Okahu)

**Before** (task GC'd mid-execution):
```python
asyncio.create_task(self._run_and_trace_ragas(...))
# no reference held → Python GC collects → task cancelled
```

**After** (reference held in module-level set):
```python
_background_tasks: set[asyncio.Task] = set()

_t = asyncio.create_task(self._run_and_trace_ragas(...))
_background_tasks.add(_t)
_t.add_done_callback(_background_tasks.discard)
```

**Result**: RAGAS tasks now complete reliably. `ragas.*` spans appear in Okahu ~60s after HTTP response.

---

## Fix 3 — Groq Key Rotation + Validation

**Triggered by**: [[03 - Issues Found#Issue 5 — Rate Limits Caused Cascading Outages|Issue 5]] (single-key TPD limit = outage; later, truncated key 401s)

### Rotator Logic
```python
class _GroqKeyRotator:
    async def call(self, **kwargs):
        for attempt in range(n_keys):
            try:
                return await clients[idx].chat.completions.create(**kwargs)
            except Exception as exc:
                if self._is_rate_limit(exc):   # "429" in msg
                    idx = (idx + 1) % n_keys
                    continue
                raise   # non-429: propagate
```

### Key Validation at Build Time
```python
if stripped and len(stripped) >= 40:
    clients.append(AsyncOpenAI(api_key=stripped, ...))
elif stripped:
    logger.warning("Groq key ignored — truncated (%d chars)", len(stripped))
```

**Result**: 4-key pool. Automatic failover on 429. Truncated keys skipped with warning — no more mysterious 401s.

---

## Fix 4 — RAGAS max_tokens Bump

**Triggered by**: warning `"The LLM generation was not completed. Please increase max_tokens"` in logs while RAGAS was being wired

RAGAS faithfulness metric makes chained LLM calls internally (statement decomposition → verification). With `max_tokens=512`, verification output was truncated → score = -1.0.

**Fix**: `max_tokens=2048` on the RAGAS ChatOpenAI instance.

**Result**: Faithfulness scores compute correctly (no more -1.0 "not computed" returns from truncation).

---

## Fix 5 — RAGAS Rate-Limit Resilience

**Triggered by**: All 4 Groq keys at TPD limit — RAGAS hung for 10+ minutes before giving up

### Problem
RAGAS default `RunConfig`: `max_retries=10`, `max_wait=60s` → 10-minute hang per metric. Also, `asyncio.gather` of 4 stages × 3 metrics = 12 concurrent LLM calls burst → all 4 keys hit 429 simultaneously.

### Fix 5a: Fast-Fail RunConfig
```python
fast_fail_cfg = RunConfig(max_retries=0, max_wait=2, timeout=25)
for m in (faithfulness, answer_relevancy, context_precision):
    m.init(run_config=fast_fail_cfg)
```

### Fix 5b: Sequential Evaluation
```python
# Before: 12 concurrent calls saturate all keys
await asyncio.gather(retrieval, initial, reflection, final)

# After: sequential, spreads load across available key capacity
retrieval_score = await self._eval_retrieval(...)
initial_score   = await self._eval_stage("initial", ...)
reflection_score = await self._eval_stage("reflection", ...)
final_score     = await self._eval_stage("final", ...)
```

### Fix 5c: `max_retries=0` on ChatOpenAI
```python
ChatOpenAI(
    model=settings.groq_model_gen,
    max_tokens=2048,
    max_retries=0,   # disable built-in retries; our _safe_score rotates keys
)
```

Built-in OpenAI retries (2×60s) prevented our key-rotation logic from getting control quickly.

### Fix 5d: All-Metric LLM Rotation
When rotating keys, rebuild **all** metric LLMs (not just the failing one) — otherwise subsequent metrics use the old rate-limited key:
```python
for m in (faithfulness, answer_relevancy, context_precision):
    m.llm = new_llm
    m.init(run_config=fast_fail_cfg)
```

**Result**: RAGAS evaluation returns `-1.0` gracefully in <60s when all keys exhausted, instead of hanging for 10 minutes. Main pipeline never affected (RAGAS runs as background task).

---

## Fix Timeline

```
Run 1  → 500 error (session race condition, non-quality bug)
Run 2  → faithfulness=0.0 every case            → Fix 1 (prompt)
Run 3  → RAGAS task never completes              → Fix 2 (task GC)
Run 4  → max_tokens=512 truncates metric         → Fix 4 (bump to 2048)
Run 5  → key 4 invalid (truncated .env value)    → Fix 3 (length check)
Run 6  → RAGAS complete: delta=+0.85             → ✅ baseline locked
Run 7+ → All 4 keys at TPD, RAGAS hangs 10min    → Fix 5 (fast-fail)
```

Each signal in Okahu → targeted code change → measurable improvement.

---

*[[🏠 Home|← Home]]* | *[[03 - Issues Found|← Issues Found]]* | *[[05 - Final Result|→ Final Result]]*
