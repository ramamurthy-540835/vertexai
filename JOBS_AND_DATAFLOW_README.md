# End-to-End Matching System: Jobs, Dataflow Pipelines, and Solution State Guide

This document provides a highly detailed technical specification of the entire Lead-to-POS Matching System. It details the runtime jobs, the Google Cloud Dataflow ingestion pipeline, and the current operational state of the solution.

---

## 1. End-to-End System Architecture

The following diagram illustrates the complete, production-hardened data flow—from external source endpoints down to the embedding vectors, matching layers, and final feedback loops.

```
SERVICE NOW
Lead Sources
     │
     ▼
OAuth 2.0 + PSC (Private Service Connect)
     │
     ▼
Cloud Run
Lead Ingestion (snow-sync-job)
     │
     ▼
Cloud SQL
Lead Tables (account, lead, contact)
────────────────────────────────────────────────────────────────

POS Sources
Google Drive
     │
     ▼
Apps Script (clasp automated export)
     │
     ▼
Cloud Storage (gcp-gcs-lead-mgmt-*-pos-raw)
     │
     ▼
Cloud Workflows (pos_ingestion_workflow)
     │
     ▼
Cloud Run / Dataflow
POS ETL (Apache Beam Flex Template)
     │
     ▼
Cloud SQL
Transaction Tables (transaction)

────────────────────────────────────────────────────────────────

Embedding Layer (Vertex AI Integration)

Cloud Run
Embedding Generator
     │
     ▼
Vertex AI
Gemini Embedding / text-embedding-004 (768 Dimensions)
     │
     ▼
Cloud SQL pgvector

leads_embeddings (l)
pos_embeddings (s)

HNSW Indexes (vector_cosine_ops)

────────────────────────────────────────────────────────────────

Matching Layer (Fuzzy & Deterministic Orchestration)

Warehouse Filter (l.warehouse_number = s.warehouse_number)
     │
     ▼
Temporal Filter (s.fiscal_year >= l.fiscal_year AND temporal offsets)
     │
     ▼
Top-K Vector Search (CROSS JOIN LATERAL HNSW search)
     │
     ▼
Candidate Retrieval (Retrieve top 20 candidate rows per lead)
     │
     ▼
Confidence Scoring

Combined Field (1x)
Physical Address (4x)
Business Name (3x)

     │
     ▼
Match Classification

High (>= 90%) ────> "Match"
Medium (85-89%) ──> "Potential"
Low (80-84%) ─────> "Potential"
No Match (< 80%) ─> "No Match"

────────────────────────────────────────────────────────────────

ServiceNow Writeback
OAuth 2.0 + PSC (Private Service Connect)
```

---

## 2. Deep Dive: Google Cloud Dataflow Ingestion Pipeline

The high-volume point-of-sale (POS) raw data is ingested into the PostgreSQL database using an Apache Beam pipeline running on **Google Cloud Dataflow** (deployed as a Flex Template under `dataflow/`).

### Architectural Implementation (`dataflow/pos_pipeline/main.py`)

The pipeline reads an incoming file from Google Cloud Storage, maps columns dynamically using a field mapping schema, and performs high-speed bulk inserts into Cloud SQL over private connections. 

```
[ GCS Input File ]
       │
       ▼
 [ ReadFileFromGCS ] ───────> Streams file line-by-line (prevents OOM)
       │
       ▼
[ WriteToPostgresIAM ] ─────> Dynamic Column Mapping + Sub-batched INSERTs
       │
       ├─── (Yields per-flush row count integers)
       ▼
[ CombineGlobally(sum) ] ──> Merges integers from all multi-worker nodes
       │
       ▼
 [ WriteBatchAudit ] ───────> Writes EXACTLY ONE batch_audit row per pipeline run
```

### Advanced Pipeline Features & Safeguards

#### 1. Out-of-Memory (OOM) Protection (`ReadFileFromGCS`)
Traditional Dataflow ingestion loads entire files into memory. To support POS files containing millions of rows, `ReadFileFromGCS` utilizes a custom chunking iterator (`iter_file_chunks`). It streams rows from GCS line-by-line, yielding manageable chunks (default `chunk_size = 10,000`) down the pipeline.
```python
class ReadFileFromGCS(beam.DoFn):
    def process(self, gcs_path: str):
        # Streams file chunk-by-chunk without full RAM buffering
        for chunk in iter_file_chunks(blob, filename, self.chunk_size):
            yield {"rows": chunk}
```

#### 2. Cloud SQL IAM Auth & Private IP (`WriteToPostgresIAM`)
The database connection utilizes the official **Google Cloud SQL Python Connector** via Google's `pg8000` driver. It authenticates dynamically using the Dataflow runner's service account (Workload Identity, `enable_iam_auth=True`) and connects entirely over private IP networks (`ip_type=IPTypes.PRIVATE`). No static credentials or public connections are exposed.

#### 3. Parameter Constraint Mitigation (Sub-Batching)
PostgreSQL enforces a maximum binding parameter limit of **65,535 parameters** per query. If a batch insert exceeds this, the query crashes. The pipeline dynamically calculates `max_rows_per_insert` based on column count and sub-batches writes to remain safely under the limit:
```python
MAX_PG_PARAMS = 60000
num_cols = len(columns)
max_rows_per_insert = max(1, MAX_PG_PARAMS // num_cols)

for start in range(0, len(self._buffer), max_rows_per_insert):
    sub_batch = self._buffer[start : start + max_rows_per_insert]
    # Execute batch SQL write...
```

