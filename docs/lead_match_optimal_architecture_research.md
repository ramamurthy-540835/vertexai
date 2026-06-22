# Lead Match Optimal Architecture Research

Date: 2026-06-22

## Executive Recommendation

For the current Costco lead-to-POS reporting workload, keep Cloud SQL PostgreSQL as the system of record and vector store, but fix the live database indexes and make embedding generation model-aware. Do not move to Vertex Vector Search yet for warehouse-level batch reporting. Move retrieval to Vertex Vector Search only when the workload grows into millions of active vectors per run, needs low-latency online lookup, or Cloud SQL CPU remains saturated after HNSW indexes and batching are in place.

The immediate blocker is not the number of records. The live database is missing HNSW indexes on `leads_embeddings.combined_embedding` and `pos_embeddings.combined_embedding`, even though the checked-in schema has those index definitions. Without HNSW, the fuzzy match query can degrade into repeated vector scans.

## Answer: Are We Sending 50k Records To An LLM In One Batch?

No. This pipeline calls the Vertex embeddings API, not a generative chat LLM. It sends business identity text to an embedding model and stores numeric vectors.

Current runtime behavior after the model-aware fix:

- `EMBEDDING_BATCH_SIZE=100` controls how many database rows are processed together.
- For each row batch, the runtime prepares three text fields: `combined_field`, `full_address`, and `business_name`.
- For batchable embedding models, each field can be sent in request chunks up to 250 texts, subject to token limits.
- For `gemini-embedding-001`, Google documents that each request can include only a single input text, so the runtime now splits requests to 1 text/request automatically.

For warehouse `569`:

- Live DB has `300` leads, `8,000` transactions, `300` lead embeddings, and `0` POS embeddings.
- Since lead embeddings already exist, the next embedding workload is mainly `8,000 POS rows * 3 fields = 24,000 embedding texts`.
- With `gemini-embedding-001`, that means up to `24,000` single-text embedding requests.
- With a batchable embedding model and `100` texts/request, that would be about `240` embedding requests.

For a hypothetical `50,000` row workload:

- With three embedding fields, it is `150,000` embedding texts.
- With `gemini-embedding-001`, that is up to `150,000` single-text requests.
- With a batchable model at `100` texts/request, that is about `1,500` requests.

## Current Live Infrastructure

Read-only checks performed on 2026-06-22.

### Cloud SQL

`lead-mgmt-db`:

- Project: `ctoteam`
- Region: `us-central1`
- Database: PostgreSQL 15
- State: `RUNNABLE`
- Edition: `ENTERPRISE`
- Availability: `REGIONAL`
- Tier: `db-custom-4-15360`
- Interpreted node size: `4 vCPU`, `15 GiB RAM`
- Disk: `100 GiB`, `PD_SSD`
- Backups: enabled

Approximate monthly cost for the current Cloud SQL node in `us-central1`, using public list prices and 730 hours/month:

- HA compute: `4 * $0.0826 + 15 * $0.014 = $0.5404/hour`, about `$394/month`
- HA SSD storage: `100 GiB * $0.000465753/hour`, about `$34/month`
- Backups, if 100 GiB used: about `$8/month`
- Estimated subtotal: about `$428/month`, before network, extra backup use, taxes, discounts, and committed-use discounts

### Cloud Run Jobs

Current live job metadata:

| Job | Image | CPU | Memory | Timeout |
|---|---|---:|---:|---:|
| `lead-match-lead-embeddings` | `lead-match-runtime:0ea0306` | 1 | 512 MiB | 3600s |
| `lead-match-pos-embeddings` | `lead-match-runtime:0ea0306` | 1 | 1 GiB | 7200s |
| `lead-match-fuzzy-match` | `lead-match-runtime:0ea0306` | 1 | 1 GiB | 3600s |
| `lead-match-report` | `lead-match-runtime:0ea0306` | 1 | 512 MiB | 900s |

The runtime image is still old until the GitHub Actions workflow is run with `deploy_runtime=true` and `update_cloud_run_jobs=true`.

### Live Warehouse Counts

| Warehouse | Lead rows | POS rows | Lead embeddings | POS embeddings | Match rows |
|---:|---:|---:|---:|---:|---:|
| 115 | 630 | 18,900 | 630 | 18,900 | 4,953 |
| 529 | 0 | 0 | 0 | 0 | 0 |
| 569 | 300 | 8,000 | 300 | 0 | 0 |

### Live Indexes

Current live indexes on embedding tables:

- `leads_embeddings`: `idx_leads_embeddings_period`
- `pos_embeddings`: `idx_pos_embeddings_period`
- `match_decision_detail`: primary key plus run and lead indexes

