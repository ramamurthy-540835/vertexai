# Lead Match Pipeline Performance Findings

**Date:** 2026-06-22  
**Incident:** GitHub Actions run [#27909693664](https://github.com/ramamurthy-540835/vertexai/actions/runs/27909693664/job/82584564896) — warehouse 529, ran ~4 hours, killed by 240-min timeout  
**Data volume:** Warehouse 529 had **0 rows** needing embeddings; warehouse 569 (next planned) has **300 leads / 8,000 POS**

---

## Root Cause: Workflow Polling Bug (P0 — Critical)

**File:** `deploy/lead_match_workflow.yaml`, lines 147-171

The Cloud Workflow polls Cloud Run execution status every 15 seconds but reads completion fields at the **wrong nesting level**. The `googleapis.run.v2.projects.locations.jobs.executions.get` API returns fields under nested objects, not at the top level:

| Field checked in workflow | Actual location in API response |
|---|---|
| `execution.succeededCount` | `execution.status.succeededCount` |
| `execution.taskCount` | `execution.spec.taskCount` |
| `execution.failedCount` | `execution.status.failedCount` |
| `execution.cancelledCount` | `execution.status.cancelledCount` |

Because `map.get(execution, "succeededCount")` always returns `null`, the `default(...)` fallback produces `0 >= 1` which is always false. The workflow never detects completion and loops forever.

**Evidence:** The monitor workflow (`.github/workflows/lead_match_monitor.yml`, lines 456-479) already handles this correctly — it checks both `j.get("succeededCount")` and `status.get("succeededCount")`.

**Impact:** Every workflow run is broken regardless of data volume. Warehouse 529 had 0 rows, both Cloud Run jobs finished in ~66 seconds, yet the workflow stayed ACTIVE for 5+ hours.

**Fix:** In `waitForRunJobExecution`, read from the correct nested paths. Also update the `returnResult` block (lines 142-144) which has the same problem when extracting `taskCount`, `succeededCount`, `failedCount`.

---

## Performance Bottlenecks for Larger Warehouses

Once the polling bug is fixed, these are the areas that will matter for warehouse 569 (300 leads, 8,000 POS) and beyond.

### 1. Embedding Generation — Batch Size and API Throughput (P1)

**File:** `lead_match_runtime/job_runner.py`, lines 31-35, 309-389 (leads), 391-472 (POS)

**Current settings:**
- Batch size: **25 rows** per API call (`EMBEDDING_BATCH_SIZE`)
- Max workers: **3** (parallel field-level calls per batch, not batch-level)
- Retry: 6 attempts, exponential backoff 1.5s base, 90s cap

**For warehouse 569 (300 leads, 8,000 POS):**
- Lead embeddings: 300 / 25 = **12 batches**, each batch makes 3 parallel API calls = **36 API calls total**
- POS embeddings: 8,000 / 25 = **320 batches**, each batch makes 3 parallel API calls = **960 API calls total**
- Batches are processed **sequentially** (only field-level parallelism within a batch)

**Estimated time at ~0.5s per API call:**
- Lead embeddings: ~12 batches x 0.5s = ~6 seconds (fast)
- POS embeddings: ~320 batches x 0.5s = **~160 seconds (~2.7 minutes)**
- With rate-limit retries (429s), this could stretch to 5-10 minutes

**Improvements:**
- Increase `EMBEDDING_BATCH_SIZE` from 25 to 100-250 (Vertex AI supports up to 250 texts per call for `gemini-embedding-001`)
- Add batch-level parallelism: process multiple batches concurrently with a semaphore, not just field-level within one batch
- Pre-check row count and skip the job entirely if 0 rows (saves container startup time)

### 2. Fuzzy Match SQL — Single Monolithic Query (P1)

**File:** `lead_match_runtime/job_runner.py`, lines 475-616

**Current approach:** One giant CTE query that does everything in a single SQL execution:
1. `lead_batch` — selects all leads (up to 1M limit)
2. `candidates` — `CROSS JOIN LATERAL` against POS, top 20 per lead by cosine similarity
3. `scored` — weighted score: `(4 * address_score + 3 * name_score) / 7`
4. `ranked` — filter by qualify_min_score (78)
5. `best_unique_pos` — deduplicate, flag ambiguity (delta < 3)
6. `INSERT INTO match_decision_detail`

**For warehouse 569:**
- 300 leads x 20 nearest neighbors = **6,000 candidate pairs** evaluated
- Single SQL execution, no progress reporting, no intermediate checkpoints

**For larger warehouses (e.g., 1,000 leads x 10,000 POS):**
- 1,000 x 20 = **20,000 candidate pairs**
- The `CROSS JOIN LATERAL` performs a full index scan of POS per lead row
- pgvector `<=>` (cosine distance) with 768-dimension vectors is CPU-intensive
- No timeout protection — a slow query runs until Cloud Run's container timeout

**Improvements:**
- Add a `statement_timeout` to protect against runaway queries
- Consider batching the match in chunks of leads (e.g., 100 at a time) with progress logging
- Ensure pgvector IVFFlat or HNSW index exists on `pos_embeddings.combined_embedding` scoped by `warehouse_number` — without it, every lateral subquery does a sequential scan
- Add EXPLAIN ANALYZE logging for the first batch to catch missing indexes early

### 3. No Progress Reporting During Match Phase (P2)

**File:** `lead_match_runtime/job_runner.py`, lines 502-610

The fuzzy match runs as a single `cursor.execute()` call with no intermediate output. For large warehouses, operators see the Cloud Run container running but cannot distinguish "still computing" from "hung."

**Improvement:** Split the match into lead batches and log progress after each batch, similar to the embedding job's batch logging (lines 378-383).

### 4. Workflow Call Logging Level (P2)

**File:** `deploy/lead_match_workflow.yaml` / `.github/workflows/lead_match_semantic_workflow.yml`

The workflow is deployed with `--call-log-level=log-errors-only`. Because the polling bug caused an infinite loop (not an error), no diagnostic logs were emitted. This made investigation harder.

**Improvement:** Use `log-all-calls` during development/testing; switch to `log-errors-only` only for production-stable runs.

### 5. GitHub Actions Timeout (P3)

**File:** `.github/workflows/lead_match_semantic_workflow.yml`, line 61

`timeout-minutes: 240` (4 hours). This is the backstop that eventually killed the stuck workflow. Once the polling bug is fixed, this timeout is appropriate for large warehouses but should be reviewed.

---

## Estimated Runtimes After Fix (Warehouse 569)

| Phase | Records | Current Config | Estimated Time | With Improvements |
|---|---|---|---|---|
| Lead embeddings | 300 rows | batch=25, workers=3 | ~6s | ~2s (batch=100) |
| POS embeddings | 8,000 rows | batch=25, workers=3 | ~3-10 min | ~30s-1 min (batch=250, batch parallelism) |
| Fuzzy match | 300 x 8,000 | single SQL, top-20 | ~10-60s (depends on index) | ~10-30s (with HNSW index) |
| Report | 1 query | - | ~5s | ~5s |
| **Total** | | | **~4-11 min** | **~1-2 min** |

Note: The workflow overhead (polling every 15s per phase) adds ~30-60s per phase.

---

## Priority Action Items

| Priority | Item | Where to Fix |
|---|---|---|
| **P0** | Fix workflow polling field paths (`succeededCount` -> `status.succeededCount`, etc.) | `deploy/lead_match_workflow.yaml:147-174` |
| **P0** | Fix workflow return block field paths | `deploy/lead_match_workflow.yaml:142-144` |
| **P1** | Increase embedding batch size (25 -> 100-250) | `job_runner.py:31` or env var `EMBEDDING_BATCH_SIZE` |
| **P1** | Add batch-level parallelism for embeddings | `job_runner.py:322` (sequential loop -> concurrent batches) |
| **P1** | Verify pgvector index on `pos_embeddings.combined_embedding` | Cloud SQL schema / migration |
| **P2** | Add progress logging to fuzzy match (batch leads) | `job_runner.py:475-616` |
| **P2** | Add SQL statement_timeout for match query | `job_runner.py:502` |
| **P2** | Switch call-log-level to log-all-calls for debugging | Workflow deploy command |
| **P3** | Cancel the stuck workflow execution | `gcloud workflows executions cancel 4382f086-152b-4967-8820-d841b7df0193 --workflow=lead_match_workflow --location=us-central1 --project=ctoteam` |
