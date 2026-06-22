# Lead Match Pipeline — Architecture Deep Analysis

**Date:** 2026-06-22  
**Scope:** End-to-end performance analysis, Cloud SQL sizing, embedding API costs, and architecture alternatives

---

## 1. Current Architecture Overview

```
GitHub Actions (trigger + monitor)
       │
       ▼
Google Cloud Workflows (orchestrator)
       │
       ├──► Cloud Run Job: lead-match-lead-embeddings
       ├──► Cloud Run Job: lead-match-pos-embeddings   (parallel)
       │
       ├──► Cloud Run Job: lead-match-fuzzy-match      (after embeddings)
       │
       └──► Cloud Run Job: lead-match-report           (after match)
       │
       ▼
Cloud SQL PostgreSQL + pgvector (lead-mgmt-db, us-central1)
```

**Data scale per warehouse:**

| Warehouse | Leads | POS Records |
|---|---|---|
| 529 (test) | 0 needing embeddings | 0 needing embeddings |
| 569 (planned) | 300 | 8,000 |
| Typical large | 500–1,000 | 10,000–50,000 |
| Full deployment | ~600 warehouses | Millions of POS total |

---

## 2. What Happens Per Batch — Actual Numbers

### Embedding Phase

Each Cloud Run embedding job processes database rows in batches, then splits the actual Vertex AI request size by embedding model:

| Setting | Value | Source |
|---|---|---|
| `EMBEDDING_BATCH_SIZE` | 100 database rows per processing batch | `job_runner.py` |
| `EMBEDDING_MAX_TEXTS_PER_REQUEST` | Default: up to 100 texts per API call (auto from batch size, capped at 250). Override via env var. | `job_runner.py` |
| `EMBEDDING_MAX_WORKERS` | 3 field-level workers | `job_runner.py` |
| `EMBEDDING_BATCH_WORKERS` | Default 3 in code, workflow sets 3 for row-batch concurrency | `job_runner.py` / GitHub Actions |
| `EMBEDDING_REQUEST_LOG_EVERY` | 100 | GitHub Actions |
| Embedding model | `gemini-embedding-001` | `lead_to_pos_match_rules.json:46` |
| Output dimensions | 768 | `lead_to_pos_match_rules.json:48` |
| Task type | `SEMANTIC_SIMILARITY` | `lead_to_pos_match_rules.json:47` |

**Per row batch cycle (100 records):**
1. Fetch 100 rows from Cloud SQL
2. Build 3 text representations per row: `combined_field`, `full_address`, `business_name`
3. Send the three fields through the embedding request splitter
4. Receive 3 x 100 = 300 vectors (768-dim each)
5. INSERT 100 rows into `leads_embeddings` or `pos_embeddings`

**Clarification:** Google's documentation confirms that `gemini-embedding-001` **does** accept a list of strings in a single `embed_content` call and returns individual embeddings for each string. (This is different from `gemini-embedding-2` which produces a single *aggregated* embedding.) So with `EMBEDDING_BATCH_SIZE=100`, each API call sends 100 texts and receives 100 vectors back.

Row-batch concurrency is configurable with `EMBEDDING_BATCH_WORKERS` (default 3).
With 3 row-batch workers and 3 field workers each, up to 9 concurrent embedding
API calls can be in flight. This is well within the default 1,500 RPM quota.

**We are NOT sending 50K records in one batch.** For warehouse 569:
- Lead embeddings already exist in the live DB: 300 lead embeddings present
- POS embeddings are missing: 8,000 POS rows x 3 fields at 100 texts/request = **~240 API calls**
- With 3 batch workers x 3 field workers = up to 9 concurrent calls, throughput ~18 calls/sec

For a large warehouse (50K POS):
- 50,000 rows x 3 fields at 100 texts/request = **~1,500 API calls**
- With 3 batch workers x 3 field workers, completes in **~12–25 minutes**

### Fuzzy Match Phase

| Setting | Value | Source |
|---|---|---|
| `MATCH_BATCH_SIZE` | 100 leads per SQL batch | `job_runner.py:36` |
| `MATCH_STATEMENT_TIMEOUT_MS` | 900,000 ms (15 min) | `job_runner.py:37` |
| Recall gate | 65% cosine similarity | `lead_to_pos_match_rules.json:73` |
| Nearest neighbor limit | 20 candidates per lead | `lead_to_pos_match_rules.json:74` |
| Qualify minimum | 78% final score | `lead_to_pos_match_rules.json:85` |

**Per match batch (100 leads):**
1. Fetch 100 lead IDs (cursor-based pagination by `lead_id`)
2. Execute single CTE query with `CROSS JOIN LATERAL`:
   - For each of 100 leads, search all POS in same warehouse via HNSW index
   - Return top 20 POS candidates per lead by cosine similarity
   - Score: `(4 * address_score + 3 * name_score) / 7`
   - Filter by qualify_min (78%), deduplicate, flag ambiguous (delta < 3)
