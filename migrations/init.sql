-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Medical evidence documents
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content         TEXT NOT NULL,
    embedding       VECTOR(384),          -- all-MiniLM-L6-v2 → 384 dims
    source          TEXT,
    disease_category TEXT,
    evidence_type   TEXT,                 -- e.g. 'guideline', 'case_report', 'review'
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ANN index for cosine similarity search (HNSW — no pre-training needed, works at any dataset size)
CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index for metadata-based filtering
CREATE INDEX IF NOT EXISTS documents_disease_category_idx
    ON documents (disease_category);

-- Patient cases
CREATE TABLE IF NOT EXISTS cases (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symptoms    JSONB NOT NULL,
    vitals      JSONB,
    history     JSONB,
    labs        JSONB,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Retrieval audit log
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id             UUID NOT NULL REFERENCES cases(id),
    query               TEXT NOT NULL,
    retrieved_doc_ids   UUID[] NOT NULL,
    scores              FLOAT[] NOT NULL,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS retrieval_logs_case_id_idx ON retrieval_logs (case_id);

-- Diagnosis outputs (initial / reflection / final)
CREATE TABLE IF NOT EXISTS outputs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id     UUID NOT NULL REFERENCES cases(id),
    stage       VARCHAR(50) NOT NULL,    -- 'initial' | 'reflection' | 'final'
    diagnosis   JSONB NOT NULL,
    reasoning   TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS outputs_case_id_idx ON outputs (case_id);
CREATE INDEX IF NOT EXISTS outputs_stage_idx   ON outputs (stage);

-- Users for secure auth and request quota tracking
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    password_salt   VARCHAR(255) NOT NULL,
    request_limit   INTEGER NOT NULL DEFAULT 5,
    requests_used   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS users_email_idx ON users (email);
