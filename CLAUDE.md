# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start local DB (pgvector/postgres on :5432)
docker-compose up -d postgres

# Run dev server
source .venv/bin/activate
uvicorn app.main:app --reload

# Full stack (API + DB together)
docker-compose up

# Run all tests (coverage enforced at 80% via pytest.ini)
pytest

# Run a single test file
pytest tests/test_diagnosis.py -v

# Run a single test
pytest tests/test_diagnosis.py::test_diagnosis_agent_returns_structured_output -v

# Run by marker
pytest -m unit
pytest -m integration

# Seed medical knowledge base (choose one)
python scripts/seed_documents.py
python scripts/seed_documents_expanded.py
python scripts/seed_pubmed.py          # PubMed abstracts

# Init DB schema manually (docker-compose does this automatically via init.sql)
python -c "from app.database import init_db; import asyncio; asyncio.run(init_db())"
```

## Architecture

### Pipeline (`app/pipeline.py`)

`DiagnosisPipeline.run()` is the single entry point for all diagnosis requests. It orchestrates four agents in sequence:

1. **retrieval_agent** — embeds symptoms with sentence-transformers, queries pgvector HNSW index (cosine similarity, `top_k_docs=5`), optionally re-queries with a hint during reflection
2. **diagnosis_agent** — sends retrieved docs + case to Groq LLM, returns structured JSON with diagnoses, confidence, evidence_ids, reasoning
3. **reflection_agent** — LLM self-critiques the initial diagnosis; sets `needs_reretrival=True` if missing evidence, which triggers a second retrieval round
4. **validator_agent** — synchronous guardrails check, filters invalid UUIDs from evidence_ids, produces final `DiagnosisResponse`

Pipeline also has `run_streaming()` which yields SSE events after each stage (consumed by `/diagnosis` when `Accept: text/event-stream`).

Post-pipeline (non-blocking, `asyncio.create_task`):
- Evaluation service (LLM-as-judge) if `ENABLE_EVALUATION=true`
- PipelineAudit write to DB
- Case-level cache population

### Services

| Service | Purpose |
|---|---|
| `llm_service.py` | Groq (default) or OCI via OpenAI SDK; includes JSON repair on malformed LLM output |
| `embedding_service.py` | sentence-transformers `all-MiniLM-L6-v2` (384-dim), runs locally |
| `cache_service.py` | LRU cache keyed on hash(symptoms + vitals + labs); separate per-user and global rate limiters |
| `tracing_service.py` | OTel spans exported to Okahu Cloud via monocle-apptrace; degrades silently to no-op if `OKAHU_API_KEY` unset or in pytest |
| `evaluation_service.py` | LLM-as-judge: faithfulness, context_relevancy, answer_relevancy scores |
| `retrieval_metrics_service.py` | Hit/miss tracking, latency, doc count per pipeline run |

### Key patterns

- All DB access is `async` via SQLAlchemy + asyncpg; sessions injected via FastAPI `Depends`
- `config.py` is a single `pydantic-settings` `Settings` class; all tunable knobs live there
- `DATABASE_URL` auto-normalized from `postgres://` → `postgresql+asyncpg://` in `Settings.fix_database_urls()`
- Tracing init happens at module import time (`tracing_service.py` bottom) — imported early in `main.py` before any LLM client is constructed

### Database schema

Three key tables (defined in `migrations/init.sql`, auto-applied by Docker entrypoint):
- `documents` — medical knowledge base, `embedding VECTOR(384)`, HNSW index on cosine ops
- `cases` — patient case records
- `diagnosis_outputs` — all pipeline stages (initial, reflection, final) keyed by `case_id + stage`
- `pipeline_audit` — one row per pipeline run with `trace_id`, `stage_timings`, `token_usage`, `cache_hit`

### Frontend

Static HTML in `frontend/` (login.html, signup.html, index.html). Served directly by FastAPI via `FileResponse`. No build step.

## Test conventions

- `pytest.ini` sets `asyncio_mode = auto` — no `@pytest.mark.asyncio` needed on async tests
- `--cov-fail-under=80` is enforced; CI will fail below 80% coverage
- `conftest.py` provides `sample_case`, `sample_documents`, `sample_stage_result` fixtures
- Tests mock LLM/DB calls; no real Groq or Postgres required to run tests

## Environment

Minimum required `.env`:
```bash
GROQ_API_KEY=gsk_...
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/medicalrag
AUTH_SECRET_KEY=change-me-in-production
```

Optional:
```bash
LLM_PROVIDER=oci              # switch to OCI Generative AI
OKAHU_API_KEY=okahu_...       # enables Okahu Cloud tracing
ENABLE_EVALUATION=true        # LLM-as-judge scoring (adds latency)
LOG_LEVEL=DEBUG
```

## Cost/TPM tuning

Three knobs in `config.py` (or env vars) to reduce Groq token pressure:
- `MAX_DOC_CHARS` — chars per document sent to LLM (default 2000)
- `MAX_DOCS_PER_PROMPT` — documents included per call (default 3)
- `MAX_REFLECTION_ROUNDS` — re-retrieval attempts (default 1)

## Production notes

- Set `APP_ENV=production` to: disable `/docs`+`/redoc`, enable HSTS header, restrict CORS
- OpenAPI docs disabled in production — test via `/health` and direct API calls
- `ALLOWED_ORIGINS` env var controls CORS in production (comma-separated)