3. INSERT matches into `match_decision_detail`

**Vector comparisons per batch:**
- 100 leads x top-20 from HNSW = ~2,000 candidate pairs scored
- HNSW should avoid a brute-force scan when the index exists and the planner uses it

---

## 3. Estimated Runtimes

### Warehouse 569 (300 leads, 8,000 POS)

The live DB currently has `300` lead embeddings and `0` POS embeddings for warehouse `569`, so the next run mostly spends time creating POS embeddings.

| Phase | Work | API Calls | Estimated Time |
|---|---|---|---|
| Lead embeddings | Already present | 0 | ~0 seconds |
| POS embeddings | 80 row batches x 3 fields, 100 texts/call | ~240 | ~1–2 minutes (with 3 batch workers) |
| Fuzzy match (requires live HNSW indexes) | 3 SQL batches | 0 | ~5–30 seconds |
| Report | 1 query | 0 | ~5 seconds |
| **Total** | | **~240 requests** | **~2–3 minutes** |

### Large Warehouse (1,000 leads, 50,000 POS)

| Phase | Work | API Calls | Estimated Time |
|---|---|---|---|
| Lead embeddings | 10 row batches x 3 fields | ~30 | ~15 seconds |
| POS embeddings | 500 row batches x 3 fields | ~1,500 | ~8–15 minutes (with 3 batch workers) |
| Fuzzy match (requires live HNSW indexes) | 10 SQL batches | 0 | ~30–120 seconds |
| Report | 1 query | 0 | ~5 seconds |
| **Total** | | **~1,530 requests** | **~10–18 minutes** |

**POS embedding is the bottleneck** due to the number of row batches. Batch-level concurrency (3 workers) overlaps API calls to reduce wall-clock time by ~2–3x vs sequential processing.

---

## 4. Cloud SQL — Current Setup and Costs

### Instance Details

| Property | Value | Source |
|---|---|---|
| Instance name | `lead-mgmt-db` | `job_runner.py:38` |
| Connection | `ctoteam:us-central1:lead-mgmt-db` | `job_runner.py:38` |
| Region | `us-central1` | live `gcloud sql instances describe` |
| Database | PostgreSQL 15 + pgvector | live query / schema SQL |
| Edition | Enterprise | live `gcloud sql instances describe` |
| Availability | Regional HA | live `gcloud sql instances describe` |
| Disk | 100 GiB PD_SSD | live `gcloud sql instances describe` |
| Tier | `db-custom-4-15360` | live `gcloud sql instances describe` |
| Node size | 4 vCPU, 15 GiB RAM | Cloud SQL custom tier convention |

### Vector Storage Requirements

Each record stores 3 vectors of 768 dimensions (4 bytes each):

| Component | Per Record | 50K POS | 500K POS (all warehouses) |
|---|---|---|---|
| Raw vectors (3 x 768 x 4B) | 9.2 KB | ~460 MB | ~4.6 GB |
| HNSW index (combined only) | ~3.3 KB | ~165 MB | ~1.65 GB |
| HNSW index (all 3 fields) | ~9.9 KB | ~495 MB | ~4.95 GB |
| Text columns + metadata | ~1 KB | ~50 MB | ~500 MB |
| **Total per table** | ~20 KB | **~1 GB** | **~10 GB** |

With leads + POS tables combined:

| Scale | Storage | Index Memory Needed |
|---|---|---|
| Single warehouse (300 + 8K) | ~200 MB | ~200 MB |
| 10 warehouses | ~2 GB | ~2 GB |
| 100 warehouses | ~20 GB | ~15–20 GB |
| 600 warehouses (full) | ~100–120 GB | ~80–100 GB |

### Current Cost Estimate

Using Google Cloud SQL us-central1 public pricing and 730 hours/month:

| Component | Estimate |
|---|---:|
| Regional HA compute, 4 vCPU + 15 GiB RAM | about $394/month |
| Regional HA SSD storage, 100 GiB | about $34/month |
| Backups, if 100 GiB used | about $8/month |
| **Estimated subtotal** | **about $428/month** |

This excludes network, extra backup usage, taxes, discounts, and committed-use discounts.

### Recommended Cloud SQL Tiers

