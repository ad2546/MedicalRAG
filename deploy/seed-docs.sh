#!/bin/bash
set -e
echo "[seed-docs] loading 721 medical documents..."
gunzip -c /seed/documents.sql.gz | psql -U postgres -d medicalrag
COUNT=$(psql -U postgres -d medicalrag -tAc "SELECT count(*) FROM documents;")
echo "[seed-docs] documents loaded: $COUNT"
# HNSW index for cosine
psql -U postgres -d medicalrag -c "CREATE INDEX IF NOT EXISTS idx_documents_embedding_hnsw ON documents USING hnsw (embedding vector_cosine_ops);" || true
echo "[seed-docs] hnsw index ensured"
