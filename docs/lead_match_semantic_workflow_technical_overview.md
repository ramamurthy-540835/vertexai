# Lead Match Semantic Workflow Technical Overview

This document explains the SPT lead-to-POS semantic matching workflow from a
technical and data science perspective. It is written for engineers, data
scientists, cloud operators, and reviewers who need to understand what runs,
where data is stored, how the model is used, and how to interpret benchmark
logs for warehouse 115.

## Executive Summary

The semantic workflow matches Costco business leads to POS transactions by
combining two layers:

1. Deterministic rule matching
   - Exact normalized field comparisons across lead and POS attributes.
   - Includes OMS alternate POS fields.
   - Produces the first classified lead/POS output.

2. Semantic fuzzy matching
   - Uses Vertex AI Gemini embeddings.
   - Stores vectors in Cloud SQL PostgreSQL with pgvector.
   - Finds semantically similar POS transactions for each lead.
   - Overrides the deterministic result only when the semantic score is better.

The GitHub workflow triggers the deployed GCP Cloud Workflow. The Cloud
Workflow then orchestrates Cloud Run Job executions for ingestion, embedding,
primary matching, fuzzy matching, Cloud SQL update, ServiceNow update, and
cleanup.

For the current SPT warehouse 115 benchmark, logs showing 50K-row Cloud SQL POS
chunks and 9M+ rows processed mean the POS extraction stage was progressing
normally. If POS extraction completed, the next expected stage is embedding
generation for leads and POS, followed by primary matching, then fuzzy matching.

## Runtime Entry Points

### Manual Trigger

The user starts the benchmark from GitHub Actions:

- Workflow: `.github/workflows/lead_match_semantic_workflow.yml`
- Inputs:
  - `environment`: usually `spt`
  - `warehouse`: `115`, `1663`, `115,1663`, blank, or `all`
  - `wait_for_completion`: `true` or `false`

For warehouse 115, the GitHub workflow sends this Cloud Workflow payload:

```json
{"warehouse":"115"}
```

If warehouse is blank or `all`, the payload is:

```json
{}
```

### GCP Orchestration

The deployed workflow is:

- `terraform/modules/workflows/lead_match_workflow.yaml`
- GCP workflow name: `lead_match_workflow`
- Cloud Run Job name: `lead-match-job`
- Region: `us-central1`

The Cloud Run job dispatcher is:

- `lead_management_job/run.py`

The Cloud Workflow starts these stages:

1. `ingest_leads_from_cloud_sql`
2. `ingest_pos_from_cloud_sql`
3. `embedding_generation_leads`
4. `embedding_generation_pos`
5. `primary_matching`
6. `fuzzy_matching`
7. `update_service_now`
8. `update_database`
9. `temporary_file_deletion`

## Data Sources

The SPT configuration is in:

- `lead_management_job/configuration_spt.ini`

The key schema is:

- `lead_mgmt_spt`

Primary source tables:

- `lead_mgmt_spt.lead`
- `lead_mgmt_spt.account`
- `lead_mgmt_spt.contact`
- `lead_mgmt_spt.transaction`

Embedding tables:

- `lead_mgmt_spt.leads_embeddings`
- `lead_mgmt_spt.pos_embeddings`

Configuration/audit/update tables are read through SQL configured in
`configuration_spt.ini`, including:

- `lead_mgmt_spt.match_configuration`
- match/audit table referenced by `JobConfig`
- temporary update tables created by `update_source_data.py`

## Warehouse Scoping

The workflow supports optional warehouse scoping through the `WAREHOUSE`
environment variable.

Warehouse scoping is applied before the large temp files are created:

- Lead extraction adds a filter on `lead.warehouse_number`.
- POS extraction adds a filter on `transaction.warehouse_number`.
- Lead embedding ID selection filters `a.warehouse_number`.
- POS embedding ID selection filters `a.warehouse_number`.
- Fuzzy matching filters classified rows by warehouse before querying
  embeddings.

For warehouse 115, this prevents the run from intentionally matching against
other warehouses.

Supported GitHub workflow inputs are:

