# GCP Jobs, Workflows, and Database Schema Catalog

This document details the configuration, deployment, runtime properties, and database structures powering the Lead-to-POS Matching system.

---

## 1. Cloud Run Jobs

The system deploys two central Cloud Run Jobs. They are built as containerized Python workloads and executed with command-line arguments to invoke specific pipeline stages.

### A. Job: `snow-sync-job`
*   **Source Folder:** `lead_management_job/` (utilizes wheels built from `lead_match_codebase/`)
*   **Trigger Mechanism:** Invoked via Cloud Workflows (`snow_sync_workflow.yaml`) or manually by developer GAs triggers (`sync_drive_with_gcs.yml`).
*   **Runtime Flow / Supported Arguments:**
    1.  `drive_to_gcs`: Pulls raw Excel/CSV POS sheets from Google Drive, streams them to the GCS bucket `pos-raw`, archives files in Drive, and outputs a manifest file in GCS under `/manifests/run_id.json`.
    2.  `snow_to_gcs_lead`: Retrieves active commercial leads from ServiceNow APIs using a paginated chunk-wise fetch, writing them to GCS.
    3.  `lead_to_db`: Loads ServiceNow leads CSVs/JSONs from GCS, pre-processes them, splits them into Account, Lead, and Contact relation components, and performs an incremental UPSERT into Cloud SQL.
*   **Primary Environment Variables:**
    *   `GCP_PROJECT_ID`: Target project ID
    *   `CONFIG_FILE_PATH`: Path to environment-specific ini configuration file (e.g. `configuration_adt.ini`)
    *   `DRIVE_FOLDER_ID`: Source Google Drive folder ID

### B. Job: `lead-match-job`
*   **Source Folder:** `lead_management_job/` (uses `run.py` as entrypoint)
*   **Trigger Mechanism:** Triggered step-by-step in parallel or sequence by Cloud Workflows (`lead_match_workflow.yaml`).
*   **Runtime Flow / Supported Arguments:**
    1.  `ingest_leads_from_cloud_sql`: Extracts newly updated leads from the SQL database and writes them as preprocessed CSVs in GCS.
    2.  `ingest_pos_from_cloud_sql`: Extracts unprocessed POS transactions from PostgreSQL (`is_processed = false`) and prepares them in GCS.
    3.  `primary_matching`: Runs the high-performance warehouse-batched chronological matching logic, generating matching comments, scoring alignments, detecting "Closed-Existing" stubs, and outputting the merged datasets to GCS.
    4.  `update_service_now`: Reads final match results from GCS, splits them into Match/Potential batches and Closed-Existing stubs, authenticates to ServiceNow via OAuth 2.0, and posts them to target endpoints.
    5.  `update_database`: Bulk updates PostgreSQL tables, marking scanned POS records as `is_processed = true` using a transaction manifest and updating Lead table statuses.
    6.  `temporary_file_deletion`: Deletes transient files generated in GCS during the matching run.
    7.  `mark_match_failed`: Failure fallback stage that logs pipeline execution failure and marks the audit record state as Failed.

---

## 2. Cloud Dataflow Ingestion Job

### Job: `pos-etl-*` (Flex Template)
*   **Source Folder:** `dataflow/` (contains Apache Beam runner and template specifications)
*   **Trigger Mechanism:** Launched programmatically by the GCP Workflow `pos_ingestion_workflow` when a new manifest JSON is uploaded.
*   **Input Source:** Raw POS Excel/CSV files stored in `gs://gcp-gcs-lead-mgmt-<env>-pos-raw/pos-raw-data/incoming-files/*`.
*   **Output Destination:** Appends entries directly to the Cloud SQL table `lead_mgmt_<env>.transaction`.
*   **Runtime Characteristics:**
    *   Utilizes a GCS-streaming `ReadFileFromGCS` DoFn that yields chunks line-by-line rather than loading whole sheets into RAM, guaranteeing multi-million row scalability.
    *   Connects privately using Cloud SQL Auth Connector (`pg8000`) over private subnet IP.
    *   Batches writes and respects PostgreSQL's maximum binding parameter limits (65,535 constraints) by dividing into sub-inserts.
    *   Performs database writes with `ON CONFLICT (sales_reference_id) DO NOTHING` to ensure strict idempotency on execution restarts.

