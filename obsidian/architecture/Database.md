---
tags: [architecture, database, pgvector]
---

# Database Layer

> PostgreSQL + pgvector. Async via SQLAlchemy + asyncpg.

---

## Schema

```
documents          → medical knowledge base (721 rows, VECTOR(384))
cases              → patient case records
diagnosis_outputs  → all 3 stages per case (initial, reflection, final)
pipeline_audit     → one row per run (trace_id, timings, token_usage, cache_hit)
users              → auth + rate limiting
```

---

## Vector Search

```sql
-- pgvector cosine similarity via HNSW index
SELECT content, 1 - (embedding <=> $1::vector) AS score
FROM documents
WHERE embedding IS NOT NULL
ORDER BY embedding <=> $1::vector
LIMIT 5;
```

**Index**: `HNSW` with `vector_cosine_ops` — approximate nearest neighbor, fast at scale.

**Embeddings**: `all-MiniLM-L6-v2` → 384-dim float32 vectors, normalized.

---

## Connection Pattern

All DB access is async via `AsyncSession`. Background tasks that need DB access create their own session:

```python
async with AsyncSessionLocal() as session:
    session.add(audit_row)
    await session.commit()
```

**Never reuse request-scoped sessions in background tasks.** → [[fixes/Session Race Condition]]

---

## Pipeline Audit

Every `pipeline.run()` writes a `PipelineAudit` row:

| Column | Content |
|---|---|
| `trace_id` | Links to Okahu traces |
| `stage_timings` | JSON: retrieval/diagnosis/reflection ms |
| `token_usage` | Prompt + completion counts |
| `cache_hit` | Boolean |
| `source` | "api" or "workflow" |

---

*[[🏠 Home|← Home]]*