- `115`
- `1663`
- `115,1663`
- blank
- `all`

## Ingestion Stage

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/data_ingestion_cloud_sql.py`

Purpose:

- Read lead and POS rows from Cloud SQL.
- Normalize text fields used for deterministic matching.
- Preserve original fields for ServiceNow and Cloud SQL updates.
- Write temp CSVs to GCS for later stages.

Cloud SQL reads are streamed:

- `CHUNK_SIZE = 50_000`
- SQLAlchemy `stream_results=True`
- SQLAlchemy `yield_per=50_000`
- `pandas.read_sql(..., chunksize=50_000)`

GCS upload behavior:

- Uses resumable GCS writer.
- Upload chunk size is 5 MB.
- Writes one output CSV per source type.

SPT GCS temp outputs:

- Leads: `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/leads_temp.csv`
- POS: `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/pos_temp.csv`

The POS benchmark logs that show 50K chunks indicate this ingestion code path.
If logs say 9M rows processed, that means roughly 180 POS extraction chunks
have been read and written.

## Vertex AI Embedding Stage

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_leads.py`
- `lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_pos.py`

Model settings:

- Model: `gemini-embedding-001`
- Vertex task type: `SEMANTIC_SIMILARITY`
- Output dimensionality: `768`
- Region: `us-central1`
- Client: `google.genai.Client(vertexai=True, ...)`

The code creates three embeddings per row where possible:

- `combined_embedding`
- `address_embedding`
- `name_embedding`

The source text fields are:

- Combined customer field:
  - business name
  - full address
  - phone
  - customer name
  - email
- Full address
- Business name

### Lead Embedding Chunking

Lead embedding API batching:

- API batch size: 250 text values
- Thread pool max workers: from `MAX_WORKERS`, default 5
- Rate limit retry with exponential backoff
- Failed embeddings are left as null, not zero-filled

Lead embedding destination:

- `lead_mgmt_spt.leads_embeddings`

### POS Embedding Chunking

POS embedding processing:

- DataFrame chunk size: 2,000 rows
- Internal API batch size: 25 text values
- Thread pool max workers: from `MAX_WORKERS`, default 5
- Checkpoint table:
  - `lead_mgmt_spt.embedding_job_checkpoint`

POS embedding destination:

- `lead_mgmt_spt.pos_embeddings`

The POS embedding path deletes existing embeddings for the selected POS IDs
before inserting replacement vectors. This makes reruns safer for the same
warehouse/scope.

## Deterministic Primary Matching

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/lead_matching.py`
- `lead_match_codebase/src/costco/leadmgmt/components/streaming_partition.py`

Purpose:

- Build exact lead/POS matches before semantic overlay.
- Avoid loading the full POS temp file into memory.
- Create a classified handoff file for fuzzy matching.
- Create a processed POS manifest for later database update.

### Matching Fields and Weights

The deterministic scorer compares normalized values and assigns points:

| Logical field | Points |
| --- | ---: |
| Business name | 40 |
| Address line one | 40 |
| Email | 30 |
| Phone | 20 |
| Zip | 10 |
| City | 5 |
| State | 5 |

Maximum deterministic score:

```text
150
```

Thresholds:

```text
MINIMUM_SCORE = 70
COMPLETE_SCORE = 100
```

Business result mapping:

- Score >= 100 becomes `Match`.
- Score >= 70 and < 100 becomes `Potential`.
- Below 70 is treated as no deterministic match.

### POS/OMS Source Sets

The scorer checks six source sets. Each set maps the same logical fields to
different POS/OMS columns:

1. POS fields only
2. POS fields plus OMS company
3. OMS primary fields with POS business name
4. OMS primary fields with OMS company
5. OMS secondary fields with POS business name
6. OMS secondary fields with OMS company 2

For each lead/POS candidate pair:

1. Score all six sets.
2. Keep the highest scoring set.
3. On ties, lower set number wins.
4. Store the winning set and matched fields for explanation.

### Candidate Generation

The primary matcher does not compare every lead to every POS row.

Candidates are generated by equi-joining on:

- same `warehouse_number`
- plus at least one blocking value match from:
  - business
  - email
  - phone
  - address

Zip/city/state do not generate candidates by themselves because their combined
maximum is too low to reach the minimum score.

### Memory Strategy for POS Matching

The POS temp CSV can be very large. The implementation avoids loading all POS
rows into memory.

`streaming_partition.py` uses two passes:

1. Spill pass
   - Reads `pos_temp.csv` from GCS in 250K-row chunks.
   - Splits each chunk by warehouse.
   - Writes per-warehouse part files to a temporary GCS prefix.

2. Process pass
   - Reads one warehouse's part files back into memory.
   - Runs deterministic matching for that warehouse.
   - Deletes that warehouse's temp part files.
   - Moves to the next warehouse.

Temporary spill layout:

```text
gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/match_spill/{match_id}_{timestamp}/wh={warehouse}/part-00001.csv
```

This design exists because Cloud Run `/tmp` is memory-backed. Spilling to local
disk would still consume memory, so the code spills to GCS.

Primary matching writes:

- Stable classified handoff:
  - `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/leads_classified.csv`
- Processed POS manifest:
  - `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/processed_pos_ids.csv`
- Archived primary output:
  - `gs://gcp-gcs-lead-mgmt-us-spt/match/final_match_result/primary_match_output_{match_id}_{timestamp}.csv`

