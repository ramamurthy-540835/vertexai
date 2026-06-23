-- Lead-to-POS pgvector HNSW indexes for fuzzy candidate retrieval.
--
-- Run this with psql autocommit enabled. CREATE INDEX CONCURRENTLY cannot run
-- inside BEGIN/COMMIT. The base schema file defines equivalent indexes for new
-- databases; this file is the idempotent live-database backfill path.

\set ON_ERROR_STOP on
\set hnsw_m 32
\set hnsw_ef_construction 128

SET maintenance_work_mem = '512MB';

ALTER TABLE leadmgmt.leads_embeddings
    ALTER COLUMN combined_embedding TYPE vector(768) USING combined_embedding::vector(768),
    ALTER COLUMN address_embedding TYPE vector(768) USING address_embedding::vector(768),
    ALTER COLUMN name_embedding TYPE vector(768) USING name_embedding::vector(768);

ALTER TABLE leadmgmt.pos_embeddings
    ALTER COLUMN combined_embedding TYPE vector(768) USING combined_embedding::vector(768),
    ALTER COLUMN address_embedding TYPE vector(768) USING address_embedding::vector(768),
    ALTER COLUMN name_embedding TYPE vector(768) USING name_embedding::vector(768);

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_lead_id_unique
ON leadmgmt.leads_embeddings (lead_id);

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_pos_id_unique
ON leadmgmt.pos_embeddings (pos_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_combined_hnsw
ON leadmgmt.leads_embeddings
USING hnsw (combined_embedding vector_cosine_ops)
WITH (m = :hnsw_m, ef_construction = :hnsw_ef_construction);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_combined_hnsw
ON leadmgmt.pos_embeddings
USING hnsw (combined_embedding vector_cosine_ops)
WITH (m = :hnsw_m, ef_construction = :hnsw_ef_construction);

ANALYZE leadmgmt.leads_embeddings;
ANALYZE leadmgmt.pos_embeddings;
