# Script Inventory and Technical Analysis

This document provides a script-level review of the main files in the Lead-to-POS Matching system, mapping their core responsibility, API touches, database interactions, and error-handling designs.

---

## 1. Core Synchronization and Utility Scripts

### A. `drive_to_gcs.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/util/drive_to_gcs.py`
*   **Purpose:** Orchestrates the secure download of POS datasets from Google Drive folders and uploads them to Cloud Storage (`pos-raw` bucket), setting checkpoints to enable mid-failure resumes.
*   **Key Functions:**
    *   `get_credentials()`: Uses Application Default Credentials (ADC) to load OAuth tokens with drive scopes.
    *   `load_checkpoint()` / `save_checkpoint()`: Stores progress states in `pos-raw-data/incoming-files/.run_checkpoint.json` to handle transient network disconnects without redownloading.
    *   `list_files()`: Recursively polls non-trashed elements from the defined Google Drive folder.
    *   `run()`: The main loop downloading raw tables, archiving them in Drive, and writing a manifest JSON.
*   **APIs Touched:** Google Drive API v3, Cloud Storage JSON API.
*   **Authentication:** GCP Workload Identity / Service Account Application Default Credentials (ADC).

### B. `sync_snow_gcp.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/sync_snow_gcp.py`
*   **Purpose:** Primary ingestion manager handling ServiceNow API pulls for Leads and POS updates, and upserting data to Postgres.
*   **Key Functions:**
    *   `get_record_snow_to_gcs()`: Performs paginated REST calls with basic auth or OAuth to download JSON payloads and saves them to GCS staging paths.
    *   `upsert_using_primary_key()`: Uses bulk parameterized SQL (`INSERT ... ON CONFLICT DO UPDATE`) committing in batches of 1000 with SQLAlchemy.
    *   `load_data_to_db()`: Orchestrates sequential database loading of raw Leads and POS files.
*   **APIs Touched:** ServiceNow Table / Lead API, GCS Client.
*   **Authentication:** Secret Manager (to retrieve ServiceNow client credentials and database connection options).

---

## 2. Similarity and Matching Logic Scripts

### A. `lead_matching.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/components/lead_matching.py`
*   **Purpose:** The primary match orchestrator. Performs chronological matching with family-based scoring and "Closed-Existing" detection.
*   **Key Functions:**
    *   `classify_matches()`: Main processor. Loops chronologically through transaction groups and detects if a matched POS record occurred *before* the lead creation. If so, flags the lead as **Closed-Existing**, skips the match record, and emits a stub row.
    *   `_score_group()`: Determines matching metrics across POS records and active leads using family-based scoring.
    *   `primary_classification()`: Prepares file configurations, registers an `InProgress` status row in `match_audit`, maps results, and saves the output dataset to GCS.
*   **Key Design:** Splitting matching into chronological batches preserves memory safety across massive POS retail logs.

### B. `fuzzy_matching_sql.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/components/fuzzy_matching_sql.py`
*   **Purpose:** Executes semantic vector similarity matching on records where deterministic fields do not align perfectly.
*   **Key Functions:**
    *   `fuzzy_matching()`: Loads exact match outputs from GCS, queries Cloud SQL for vector similarities using Lateral joins, computes a weighted score, and merges high-confidence overrides.
    *   `build_fuzzy_matching_comment()`: Generates detailed, human-readable scoring explanations describing combined, address, and name embedding alignments.
*   **Database Interactions:** Executes cosine-distance calculation queries (`<=>` operator) against `pos_embeddings` and `leads_embeddings` tables.
*   **Current Production Status:** This script is registered as a KFP pipeline component but is **currently bypassed** in the serverless Cloud Workflows (`lead_match_workflow.yaml`).

---

## 3. Database Updates and ServiceNow Sync

### A. `update_servicenow.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/components/update_servicenow.py`
*   **Purpose:** Synchronizes match outputs and lead updates back to ServiceNow endpoints.
*   **Key Functions:**
    *   `get_oauth_token()`: Fetches OAuth 2.0 access credentials from ServiceNow with automated exponential backoffs (2s, 4s, 8s) to account for sleeping ServiceNow test instances.
    *   `_post_batches()`: Dispatches bulk REST requests in parameterized sizes (e.g., 200 records per batch).
    *   `_redact_for_logging()`: Redacts Customer PII fields (email, address, first/last names, phone numbers) before printing payloads to GCP Cloud Logging.
*   **APIs Touched:** ServiceNow Match Result Update API, ServiceNow Lead Status API.

### B. `update_source_data.py`
*   **Path:** `lead_match_codebase/src/costco/leadmgmt/components/update_source_data.py`
*   **Purpose:** Updates relational tables in Cloud SQL based on the final match results.
*   **Key Functions:**
    *   `update_cloud_sql()`: Extracts matches from GCS, separates Match/Potential candidates and Closed-Existing stubs, updates transaction tables, and flips `is_processed = true` for scanned POS IDs.
    *   `lead_status_closed_existing_update()`: Direct SQL updates applying `Closed - Existing` statuses to relevant entries in the lead table.

---

## 4. Google Apps Script and Dataflow Ingestion

### A. `apps-script/code.js`
*   **Purpose:** Polls a Google Drive folder for trigger files and triggers GitHub Actions workflows.
*   **Key Functions:**
    *   `findTriggerFile()`: Scans the specified folder for `process_pos_data.txt`.
    *   `dispatchGitHubWorkflow()`: Dispatches a `workflow_dispatch` Event to GitHub's REST API.
    *   `checkAndTrigger()`: The primary entry point. Deletes the trigger file upon successful dispatch to prevent infinite triggers.

### B. `dataflow/pos_pipeline/main.py`
*   **Purpose:** Runs a batch Apache Beam pipeline to ingest massive POS files into Postgres.
*   **Key Classes:**
    *   `ReadFileFromGCS`: DoFn class streaming files line-by-line rather than reading into RAM.
    *   `WriteToPostgresIAM`: DoFn mapping columns based on `field_map.json` and bulk-inserting them into Cloud SQL PostgreSQL using private IP and IAM authentication.