## Semantic Fuzzy Matching

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/fuzzy_matching_sql.py`

Purpose:

- Read deterministic classified output.
- Query pgvector embeddings for semantic candidates.
- Compute semantic score.
- Override deterministic result only when semantic score is higher.
- Write final update CSV to the final GCS folder.

### Fuzzy SQL Candidate Logic

For each lead in the classified handoff:

1. Pull lead embedding vectors from `leads_embeddings`.
2. Search POS embeddings from `pos_embeddings`.
3. Restrict to the same warehouse when warehouse is present.
4. Restrict to POS transactions not earlier than the lead fiscal period.
5. Order by vector distance on `combined_embedding`.
6. Keep the top 20 candidates per lead.
7. Require `combined_field_score >= 80`.

The vector score uses pgvector cosine distance:

```sql
(1 - (pos_embedding <=> lead_embedding)) * 100
```

Scores produced:

- `combined_field_score`
- `full_address_score`
- `business_name_score`

The final semantic score is weighted:

```text
similarity_score =
  (combined_field_score
   + 4 * full_address_score
   + 3 * business_name_score) / 8
```

This heavily weights address similarity, then business-name similarity, then
the broader combined field.

The Python layer keeps fuzzy candidates with:

```text
similarity_score >= 80
```

Then it merges fuzzy candidates onto the deterministic output by `lead_id`.

### Override Rule

Fuzzy does not blindly replace deterministic matching.

It updates the row only when:

```text
fuzzy similarity_score > primary similarity_score
and fuzzy pos_id is present
and fuzzy similarity_score is present
```

If the fuzzy result is not better, the deterministic row is retained.

Final match labels come from Cloud SQL:

```sql
SELECT match_result, min_score, max_score
FROM lead_mgmt_spt.match_configuration
```

That table controls whether the final score maps to `Match`, `Potential`, or
`No Match`.

Fuzzy writes final output to:

```text
gs://gcp-gcs-lead-mgmt-us-spt/match/final_match_result/final_update_dataframe_{timestamp}.csv
```

Before writing, `process_and_archive_files` archives anything already in
`match/final_match_result`. This is important because the update jobs expect
exactly one file in that folder.

## Final Update Stages

### Cloud SQL Update

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/update_source_data.py`

Reads final GCS file from:

```text
gs://gcp-gcs-lead-mgmt-us-spt/match/final_match_result/
```

It expects exactly one file in that folder.

It updates:

- POS transaction match fields for `Match` and `Potential`.
- Lead table match fields for `Match` and `Potential`.
- Closed-existing lead status where applicable.
- `transaction.is_processed = true` for scanned POS IDs from:
  - `temporary_folder/processed_pos_ids.csv`

### ServiceNow Update

Implemented in:

- `lead_match_codebase/src/costco/leadmgmt/components/update_servicenow.py`

