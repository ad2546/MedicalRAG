# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
# Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file (minimum required)
cat > .env << 'EOF'
GROQ_API_KEY=gsk_...
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/medicalrag
AUTH_SECRET_KEY=change-me-in-production
EOF
```

## Commands

```bash
# Start local DB (pgvector/postgres on :5432)
docker-compose up -d postgres

# Run dev server (requires .venv activation + .env file)
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

### Request Flow

All diagnosis requests flow through **four agents in `app/pipeline.py`**, orchestrated by `DiagnosisPipeline.run()`:

1. **retrieval_agent** — embeds symptoms with sentence-transformers, queries pgvector HNSW index (cosine similarity, `top_k_docs=5`), optionally re-queries with a hint during reflection
2. **diagnosis_agent** — sends retrieved docs + case to Groq LLM, returns structured JSON with diagnoses, confidence, evidence_ids, reasoning
3. **reflection_agent** — LLM self-critiques initial diagnosis; sets `needs_reretrieval=True` if missing evidence (triggers second retrieval round)
4. **validator_agent** — synchronous guardrails check, filters invalid UUIDs from evidence_ids, produces final `DiagnosisResponse`

Two execution modes:
- `run()` — returns `DiagnosisResponse` (blocking); used by `/cases` and `/workflow/run`
- `run_streaming()` — yields SSE events after each stage; used by `/cases/stream` (streaming to browser)

Post-pipeline tasks (non-blocking, spawned as `asyncio.create_task`):
- Evaluation service (LLM-as-judge RAGAS metrics) if `ENABLE_EVALUATION=true`
- PipelineAudit DB write (trace_id, stage timings, token usage)
- Case-level cache population

### API Routing

| Router | Endpoints | Purpose |
|--------|-----------|---------|
| `auth.py` | `/auth/signup`, `/auth/login`, `/auth/logout`, `/auth/me` | User registration, login, session |
| `cases.py` | `POST /cases`, `POST /cases/stream` | Create case (sync or streaming diagnosis) |
| `diagnosis.py` | `GET /diagnosis/{case_id}` | Retrieve cached diagnosis for a case |
| `chat.py` | `POST /chat/{case_id}` | Multi-turn chat about diagnosis (context-aware) |
| `documents.py` | `GET /documents/{doc_id}` | Fetch document citation by ID |
| `workflow.py` | `POST /workflow/run`, `GET /workflow/cache/stats`, `DELETE /workflow/cache` | External API for orchestrators (n8n, cron); Bearer token auth |

### Services

| Service | Purpose |
|---|---|
| `llm_service.py` | Groq (default) or OCI via OpenAI-compatible SDK; `_GroqKeyRotator` cycles through up to 4 keys (`GROQ_API_KEY`→`_2`→`_3`→`_4`) on 429; JSON repair on malformed output |
| `embedding_service.py` | sentence-transformers `all-MiniLM-L6-v2` (384-dim), runs locally; no API calls |
| `cache_service.py` | Two-level LRU: case results (TTL 1h, max 500 entries) keyed on SHA256(symptoms+vitals+labs) + LLM prompt responses (TTL 24h, max 2000 entries); global daily request counter resets at UTC midnight |
| `tracing_service.py` | OTel spans exported to Okahu Cloud via monocle-apptrace; degrades gracefully if `OKAHU_API_KEY` unset |
| `evaluation_service.py` | Legacy evaluation endpoint; calls LLM-as-judge for scores |
| `ragas_evaluation_service.py` | RAGAS framework integration; faithfulness, context_relevancy, answer_relevancy scoring |
| `retrieval_metrics_service.py` | Hit/miss tracking, latency, doc count per pipeline run |

### Auth & rate limiting

- Token format: `base64url(payload).base64url(HMAC-SHA256-sig)` stored in `access_token` httpOnly cookie (not a standard JWT)
- `development` mode bypasses all per-user and global quota checks
- Two quota layers: per-user DB atomic update (fails if `requests_used >= request_limit`) + global in-memory `GlobalRateLimiter` (resets UTC midnight)

### Key patterns

- All DB access is `async` via SQLAlchemy + asyncpg; sessions injected via FastAPI `Depends` (no manual connection management)
- `config.py` is a single `pydantic-settings` `Settings` class; all tunable knobs live there
- `DATABASE_URL` auto-normalized from `postgres://` → `postgresql+asyncpg://` in `Settings.fix_database_urls()`
- Tracing init happens at module import time (`tracing_service.py` bottom) — imported early in `main.py` before any LLM client is constructed
- LLM client is created once at module load; subsequent calls reuse the same client (cost reduction)

### Database schema

Core tables (defined in `migrations/init.sql`, auto-applied by Docker entrypoint):
- `documents` — medical knowledge base, `embedding VECTOR(384)` (all-MiniLM-L6-v2), HNSW index on cosine ops
- `cases` — patient case records (symptoms, vitals, labs as JSONB)
- `diagnosis_outputs` — diagnosis results per stage (initial, reflection, final); keyed by `case_id + stage`
- `users` — user accounts with PBKDF2-SHA256 password hash (210k iterations); `request_limit` / `requests_used` for per-user quota

