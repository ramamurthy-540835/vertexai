-- Lead-to-POS pgvector HNSW indexes for fuzzy candidate retrieval.
--
-- Run this with psql autocommit enabled. CREATE INDEX CONCURRENTLY cannot run
-- inside BEGIN/COMMIT. The base schema file defines equivalent indexes for new
-- databases; this file is the idempotent live-database backfill path.

\set ON_ERROR_STOP on

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_combined_hnsw
ON leadmgmt.leads_embeddings
USING hnsw (combined_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 128);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_combined_hnsw
ON leadmgmt.pos_embeddings
USING hnsw (combined_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 128);

ANALYZE leadmgmt.leads_embeddings;
ANALYZE leadmgmt.pos_embeddings;