Reads the same final GCS file.

It sends:

- Match/Potential rows to ServiceNow match update endpoint.
- Closed-existing rows to the closed-existing update endpoint.

ServiceNow batch size for SPT:

```text
insert_batch_size = 1000
```

Match/Potential failures are fatal. Closed-existing update failures are logged
but do not fail the already-delivered Match/Potential path.

## GCS Object Summary for SPT

| Purpose | Path |
| --- | --- |
| Lead temp CSV | `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/leads_temp.csv` |
| POS temp CSV | `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/pos_temp.csv` |
| Classified handoff | `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/leads_classified.csv` |
| Processed POS manifest | `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/processed_pos_ids.csv` |
| Primary/final output folder | `gs://gcp-gcs-lead-mgmt-us-spt/match/final_match_result/` |
| Archived final outputs | `gs://gcp-gcs-lead-mgmt-us-spt/archive/match/final_match_result/` |
| Raw lead input archive | `gs://gcp-gcs-lead-mgmt-us-spt/archive/match/raw_input/leads/` |
| Raw POS input archive | `gs://gcp-gcs-lead-mgmt-us-spt/archive/match/raw_input/pos/` |
| Temporary match spill | `gs://gcp-gcs-lead-mgmt-us-spt/temporary_folder/match_spill/...` |

## Cloud SQL Table Summary

| Table | Purpose |
| --- | --- |
| `lead_mgmt_spt.lead` | Source leads and final lead match status |
| `lead_mgmt_spt.account` | Lead account attributes such as business/address |
| `lead_mgmt_spt.contact` | Lead contact attributes such as name/email/phone |
| `lead_mgmt_spt.transaction` | POS transaction source and final POS match fields |
| `lead_mgmt_spt.leads_embeddings` | Vertex embedding vectors for leads |
| `lead_mgmt_spt.pos_embeddings` | Vertex embedding vectors for POS transactions |
| `lead_mgmt_spt.embedding_job_checkpoint` | POS embedding chunk checkpoint state |
| `lead_mgmt_spt.match_configuration` | Score ranges for final match labels |

## How to Read the Current Warehouse 115 Benchmark

Given the observed status:

```text
SPT semantic workflow is still running.
POS Cloud SQL extraction processed at least 9M rows in 50K-row chunks.
POS extraction completed successfully.
Need to confirm the next Cloud Workflow stage started.
```

Technical interpretation:

1. The workflow was still healthy during POS extraction.
2. The 50K-row log cadence confirms the streaming Cloud SQL ingestion path.
3. Completing POS extraction means `pos_temp.csv` should exist in GCS.
4. The next expected stages are:
   - `embedding_generation_leads`
   - `embedding_generation_pos`
5. If embedding starts, logs should show:
   - `Starting Embedding Job`
   - `Run ID: ... | Model: gemini-embedding-001`
   - `Processing chunk X/Y`
6. If primary matching starts, logs should show:
   - `Leads span N distinct warehouse(s)`
   - `Spill: N chunk(s), rows seen, rows kept`
   - `Warehouse X/Y (115) - POS rows, leads`
7. If fuzzy matching starts, logs should show:
   - `Rows to update: N`
   - `Fuzzy match output written to: gs://.../final_update_dataframe_...csv`

The benchmark is not complete until these are true:

- Cloud Workflow execution state is `SUCCEEDED`.
- `fuzzy_matching` Cloud Run execution succeeded.
- Final update CSV exists in `match/final_match_result`.
- `update_database` succeeded.
- `update_service_now` succeeded or intentionally skipped because there were
  no rows to send.
- No Cloud Run execution shows OOM, timeout, or nonzero exit.

## Metrics to Capture for Benchmark Results

Capture these values from GCP Console logs and metrics:

