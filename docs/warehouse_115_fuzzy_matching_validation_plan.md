# Costco Lead-to-POS Matching – Fuzzy Matching Validation Approach for Warehouse 115

**Version:** 1.0  
**Date:** June 18, 2026  
**Status:** Draft for Review  
**Authors:** Arun Kumar G, Madhu Vamsi Turaka, Ramamurthy Valavandan

---

## 1. Objective

Validate the effectiveness and business value of activating the **Semantic Fuzzy Matching** layer (powered by `gemini-embedding-001` + pgvector HNSW) as a secondary matching pass on top of the existing deterministic exact matching for **Warehouse 115**.

The goal is to measure the incremental lift in:
- Close Match rate
- Potential Match quality
- Overall lead-to-POS conversion attribution
- Reduction in manual review volume

This validation will be performed in a controlled manner without modifying the active production pipeline initially.

---

## 2. Current Production Matching Logic (as of June 2026)

### 2.1 Active Path: Deterministic Exact Matching

- **Primary Script:** `primary_matching.py` (inside `lead-match-job`)
- **Mechanism:** Warehouse-batched exact + deterministic scoring on normalized fields (Membership Number, Address, Name, etc.).
- **Status:** Fully active and triggered daily via Cloud Workflows.
- **Classification/Thresholds**:
  - Close Match / Match (Direct Match, e.g. Exact Membership Number, clean matched address)
  - Potential Match (Partial alignments that require manual review)
  - Close Existing (Detection of already established stubs)

### 2.2 Bypassed but Production-Ready Path: Semantic Fuzzy Matching

- **Primary Script:** `fuzzy_matching_sql.py`
- **Embedding Model:** `gemini-embedding-001` (768 dimensions)
- **Vector Store:** Cloud SQL PostgreSQL + pgvector
- **Index:** HNSW (`vector_cosine_ops`)
- **Status:** Fully provisioned (tables, indexes, embedding generation jobs, and code exist) but **not invoked** by the current `lead_match_workflow`.
- **Embedding Generation:** Handled by `vector_db_loading_pos.py` and `vector_db_loading_leads.py` with checkpointing and rate-limit handling.

**Current Reality:**  
The system runs only on exact matching. Semantic matching exists as a dormant but production-ready capability.

---

## 3. Exact Match Baseline (Warehouse 115)

Before enabling semantic matching, establish the following baseline metrics for Warehouse 115:

| Metric | Description | How to Measure |
|--------|-------------|----------------|
| Total POS Records | Count of POS transactions for Warehouse 115 in the analysis period | SQL on `transaction` table |
| Total Leads | Count of eligible leads (Open, Closed Cold, Closed Match) for Warehouse 115 | SQL on `lead` table |
| Exact Close Matches | Leads that received Close Match via deterministic logic | Count from `match_audit` or result tables |
| Exact Potential Matches | Leads routed to manual review via deterministic logic | Count from `match_audit` |
| Exact Match Rate | (Close Matches + High-quality Potentials) / Total Eligible Leads | Calculated |
| Manual Review Volume | Number of Potential Matches requiring analyst action | From ServiceNow queue or audit logs |

---

## 4. Warehouse 115 Validation Scope

- **Warehouse:** 115 only (controlled scope for initial validation)
- **Time Window:** Last 3–6 fiscal periods (to be confirmed with business)
- **Lead Statuses:** Open, Closed Cold, Closed Match
- **POS Records:** All unconsumed + historical for Close Existing detection
- **Embedding Model:** `gemini-embedding-001` (utilizing Google Vertex AI Text Embeddings)
- **Vector Store:** Cloud SQL PostgreSQL pgvector (current production)
- **Index Type:** HNSW (Hierarchical Navigable Small World) with cosine similarity matching

---

## 5. Dataset Details

The dataset represents all physical transactions and leads associated with Warehouse 115:

| Entity | Table | Filter for Warehouse 115 |
|--------|-------|--------------------------|
| Leads | `lead` | `warehouse_number = '115'` + eligible statuses |
| POS Transactions | `transaction` | `warehouse_number = '115'` + `is_processed = false` (for active matching) |
| Lead Embeddings | `leads_embeddings` | `warehouse_number = '115'` |
| POS Embeddings | `pos_embeddings` | `warehouse_number = '115'` |

---

## 6. Validation Phases

### Phase 1: Baseline Execution (Exact Matching Only)
*   **Step 1:** Run the existing deterministic matching logic (`primary_matching.py`) for the Warehouse 115 scope.
*   **Step 2:** Extract match statistics from `match_audit`.
*   **Step 3:** Record exact match counts, manual potential match counts, and no-match residuals.

### Phase 2: Semantic Matching Execution (Shadow Mode)
*   **Step 1:** Execute `fuzzy_matching_sql.py` isolated only to leads from Warehouse 115 that failed to establish a 'Close Match' in Phase 1.
*   **Step 2:** Retrieve candidate rows from `pos_embeddings` using the optimized `CROSS JOIN LATERAL` nearest-neighbor search.
*   **Step 3:** Apply the weighted similarity scoring formula:
    $$\text{Similarity Score} = \frac{\text{Combined Score} + (4 \times \text{Address Score}) + (3 \times \text{Name Score})}{8}$$
*   **Step 4:** Classify outcomes into confidence buckets based on `match_configuration` thresholds.
*   **Step 5:** Save shadow results to an isolated GCS verification path. Do **not** write back to ServiceNow or modify PostgreSQL transactional flags yet.