Missing live indexes:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_combined_hnsw
ON leadmgmt.leads_embeddings
USING hnsw (combined_embedding vector_cosine_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_combined_hnsw
ON leadmgmt.pos_embeddings
USING hnsw (combined_embedding vector_cosine_ops);

ANALYZE leadmgmt.leads_embeddings;
ANALYZE leadmgmt.pos_embeddings;
```

These should be created before treating fuzzy-match runtime as meaningful.

## Research Findings

### Embedding API Limits

Google documents a general text embeddings request limit of `250` input texts and `20,000` input tokens per request, with individual texts limited to `2,048` tokens. The same docs call out a special case: for `gemini-embedding-001`, each request can include only one input text.

Implication:

- `EMBEDDING_BATCH_SIZE=100` is fine as a row-processing batch size.
- It is not safe to assume `gemini-embedding-001` can receive 100 texts in one API call.
- The runtime must split API calls by model. This is now fixed locally in `lead_match_runtime/job_runner.py`.

### Cloud SQL pgvector

Google Cloud SQL for PostgreSQL supports `pgvector`. Google documents HNSW indexes for approximate nearest-neighbor searches and recommends `vector_cosine_ops` for cosine distance use cases.

Implication:

- The current architecture is viable for this data size, but only after HNSW indexes exist in the live DB.
- Warehouse `569` should not take hours once POS embeddings are created and HNSW exists.

### Vertex Vector Search

Vertex Vector Search is built for globally scalable, low-latency vector search using Google research technology. It is a good candidate when retrieval needs to handle much larger indexes, high query concurrency, or online serving outside the relational transaction path.

Implication:

- For the current batch reporting use case, moving now would add sync complexity without solving the immediate missing-index problem.
- For large production scale, split retrieval into Vertex Vector Search and keep final scoring, fiscal rules, conflict resolution, and reporting in Cloud SQL.

## Recommended Architecture

### Phase 0: Immediate Stabilization

1. Create live HNSW indexes on `leads_embeddings` and `pos_embeddings`.
2. Run `ANALYZE` after index creation and after large embedding loads.
3. Deploy the current runtime through GitHub Actions only:
   - `deploy_runtime=true`
   - `update_cloud_run_jobs=true`
   - `deploy_workflow=true`
4. Run warehouse `569` and verify:
   - POS embeddings move from `0` to `8,000`.
   - Fuzzy match logs show per-batch progress.
   - Workflow no longer waits forever after Cloud Run jobs succeed.

### Phase 1: Best Current Architecture For 569 And Similar Warehouses

Keep the current four-stage orchestration:

1. Lead embeddings and POS embeddings in parallel.
2. Fuzzy candidate retrieval and scoring.
3. Report generation.

Use Cloud SQL PostgreSQL for:

- Source-of-truth lead, account, and transaction data.
- Embedding storage.
- HNSW nearest-neighbor retrieval.
- Final match decision records.
- Manual review handoff and report output.

Use Cloud Run Jobs for:

- Idempotent embedding generation.
- Batched fuzzy matching.
- Report generation.

Use Cloud Workflows for:

- Running lead/POS embeddings in parallel.
- Serializing fuzzy match after embeddings are complete.
- Serializing report after fuzzy match is complete.
- Returning Cloud Run job execution counts.

### Phase 2: Throughput Optimization

The highest-value optimization is reducing embedding call count.

Recommended options:

1. Keep `gemini-embedding-001` for quality, but only embed `combined_field` first. Use deterministic address/name similarity, `pg_trgm`, or lightweight SQL scoring for precision on the top candidates. This changes 3 embedding calls per row to 1.
2. If three semantic fields must remain, switch to a batchable embedding model for bulk load, and set request chunks to 100 to 250 texts/request.
3. Add partitioned workers by warehouse or lead range only after quota and DB CPU are measured.

For warehouse `569`, the current `db-custom-4-15360` Cloud SQL node should be enough after HNSW indexes. The bottleneck is likely embedding API request count, not database size.

### Phase 3: When To Move To Vertex Vector Search

Move candidate retrieval to Vertex Vector Search when at least one of these becomes true:

- Active vector corpus grows past low millions and HNSW queries saturate Cloud SQL CPU.
- Search becomes online or user-facing instead of batch reporting.
- Multiple concurrent warehouse runs must execute during business hours.
- You need independent scaling of vector retrieval without scaling the transactional database.

Target split:

- Cloud SQL remains source of truth and report store.
- GCS stores embedding export snapshots.
- Vertex Vector Search stores candidate retrieval index.
- Cloud Run fuzzy scorer queries Vector Search for top K candidates, then joins details from Cloud SQL and applies fiscal/business rules.

Do not move conflict resolution or lifecycle rules out of Cloud SQL until retrieval scale requires it.

## Concrete Action Plan

### P0

- Create live HNSW indexes.
- Deploy latest runtime image and workflow through GitHub Actions.
- Run warehouse `569`.
- Confirm no `gemini-embedding-001` multi-text request errors appear.

### P1

- Add a preflight SQL check that fails when HNSW indexes are missing.
- Add summary output for:
  - lead rows needing embeddings
  - POS rows needing embeddings
  - embedding API request count estimate
  - HNSW index presence
  - Cloud SQL tier
- Consider reducing from three embeddings per row to one combined embedding plus deterministic precision scoring.

### P2

- Add Cloud SQL Query Insights checks for fuzzy-match query duration and CPU.
- Capture `EXPLAIN (ANALYZE, BUFFERS)` for one controlled fuzzy batch.
- Add warehouse-range partitioning only if HNSW plus batching is insufficient.

## Source Links

- Google text embedding API limits: https://cloud.google.com/vertex-ai/generative-ai/docs/embeddings/get-text-embeddings
- Google text embedding REST reference: https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/text-embeddings-api
- Cloud SQL pgvector and HNSW indexing: https://cloud.google.com/sql/docs/postgres/work-with-vectors
- Cloud SQL instance settings and machine sizing: https://cloud.google.com/sql/docs/postgres/instance-settings
- Cloud SQL machine series overview: https://cloud.google.com/sql/docs/postgres/machine-series-overview
- Cloud SQL pricing: https://cloud.google.com/sql/pricing
- Vertex Vector Search overview: https://cloud.google.com/vertex-ai/docs/vector-search/overview
