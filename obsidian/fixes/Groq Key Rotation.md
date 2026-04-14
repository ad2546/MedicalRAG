---
tags: [fix, groq, rate-limit, resilience]
---

# Fix — Groq Key Rotation

> **One rate limit shouldn't take the system down. Four keys, seamless rotation.**

---

## The Problem

Groq free tier: 100,000 tokens per day per key. With RAGAS evaluation running on every pipeline call (evaluation + RAGAS = ~6 LLM calls per case), one test session exhausted the daily budget.

Single key → 429 → pipeline fails.

---

## The Solution

`_GroqKeyRotator` in `llm_service.py`:

```python
class _GroqKeyRotator:
    def __init__(self):
        self._clients = None    # lazy build
        self._current_idx = 0

    def _build_clients(self):
        keys = [key_1, key_2, key_3, key_4]
        return [AsyncOpenAI(api_key=k, base_url="https://api.groq.com/openai/v1")
                for k in keys
                if k and len(k.strip()) >= 40]   # skip truncated keys

    async def call(self, **kwargs):
        for attempt in range(n_keys):
            idx = (self._current_idx + attempt) % n_keys
            try:
                result = await clients[idx].chat.completions.create(**kwargs)
                self._current_idx = idx   # lock in successful key
                return result
            except Exception as exc:
                if self._is_rate_limit(exc):
                    log("Groq key #{idx+1} rate-limited — rotating")
                    continue
                raise   # non-429: propagate immediately
        raise last_exc
```

---

## Key Validation

Key 4 in `.env` was truncated (31 chars vs ~56 expected). Without validation:
- Client built with invalid key
- Rotator cycles into it after keys 1–3 rate-limit
- 401 Invalid API Key error

**Fix**: Skip keys shorter than 40 chars at build time with a warning.

---

## RAGAS Rotation

RAGAS evaluation service has its own retry logic in `_safe_score()`:

```python
for attempt in range(len(_groq_rotator._clients_list())):
    try:
        return await metric.single_turn_ascore(sample)
    except Exception as exc:
        if "429" in str(exc):
            _groq_rotator._current_idx = (idx + 1) % n
            metric.llm = LangchainLLMWrapper(self._make_groq_llm())
            continue
```

RAGAS uses a `LangchainLLMWrapper(ChatOpenAI(...))` — rotating requires rebuilding the LLM object with the new key.

---

## Result

| Before | After |
|---|---|
| Key 1 rate-limited → HTTP 500 | Key 1 rate-limited → try key 2 |
| 1 key, 1 failure mode | 3 valid keys, seamless rotation |
| No logging of rotation | `WARNING: Groq key #1 rate-limited — rotating` |

---

*[[🏠 Home|← Home]]*