### Phase 3: Comparative Analysis
*   **Step 1:** Consolidate records matched by the semantic pass and sample the pairs.
*   **Step 2:** Run audit checks to verify match quality and pinpoint false-positive risks.
*   **Step 3:** Measure the percentage lift in matching success rates.

### Phase 4: Controlled Writeback Pilot
*   **Step 1:** Select a small cohort of highest-confidence semantic matches (e.g. Similarity Score $\ge 95$).
*   **Step 2:** Manually trigger writebacks of these matched records to ServiceNow via OAuth 2.0 + PSC.
*   **Step 3:** Track the success of the sync, validating final pipeline integrity.

---

## 7. Comparative Analysis Metrics

| Metric | Exact Only (Baseline) | Exact + Semantic | Delta / Lift |
|--------|-----------------------|------------------|--------------|
| Close Match Count | - | - | - |
| Potential Match Count | - | - | - |
| No Match Count | - | - | - |
| Manual Review Volume | - | - | - |
| Average Confidence Score | - | - | - |
| False Positive Rate (sampled) | - | - | - |

---

## 8. Performance Metrics

To ensure production compatibility, we will measure:
- **Embedding Generation Latency**: Processing duration per batch using `text-embedding-004` API endpoints.
- **Vector Search Execution**: SQL execution latency of `CROSS JOIN LATERAL` queries with HNSW enabled.
- **End-to-End Runtime**: Comparison of pipeline run durations with vs. without the semantic pass.
- **Cloud SQL Utilization**: Server performance monitoring (CPU, RAM, IOPS) during validation batches.

---

## 9. Cloud SQL Capacity Considerations

The active database is a Cloud SQL PostgreSQL 15 Instance (`db-custom-4-15360` with 4 vCPUs and 15 GB RAM).

**Validation Capacity Protocols:**
1.  **Off-Peak Execution:** All validation queries and scripts will run during off-peak hours.
2.  **Resource Limits:** Set conservative memory allocations inside the validation session (`work_mem` and `maintenance_work_mem`) to protect production workloads.
3.  **HNSW Efficiency**: Verify query execution plans with `EXPLAIN ANALYZE` to ensure index-backed HNSW traversals are taking place, preventing costly sequential full-table vector scans.

---

## 10. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Production Cloud SQL Performance Degradation | Medium | High | Run strictly off-peak, monitor pg_stat_activity, and ensure queries utilize HNSW indexes. |
| Model API Rate-Limitation / Quota Exhaustion | Low | Medium | Utilize the built-in batch processing, sliding windows, and checkpointing logic present in the codebase. |
| Semantic False Positives | Medium | Medium | Maintain highly conservative thresholds ($\ge 90\%$) for automatic writeback, routing lower similarity scores to human review. |
| Pipeline State Contamination | Low | High | Run Phase 2 and 3 in **Shadow Mode**, storing outputs in separate testing directories in GCS without writing back to ServiceNow or marking transactions as processed. |

---

## 11. Next Steps

1.  **Plan Approval:** Present this document to Arun Kumar G, Madhu Vamsi Turaka, and Ramamurthy Valavandan for review.
2.  **Baseline Extraction:** Run baseline exact queries for Warehouse 115.
3.  **Shadow Execution:** Run a controlled, off-peak fuzzy matching script on the Warehouse 115 dataset.
4.  **Comparative Reporting:** Deliver a comparative review report detailing the exact metrics and recommendation for full automated pipeline activation.

---

## 12. Owners / Reviewers

-   **Arun Kumar G** (Reviewer / Approver)
-   **Madhu Vamsi Turaka** (Reviewer / Approver)
-   **Ramamurthy Valavandan** (Reviewer / Approver)

---

## 13. Test Commands & Validation SQL (Warehouse 115)

Use the following queries (running under the target environment schema, e.g. `lead_mgmt_spt`) to establish baseline datasets and capacity metrics:

```sql
-- 1. Count POS records for Warehouse 115
SELECT COUNT(*) FROM "$SCHEMA_NAME".transaction 
WHERE warehouse_number = 115;

-- 2. Count eligible leads for Warehouse 115
SELECT COUNT(*) FROM "$SCHEMA_NAME".lead 
WHERE warehouse_number = 115 
  AND lead_status IN ('Open', 'Closed - Match', 'Closed - Cold');

-- 3. Check pos_embeddings count for Warehouse 115
SELECT COUNT(*) FROM "$SCHEMA_NAME".pos_embeddings 
WHERE warehouse_number = 115;

-- 4. Check leads_embeddings count for Warehouse 115
SELECT COUNT(*) FROM "$SCHEMA_NAME".leads_embeddings 
WHERE warehouse_number = 115;

-- 5. Check table, index, and TOAST sizes (important for capacity planning)
SELECT 
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_size_pretty(pg_relation_size(relid)) AS table_only_size,
    pg_size_pretty(pg_indexes_size(relid)) AS indexes_only_size,
    pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid) - pg_indexes_size(relid)) AS toast_only_size
FROM pg_catalog.pg_stat_user_tables
WHERE relname IN ('transaction', 'lead', 'pos_embeddings', 'leads_embeddings')
ORDER BY pg_total_relation_size(relid) DESC;

-- 6. Confirm HNSW index is present and active
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'pos_embeddings' 
  AND indexdef ILIKE '%hnsw%';

-- 7. Check current match_configuration thresholds
SELECT * FROM "$SCHEMA_NAME".match_configuration;
```