| Metric | Why it matters |
| --- | --- |
| Total workflow duration | End-to-end business runtime |
| Lead extraction duration | Lead Cloud SQL read cost |
| POS extraction duration | Large table scan/read cost |
| POS rows extracted | Scope size for warehouse 115 |
| Lead embedding duration | Vertex API throughput for leads |
| POS embedding duration | Vertex API throughput for POS |
| POS embedding chunks completed | Progress and retry visibility |
| Primary matching duration | Deterministic engine runtime |
| Primary matching peak memory | Validates streaming design |
| Fuzzy matching duration | pgvector query runtime |
| Fuzzy rows updated | Incremental value of semantic overlay |
| Final match distribution | Business quality summary |
| Cloud SQL update duration | Database write cost |
| ServiceNow update duration | External integration cost |

Recommended log strings to search in GCP Console:

```text
Using match_id
Reading pos data in chunks of 50000 rows
Wrote rows to
Starting Embedding Job
Run ID:
Model: gemini-embedding-001
Processing chunk
Leads span
Spill complete
Warehouse
Rows to update
Fuzzy match output written
Match result distribution
ServiceNow update completed
Workflow execution SUCCEEDED
```

Error strings to search:

```text
MemoryError
Killed
OOM
exit code 137
timeout
Rate limit
resource exhausted
quota
FAILED
Traceback
```

## Data Science Interpretation

The workflow is not a single black-box AI model. It is a hybrid matching
system:

1. Deterministic layer
   - High precision.
   - Explainable exact-field matches.
   - Good when fields are clean and normalized values line up.

2. Semantic layer
   - Better recall for noisy names/addresses.
   - Uses vector similarity instead of exact string equality.
   - Only promotes a fuzzy candidate when it beats the deterministic score.

The semantic layer is most useful when:

- Business names are abbreviated or slightly different.
- Addresses are formatted differently.
- POS has richer OMS fields than the lead.
- Exact phone/email is missing but business/address meaning still aligns.

The main quality controls are:

- Warehouse equality.
- Fiscal date constraint.
- Top 20 vector candidates per lead.
- Minimum combined-field semantic score of 80.
- Weighted final semantic score.
- `match_configuration` score bands.
- Fuzzy override only when the fuzzy score is higher.

## Operational Risks and Watchpoints

1. POS extraction duration
   - Large POS scans can run for a long time even when healthy.

2. Vertex quota/rate limit
   - Embedding stages can slow down if rate-limited.
   - Code retries quota/rate errors with backoff.

3. Single-warehouse memory pressure
   - Primary matching processes one warehouse at a time.
   - If one warehouse is very large, that warehouse can still pressure memory.

4. Final result folder contract
   - Update jobs expect exactly one final CSV in `match/final_match_result`.
   - The fuzzy stage should be the last writer before update stages.

5. Embedding nulls
   - Failed embeddings are left null.
   - Rows with missing embeddings may not participate in fuzzy matching.

6. Label consistency
   - Update code expects final business labels such as `Match` and
     `Potential`.
   - The source of truth is `lead_mgmt_spt.match_configuration`.

## Code Reference Map

| Area | File |
| --- | --- |
| GitHub manual trigger | `.github/workflows/lead_match_semantic_workflow.yml` |
| GCP workflow orchestration | `terraform/modules/workflows/lead_match_workflow.yaml` |
| Cloud Run job dispatcher | `lead_management_job/run.py` |
| SPT configuration | `lead_management_job/configuration_spt.ini` |
| Warehouse parser/filter | `lead_match_codebase/src/costco/leadmgmt/util/warehouse_scope.py` |
| Cloud SQL ingestion | `lead_match_codebase/src/costco/leadmgmt/components/data_ingestion_cloud_sql.py` |
| Lead embeddings | `lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_leads.py` |
| POS embeddings | `lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_pos.py` |
| Primary deterministic matching | `lead_match_codebase/src/costco/leadmgmt/components/lead_matching.py` |
| Streaming POS partitioning | `lead_match_codebase/src/costco/leadmgmt/components/streaming_partition.py` |
| Fuzzy semantic matching | `lead_match_codebase/src/costco/leadmgmt/components/fuzzy_matching_sql.py` |
| Cloud SQL final update | `lead_match_codebase/src/costco/leadmgmt/components/update_source_data.py` |
| ServiceNow final update | `lead_match_codebase/src/costco/leadmgmt/components/update_servicenow.py` |