#### 4. Strict Idempotency (`ON CONFLICT`)
To ensure that retried, duplicated, or restarted Dataflow worker bundles do not result in duplicate records, database insertions use SQL UPSERT semantics:
```sql
INSERT INTO "$SCHEMA_NAME".transaction (pos_id, sales_reference_id, ...)
VALUES (%s, %s, ...)
ON CONFLICT (sales_reference_id) DO NOTHING;
```

#### 5. Multi-Worker Safe Auditing (`CombineGlobally`)
Because Dataflow runs across multiple distributed nodes in parallel, writing an audit row from individual workers would cause multiple conflicting audit logs. The pipeline solves this by yielding row count integers from workers, summing them globally using `CombineGlobally(sum)`, and passing the single aggregated total to `WriteBatchAudit` to write **exactly one** record to `batch_audit`.
```python
counts = (
    p 
    | "StartWithInputPath" >> beam.Create([known_args.input_file])
    | "ReadFile" >> beam.ParDo(ReadFileFromGCS())
    | "WriteDB" >> beam.ParDo(WriteToPostgresIAM(...))
)

(
    counts
    | "SumRowCounts" >> beam.CombineGlobally(sum)
    | "WriteAudit" >> beam.ParDo(WriteBatchAudit(...))
)
```

---

## 3. Automated GCP Workflows & Jobs Catalog

The automation layer is orchestrated via three core **Cloud Workflows** executing two centralized containerized **Cloud Run Jobs** with targeted runtime arguments:

### A. Job: `snow-sync-job` (`lead_management_job/`)
An operational job responsible for driving source-to-GCP synchronization.

*   `drive_to_gcs`: Pulls raw Excel/CSV POS sheets from Google Drive via the Drive API, streams them to the GCS raw landing bucket, archives files in Drive, and outputs a execution manifest JSON file under `manifests/run_id.json`.
*   `snow_to_gcs_lead`: Connects to ServiceNow API endpoints via OAuth 2.0 over Private Service Connect (PSC), executing paginated chunk-wise requests to fetch active commercial leads, and saves them to GCS.
*   `lead_to_db`: Downloads ServiceNow leads files from GCS, applies schema normalization rules, partitions data into Account, Lead, and Contact normalized entities, and executes bulk `INSERT ... ON CONFLICT` UPSERT operations into Cloud SQL.

### B. Job: `lead-match-job` (`lead_management_job/`)
The analytical engine executing matching matches, updates, and feedback loops.

*   `ingest_leads_from_cloud_sql`: Queries Cloud SQL for active, un-matched leads and exports them as clean CSV data in GCS.
*   `ingest_pos_from_cloud_sql`: Queries Cloud SQL for unprocessed POS transactions (`is_processed = false`) and structures them as GCS CSV data.
*   `primary_matching`: Runs the high-performance warehouse-batched exact/deterministic matching logic, applying matching scores, identifying existing stubs, and saving results to GCS.
*   `update_service_now`: Reads match outputs from GCS, partitions them into Match and Potential batches, authenticates to ServiceNow via OAuth 2.0 + PSC, and writes matching results back to the corporate IT service system.
*   `update_database`: Bulk updates PostgreSQL tables, marking scanned POS records as `is_processed = true` and updating Lead table match states.
*   `temporary_file_deletion`: Performs garbage collection, purging temporary GCS directories used during the matching run.
*   `mark_match_failed`: Error-handling fallback step that catches up-stream stage failures, logs errors, and updates database execution logs to `Failed`.

---

## 4. Current State of the Solution

The matching system is fully provisioned in production, but exists in a split-operational state:

### 1. Active Production Path (Deterministic/Exact Match)
The production system actively processes matches using **`primary_matching.py`**. 
*   **Mechanism**: Performs deterministic exact field alignments (e.g. matching account numbers, telephone numbers, emails, or cleaned corporate text strings) using Pandas vectorized operations.
*   **Operational State**: **Fully Active**. Automatically triggered daily by the unified Cloud Workflows chain on GCS manifest uploads.

### 2. Bypassed Path (pgvector Semantic Fuzzy Match)
A highly sophisticated semantic similarity match module exists under **`fuzzy_matching_sql.py`**, utilizing Vertex AI model text embeddings and PostgreSQL HNSW vector indexes.
*   **Mechanism**: Uses Google Vertex AI models to convert address, business name, and combined text strings to 768-dimensional embeddings. It then runs optimized `CROSS JOIN LATERAL` nearest-neighbor searches in Postgres to find records where fuzzy differences (like spelling typos, address formatting variations, abbreviation differences) prevent exact matching.
*   **Operational State**: **Currently Dormant / Bypassed**. Although the tables, database flags, indexes, and source codes are completely provisioned and verified, the orchestrator bypasses this step in the active production pipeline, relying strictly on the exact matching rules.

### Actionable Strategic Recommendation
It is highly recommended to activate `fuzzy_matching_sql.py` in the pipeline as a secondary matching pass. Records that fail exact alignment should automatically fall back to the pgvector similarity engine, significantly increasing business lead-to-POS conversion metrics without adding excessive manual operational overhead.
