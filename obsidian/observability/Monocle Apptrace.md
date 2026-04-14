---
tags: [observability, monocle, opentelemetry]
---

# monocle-apptrace

> Python library that auto-instruments AI/LLM SDK calls and exports OTel spans to Okahu Cloud (or any OTel backend).

---

## What It Does

Monkey-patches `openai.AsyncCompletions.create()` to wrap every call in an OTel span with:
- `entity.1.type = inference.openai`
- `span.type = inference`
- Token counts in the `metadata` event
- `workflow.name` attribute for Okahu routing

---

## Setup

```python
from monocle_apptrace.instrumentation.common import setup_monocle_telemetry
from monocle_apptrace.instrumentation.metamodel.openai.methods import OPENAI_METHODS

setup_monocle_telemetry(
    workflow_name="medicalChatbot",
    span_processors=[BatchSpanProcessor(OkahuSpanExporter())],
    wrapper_methods=OPENAI_METHODS,
    union_with_default_methods=False,   # don't wrap FastAPI routes
)
```

**Critical**: Must be called before any `AsyncOpenAI` client is constructed. In our app, `tracing_service.py` does this at module level, and is imported first in `main.py`.

---

## What Gets Instrumented

✅ `openai.resources.chat.completions.AsyncCompletions.create`  
❌ FastAPI request handlers (excluded via `union_with_default_methods=False`)  
❌ Custom OTel spans from `tracing_service.span()` (need explicit monocle attributes to appear as monocle spans)

---

## The monocle Span Identifier

A span is a "monocle span" only if it has the `MONOCLE_SDK_VERSION` attribute set. Our `_LoggingOkahuExporter` uses this to separate:

```python
monocle_spans = [s for s in spans if s.attributes.get(MONOCLE_SDK_VERSION)]
```

Custom spans (retrieval, eval) are exported but appear in the "non-monocle" bucket in our logging wrapper.

---

## nest_asyncio Problem

RAGAS imports `ragas.executor` at module load time, which calls `nest_asyncio.apply()`. This fails on uvloop (used by uvicorn). Fix:

```python
import nest_asyncio as _nest_asyncio
_nest_asyncio.apply = lambda loop=None: None   # neutralize before RAGAS loads
```

→ [[architecture/Services#RAGAS Evaluation Service|RAGAS Service]]

---

## Related

- [[observability/Okahu Cloud|Okahu Cloud]] — the destination
- [[observability/Span Types|Span Types]] — what gets emitted
- [[architecture/Services|Services Layer]] — where tracing_service lives

---

*[[🏠 Home|← Home]]*