---

## 3. Cloud Workflows (Orchestrators)

The matching lifecycle is fully automated using three interconnected GCP Workflows:

```
[ GCS Manifest Upload ] ──> Trigger ──> [ pos_ingestion_workflow ]
                                                   │
                                            (On Success)
                                                   ▼
                                        [ snow_sync_workflow ]
                                                   │
                                                   ▼
                                         [ lead_match_workflow ]
```

### A. `pos_ingestion_workflow`
*   **Trigger:** Eventarc trigger matching GCS Object Finalize events under the `manifests/` prefix.
*   **Core Flow:**
    *   Extracts list of files from manifest JSON.
    *   Launches parallel Dataflow Flex Template jobs (`pos-etl-*`) for each file (bounded by concurrent limit parameter `chunk_size`).
    *   Implements GCS-backed state checkpointing (`state/<run_id>.json`). If an execution crashes midway, the next attempt resumes exactly where it failed.
    *   Moves successfully completed POS files from `incoming/` to `processed/` folders in GCS.
    *   Triggers the downstream workflow `snow_sync_workflow`.

### B. `snow_sync_workflow`
*   **Core Flow:**
    *   Executes `snow-sync-job` with `snow_to_gcs_lead` args to retrieve ServiceNow leads.
    *   Executes `snow-sync-job` with `lead_to_db` args to ingest leads to database.
    *   On completion, triggers the downstream `lead_match_workflow`.

### C. `lead_match_workflow`
*   **Core Flow:**
    *   Runs lead and POS ingestion stages of `lead-match-job` in parallel.
    *   Runs the primary matching orchestrator.
    *   Runs database update and ServiceNow result push steps in parallel.
    *   Runs temporary storage cleanup.
    *   If any stage fails, runs `mark_match_failed` to ensure the run state is properly audited in PostgreSQL database logs.

---

## 4. Cloud SQL Database Schema Detail

### Table Catalog (`postgres_resources/costco_db_ddl.sql`)

1.  **`account`**: Stores corporate business account profiles linked to leads.
    *   *Primary Key:* `account_id` (varchar)
    *   *Constraint:* `uniq_account_name_addr_01` UNIQUE on `(business_name, address_line_one, address_line_two, city, state, zip_code)`.
    *   *Index:* `account_unique_with_nulls_as_value` using COALESCE statements for null safety during business key deduplication.
2.  **`lead`**: Stores the leads imported from ServiceNow.
    *   *Primary Key:* `lead_id` (varchar)
    *   *Foreign Key:* `account_id` references `account(account_id)`.
    *   *Index:* `lead_status_index` on `lead_status` (btree).
3.  **`contact`**: Stores primary contacts attached to leads.
    *   *Primary Key:* `contact_id` (varchar)
    *   *Foreign Key:* `lead_id` references `lead(lead_id)`.
4.  **`batch_audit`** & **`match_audit`**: Real-time logging tables that track individual staging ingest volumes, matching statistics, matching status, and execution times.
5.  **`leads_embeddings`**: Vector database table representing leads data.
    *   *Fields:* `lead_id`, `combined_field`, `business_name`, `business_address`, `combined_embedding` (`vector(768)`), `address_embedding` (`vector(768)`), `name_embedding` (`vector(768)`).
    *   *Index:* `leads_combined_embedding_idx` using **HNSW** (`vector_cosine_ops`).
6.  **`pos_embeddings`**: Vector database table representing POS transactions.
    *   *Fields:* `pos_id`, `account_number`, `combined_field`, `business_name`, `business_address`, `combined_embedding` (`vector(768)`), `address_embedding` (`vector(768)`), `name_embedding` (`vector(768)`).
    *   *Index:* `pos_combined_embedding_idx` using **HNSW** (`vector_cosine_ops`).
7.  **`transaction`**: High-volume repository storing POS retail transactions.
    *   *Primary Key:* `pos_id` (auto-generated using custom prefix and sequence `GPOS...`).
    *   *Fields:* `sales_reference_id`, `account_number`, `lead_id`, `match_score`, `match_type`, `is_processed` (boolean flag to exclude/include in matching runs), `process_datetime`.