Additional tables (created by ORM during init):
- `pipeline_audit` — one row per pipeline run with `trace_id`, `stage_timings`, `token_usage`, `source` (web or workflow)

### Frontend

Static HTML in `frontend/` (login.html, signup.html, index.html). Served directly by FastAPI via `FileResponse`. No build step or npm involved.

Security headers set in `SecurityHeadersMiddleware` (main.py): X-Frame-Options, CSP, HSTS (production only), cache-control (no-store for PHI endpoints).

## Test conventions

- `pytest.ini` sets `asyncio_mode = auto` — no `@pytest.mark.asyncio` needed on async tests
- `--cov-fail-under=80` is enforced; CI will fail below 80% coverage
- `conftest.py` provides `sample_case`, `sample_documents`, `sample_stage_result` fixtures
- Tests mock LLM/DB calls; no real Groq or Postgres required to run tests

## Environment

**Minimum required `.env`:**
```bash
GROQ_API_KEY=gsk_...
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/medicalrag
AUTH_SECRET_KEY=change-me-in-production  # HMAC-SHA256 signing key (custom token, NOT JWT)
```

**Optional:**
```bash
APP_ENV=production            # disable /docs, /redoc; enable HSTS; restrict CORS
LLM_PROVIDER=oci              # switch to OCI Generative AI (default: groq)
OKAHU_API_KEY=okahu_...       # enables Okahu Cloud tracing (production observability)
OKAHU_SERVICE_NAME=medicalrag # service name in Okahu dashboard
ENABLE_EVALUATION=true        # LLM-as-judge scoring: faithfulness, context_relevancy, answer_relevancy (async, non-blocking)
ENABLE_RAGAS_EVALUATION=true  # RAGAS per-agent scoring; requires ragas + langchain-openai packages
WORKFLOW_API_KEY=...          # Bearer token for /workflow/run endpoint
GROQ_API_KEY_2=...            # fallback keys rotated automatically on 429
GROQ_API_KEY_3=...
GROQ_API_KEY_4=...
GLOBAL_DAILY_REQUEST_LIMIT=200  # hard cap across all users, resets UTC midnight (default: 200)
DEFAULT_USER_REQUEST_LIMIT=5    # per-user request cap enforced via atomic DB update (default: 5)
LOG_LEVEL=DEBUG               # default: INFO
ALLOWED_ORIGINS=...           # comma-separated CORS origins (production only)
```

**Tuning (in config.py or env):**
```bash
MAX_DOC_CHARS=2000            # chars per document sent to LLM
MAX_DOCS_PER_PROMPT=3         # documents included per LLM call
MAX_REFLECTION_ROUNDS=1       # re-retrieval attempts (0 = no reflection)
```

## Key Design Decisions

**Why Groq + sentence-transformers?**
- Groq: free tier with 30K TPM (sufficient for demos), JSON repair built-in
- sentence-transformers: runs locally, no API calls, 384-dim embeddings fit pgvector HNSW index well

**Why reflection agent?**
- Self-critique detects missing evidence; triggers re-retrieval with semantic hints
- Improves diagnosis accuracy without multiple separate LLM calls (just one reflection round by default)

**Why pgvector HNSW (not IVFFlat)?**
- HNSW scales to any dataset size; IVFFlat requires pre-training on larger corpora
- No performance penalty at small scale; better future-proofing

**Async/await throughout:**
- Ensures all I/O operations (DB, embedding, LLM) are non-blocking
- FastAPI runs handlers concurrently; concurrent case processing is natural

## Common Tasks

**Running a subset of tests:**
```bash
# Just unit tests (fast, no DB required)
pytest -m unit

# Just integration tests (requires postgres running)
pytest -m integration

# Single test
pytest tests/test_diagnosis.py::test_diagnosis_agent_returns_structured_output -v
```

**Checking coverage:**
```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

**Testing streaming responses:**
```bash
curl -N http://localhost:8000/cases/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"symptoms": [...]}'
```

**External workflow trigger (with WORKFLOW_API_KEY):**
```bash
curl -X POST http://localhost:8000/workflow/run \
  -H "Authorization: Bearer $WORKFLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"symptoms": [...]}'
```

## Production Deployment

- Set `APP_ENV=production` to disable OpenAPI docs, enable HSTS, restrict CORS
- Docker image pre-bakes `all-MiniLM-L6-v2` embedding model (no HuggingFace download on cold start)
- All PHI endpoints use `Cache-Control: no-store` header; see `SecurityHeadersMiddleware` in main.py
- Okahu Cloud tracing requires `OKAHU_API_KEY` and `OKAHU_SERVICE_NAME` env vars
- Deployment example in `deploy/README.md` (Railway-specific but generalizes to other platforms)
