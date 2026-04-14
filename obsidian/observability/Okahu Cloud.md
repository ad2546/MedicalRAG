---
tags: [observability, okahu, tracing]
---

# Okahu Cloud

> AI observability platform. Receives OpenTelemetry spans from monocle-apptrace and renders them as trace timelines with LLM-specific metadata.

---

## What It Shows

For each pipeline run (trace):
- **Span timeline** — which agents ran, in what order, how long
- **Token counts** — prompt + completion per LLM call
- **Inference spans** — `openai.AsyncCompletions.create` with model name
- **Workflow spans** — the surrounding context
- **Custom spans** — retrieval metrics, eval scores, RAGAS per-agent

---

## Connection

```
MedicalRAG → monocle-apptrace → OkahuSpanExporter → Okahu Cloud
                                       ↑
                    OKAHU_API_KEY in .env
                    service.name = "medicalChatbot"
```

Connectivity verified at startup:

```python
POST https://ingest.okahu.co/api/v1/trace/ingest
Headers: x-api-key: {OKAHU_API_KEY}
Body: {"batch": []}
```

Expected response: `HTTP 204` → Okahu reachable.

---

## Span Types Visible in Portal

| Span | Appears As | Source |
|---|---|---|
| `openai.AsyncCompletions.create` | Inference span | monocle auto-instrument |
| `workflow` | Workflow wrapper | monocle |
| `retrieval_metrics` | Custom span | [[observability/Span Types\|tracing_service]] |
| `eval.faithfulness` | Evaluation span | tracing_service |
| `ragas.retrieval` | RAGAS span | tracing_service |
| `ragas.reflection_delta` | Delta span | tracing_service |

---

## Key Signals

### Cache Hit
- 0 `openai.AsyncCompletions` spans
- Trace duration < 10ms
- Instantly visible as a "silent" trace

### Re-Retrieval Triggered
- 3 LLM spans instead of 2
- Third `workflow` span in timeline

### Rate Limit Hit
- Groq 429 error in span metadata
- [[fixes/Groq Key Rotation|Rotation to next key]] logged

---

## Configuration

```bash
OKAHU_API_KEY=okh_...
MONOCLE_EXPORTER=okahu       # picked up by monocle automatically
```

`workflow_name` in `setup_monocle_telemetry()` must exactly match the Okahu portal app name.

---

## Related

- [[observability/Monocle Apptrace|Monocle Apptrace]] — the instrumentation layer
- [[observability/Span Types|Span Types]] — all spans emitted
- [[observability/Trace Flow|Trace Flow]] — end-to-end trace lifecycle

---

*[[🏠 Home|← Home]]*
