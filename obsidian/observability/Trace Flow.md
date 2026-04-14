---
tags: [observability, trace, flow]
---

# Trace Flow — End-to-End Lifecycle

> What happens to a span from code to Okahu Cloud portal.

---

## Timeline

```
POST /case
  │
  ├─ pipeline.run() creates trace_id = uuid4()
  │
  ├─ [monocle] AsyncOpenAI.create() called
  │      └─ monocle patches → OTel span started
  │             attributes: entity.1.type=inference.openai
  │             span.type=inference
  │
  ├─ [monocle] span ended → added to BatchSpanProcessor queue
  │
  ├─ tracing_service.trace_retrieval_metrics()
  │      └─ manual OTel span → retrieval_metrics
  │
  ├─ HTTP response returned to client  ← fast
  │
  └─ [background] BatchSpanProcessor flushes batch to OkahuSpanExporter
         │
         └─ POST https://ingest.okahu.co/api/v1/trace/ingest
                Headers: x-api-key: {OKAHU_API_KEY}
                Body: [{span1}, {span2}, ...]
                
                → Okahu portal: trace visible within ~5s
```

---

## Batch Export

`BatchSpanProcessor` collects spans and exports in batches:
- Max batch size: 512 spans
- Max export interval: 5 seconds
- On shutdown: force flush remaining

Our `_LoggingOkahuExporter` wrapper logs every export:

```
Okahu export #3 — total=8 monocle=4 span_names=[...]
Okahu export #3 result: SpanExportResult.SUCCESS
```

---

## RAGAS Spans (Delayed)

RAGAS runs as a background task — spans arrive in Okahu 60–120 seconds after the HTTP response:

```
t=0s    → HTTP response returned
t=0s    → RAGAS background task starts
t=60s   → RAGAS completes (LLM calls finish)
t=65s   → ragas.* spans exported to Okahu
t=70s   → visible in portal
```

---

## Failure Modes

| Failure | Effect | Recovery |
|---|---|---|
| Okahu unreachable | Spans buffered, export fails | Logged as WARNING, pipeline continues |
| GROQ 429 in RAGAS | Per-metric retry with next key | [[fixes/Groq Key Rotation\|Key rotation]] |
| Background task GC'd | RAGAS never runs | [[fixes/Task GC Fix\|Task ref held in set]] |

---

*[[🏠 Home|← Home]]*