| Tier | vCPUs | RAM | Availability | Approx Monthly Cost | Good For |
|---|---:|---:|---|---:|---|
| db-custom-2-8192 | 2 | 8 GiB | Zonal | Lower-cost single warehouse testing | Dev/test |
| **db-custom-4-15360** | **4** | **15 GiB** | **Regional HA** | **~$428 current estimate** | **Current production test footprint** |
| db-custom-8-32768 | 8 | 32 GiB | Regional HA | Higher | Larger multi-warehouse runs |
| db-custom-16-65536 | 16 | 64 GiB | Regional HA | Higher | Full deployment with headroom |

**Key tuning for pgvector:**
- `maintenance_work_mem = '4GB'` — for HNSW index builds
- `max_parallel_maintenance_workers = 7` — parallel index creation
- `SET hnsw.ef_search = 100` — default 40 is too low for top-20 recall
- HNSW index params: `m = 16`, `ef_construction = 128` (default 64 is too low)

### Current HNSW Indexes

The checked-in schema defines HNSW indexes for `combined_embedding`, but the live database does **not** currently have those HNSW indexes. Live indexes found:

- `idx_leads_embeddings_period`
- `idx_pos_embeddings_period`
- `idx_mdd_run`
- `idx_mdd_lead_id`
- `match_decision_detail_pkey`

Missing live indexes to create are now captured in `schema/lead_match_hnsw_indexes.sql`.
Run that file with `psql` autocommit enabled because `CREATE INDEX CONCURRENTLY`
cannot run inside `BEGIN/COMMIT`:

```sql
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
```

The `address_embedding` and `name_embedding` fields used in scoring do not need HNSW indexes initially. They are computed only after the `combined_embedding` recall step selects candidates. That is the right shape.

---

## 5. Vertex AI Embedding API — Costs and Limits

### Pricing (gemini-embedding-001)

| Method | Cost per 1M tokens | Notes |
|---|---|---|
| Online (current) | **$0.15** | Real-time; request shape depends on model, and `gemini-embedding-001` is single-text per request |
| Batch Prediction API | **$0.075** | 50% cheaper, async, for bulk re-embeds |

### Limits

| Quota | Default | Can Increase? |
|---|---|---|
| General text embedding request | Up to 250 input texts and 20,000 input tokens | Google documented limit |
| Individual text | 2,048 tokens before truncation | Google documented limit |
| `gemini-embedding-001` request | Single input text only | Google documented special case |
| Throughput quota | Project/model/region specific | Check Cloud Console quotas |

### Cost Per Warehouse

Average ~30 tokens per text (business name + address):

| Warehouse Size | Records x Fields | Tokens | Cost (Online) | Cost (Batch API) |
|---|---|---|---|---|
| 569 (300 + 8K) | 8,300 x 3 = 24,900 | ~747K | **$0.11** | $0.06 |
| Large (1K + 50K) | 51,000 x 3 = 153,000 | ~4.6M | **$0.69** | $0.35 |
| All 600 warehouses | ~30M x 3 = ~90M | ~2.7B | **$405** | $203 |

Embedding costs are negligible at this scale. The bottleneck is request throughput. With `gemini-embedding-001`, request count is much higher than the original table implied because the model accepts one text per request.

---

## 6. Architecture Alternatives Evaluated

### Option A: Current — Cloud SQL + pgvector (Recommended to Keep)

| Pros | Cons |
|---|---|
| Already working | HNSW memory scales linearly |
| Simple architecture | Single-node bottleneck at 600 warehouses |
| pgvector handles per-warehouse scale well | No auto-scaling |
| Lowest complexity because data, vectors, scoring, and reports remain in one database | Index rebuilds slow for full dataset |
| SQL-native matching logic | |

**Best for:** Current scale (per-warehouse batch processing, <500K vectors in query scope).

**Current cost:** The live regional HA Cloud SQL instance is closer to `$428/month` before discounts, not the earlier `$140/month` zonal-style estimate.

### Option B: AlloyDB + ScaNN

| Pros | Cons |
|---|---|
| 6x faster vector queries (ScaNN) | 2–3x more expensive ($330–430/mo minimum) |
| 10x faster filtered vector search | No micro tier (minimum 2 vCPU) |
| 16x faster index creation | Migration effort |
| Built-in HA | Overkill for current scale |
| Scales to 10B vectors | |

**Best for:** When you need cross-warehouse search, >1M vectors in scope, or real-time matching.

**Cost:** likely higher than the current Cloud SQL regional HA footprint; confirm with the pricing calculator before migration.

### Option C: Vertex AI Vector Search (Matching Engine)

| Pros | Cons |
|---|---|
| Sub-10ms latency | Always-on nodes ($70–800+/mo) |
| Scales to billions of vectors | Cannot run SQL matching logic |
| Fully managed | Pay even when idle |
| | Would require major rewrite |

**Best for:** Real-time serving at high QPS (thousands of queries/second). Not for batch jobs.

**Verdict:** Overkill. Your pipeline runs batch jobs — paying for 24/7 nodes that are used once a day is wasteful.

### Option D: BigQuery VECTOR_SEARCH

| Pros | Cons |
|---|---|
| Serverless, no infra to manage | Higher per-query latency (seconds) |
| Good for analytics on results | No CROSS JOIN LATERAL pattern |
| Pay per query | Requires Enterprise edition for indexes |
| | Data sync complexity |

**Best for:** If data already lives in BigQuery and you need ad-hoc analytics.

**Verdict:** Wrong tool for iterative batch matching with per-warehouse filtering.

### Recommendation Matrix

| Scale | Recommendation | Monthly Cost |
|---|---|---|
| 1–10 warehouses (dev/test) | Cloud SQL zonal test instance | Use pricing calculator |
| 10–100 warehouses | **Current Cloud SQL regional HA `db-custom-4-15360` after HNSW indexes** | **~$428/month current estimate** |
| 100–600 warehouses | Cloud SQL larger tier or AlloyDB proof of concept | Benchmark first |
| 600+ warehouses or cross-warehouse search | AlloyDB or Vertex Vector Search split architecture | Pricing calculator |
| Real-time API serving | Add Vertex Vector Search candidate retrieval | Pricing calculator |

---

## 7. Performance Improvements — Prioritized

### Tier 1: Quick Wins (No Architecture Change)

| # | Change | Impact | Effort |
|---|---|---|---|
| 1 | Create missing live HNSW indexes | Prevent repeated vector scans | `schema/lead_match_hnsw_indexes.sql` |
| 2 | Keep model-aware request splitting | Prevent invalid multi-text Gemini embedding calls | Done in runtime |
| 3 | Evaluate a batchable embedding model for bulk loads | Reduces 24,000 Gemini requests for 569 to about 240 requests | Model/config decision |
| 4 | Add batch-level concurrency only after quota check | Faster embeddings without blindly exceeding quota | Configurable; workflow starts at 2 |
| 5 | Set `hnsw.ef_search = 100` at session start | Better top-20 recall | Done in runtime and workflow env |

### Tier 2: Medium Effort

| # | Change | Impact | Effort |
|---|---|---|---|
| 6 | Use Vertex AI Batch Prediction for full re-embeds | 50% cost savings on bulk operations | New code path |
| 7 | Add HNSW index params (`ef_construction=128`) and rebuild | Better recall quality | Schema migration |
| 8 | Add `EXPLAIN ANALYZE` logging for first match batch | Catch missing indexes / seq scans early | A few lines in `run_fuzzy_match()` |
| 9 | Consider reducing dimensions to 256 | 3x less memory, 3x faster distance computation, slight accuracy tradeoff | Requires re-embedding + testing |

### Tier 3: Architecture Evolution (When Needed)

| # | Change | Impact | Effort |
|---|---|---|---|
| 10 | Migrate to AlloyDB with ScaNN | 6–10x faster vector search | Major migration |
| 11 | Partition embedding tables by warehouse_number | Better query locality for filtered searches | Schema redesign |
| 12 | Add Cloud Run job concurrency (process multiple warehouses in parallel) | Linear speedup for multi-warehouse runs | Workflow redesign |

---

## 8. Embedding Dimension Tradeoff

Reducing from 768 to 256 dimensions is worth evaluating:

| Dimension | Vector Size | HNSW Memory (50K) | Distance Compute | Quality |
|---|---|---|---|---|
| 768 (current) | 3,072 bytes | ~495 MB (3 indexes) | Baseline | Baseline |
| 256 | 1,024 bytes | ~165 MB (3 indexes) | ~3x faster | ~2–5% lower similarity accuracy |

For short texts (business names + addresses, ~30 tokens), 256 dimensions often capture sufficient semantic information. This should be tested on a validation set before deploying.

---

## 9. Summary

**Why warehouse 529 took 5 hours:** Not data volume (0 rows). The Cloud Workflow polling bug made it loop forever. This is fixed in commit `ce32ed8`.

**Is 50K going to the LLM in one batch?** No. This is an embedding pipeline, not a generative chat LLM. Records are processed in row batches of 100, and actual API requests are split by model. For `gemini-embedding-001`, that means one text per request.

**Current architecture is correct for this scale.** Cloud SQL + pgvector with HNSW indexes handles per-warehouse matching well. The main improvements are operational:
1. Create the missing live HNSW indexes
2. Keep embedding request size model-aware
3. Use a batchable embedding model or batch prediction path for bulk backfills
4. Add batch-level parallelism only after checking quotas
5. Tune HNSW search parameters

**When to evolve:** If cross-warehouse matching or >1M vector scope becomes needed, migrate to AlloyDB with ScaNN. Until then, tune the current stack.
