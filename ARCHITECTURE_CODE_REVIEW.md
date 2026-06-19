# Technical Architecture Code Review: Lead-to-POS Matching System

This document provides a comprehensive technical overview and architectural analysis of the **Costco Lead-to-POS Matching** system. It is based on a deep-dive recursive review of the project files located at `~/projects/Ram_Projects/DiracDelta/lead-pos`.

---

## 1. Executive Summary & Core Mission
The Lead-to-POS Matching system is designed to programmatically match commercial leads (sourced from ServiceNow) against Point of Sale (POS) transaction records. This enables Costco to attribute sales back to specific advertising, marketing, or outreach leads, track conversion rates, and automatically identify leads that represent already active buying customers (**Closed-Existing** status). 

The system operates across three primary environments:
*   **ADT** (Development/Testing)
*   **QAT** (Quality Assurance)
*   **SPT** (Staging/Pre-production)

---

## 2. End-to-End System Architecture

The following diagram illustrates the complete data flow, from POS file upload in Google Drive and ServiceNow lead retrieval, through private data ingestion and similarity matching in GCP, to final synchronization of results back to ServiceNow.

```mermaid
graph TD
    %% POS Data Ingestion Path
    subgraph Google Workspace & Drive
        GD[Google Drive Folder] -->|POS File Upload| GAS[Google Apps Script]
        GAS -->|Detect process_pos_data.txt| GH_A_Drive[GitHub Actions: drive_to_gcs_sync]
    end

    subgraph GitHub Actions CD
        GH_A_Drive -->|Trigger| CR_Drive_Sync[Cloud Run Job: snow-sync-job]
    end

    subgraph Google Cloud Platform (VPC Network)
        %% Cloud Run & GCS Ingestion
        CR_Drive_Sync -->|Download & Move| GCS_Raw_POS[(GCS: pos-raw-raw)]
        CR_Drive_Sync -->|Write Manifest| GCS_Manifests[(GCS: manifests/*.json)]

        %% GCS Notification and Eventarc Trigger
        GCS_Manifests -->|GCS Object Created Pub/Sub| PS_Topic[Pub/Sub: gcs-file-events]
        PS_Topic -->|Eventarc Event| WF_POS_Ingest[GCP Workflow: pos_ingestion_workflow]

        %% Dataflow Processing
        WF_POS_Ingest -->|Launch Flex Template| DF_POS_ETL[Dataflow Job: pos-etl-*]
        GCS_Raw_POS -->|Read Chunked JSON/Excel| DF_POS_ETL
        DF_POS_ETL -->|Batch INSERT via Private IP + IAM| Cloud_SQL[(Cloud SQL PostgreSQL)]

        %% Downstream Ingestion Trigger
        WF_POS_Ingest -->|Trigger Downstream| WF_Snow_Sync[GCP Workflow: snow_sync_workflow]

        %% ServiceNow Lead Ingestion
        WF_Snow_Sync -->|Trigger| CR_Snow_Sync[Cloud Run Job: snow-sync-job]
        SNOW_API_Leads[ServiceNow Lead API] <-->|OAuth 2.0 Private PSC Attachment| CR_Snow_Sync
        CR_Snow_Sync -->|Download| GCS_Temp_Leads[(GCS: temp_leads_path)]
        CR_Snow_Sync -->|Bulk Upsert| Cloud_SQL

        %% Matching Orchestration
        WF_Snow_Sync -->|Trigger Downstream| WF_Lead_Match[GCP Workflow: lead_match_workflow]
        
        %% Step-by-Step Cloud Run Matching
        WF_Lead_Match -->|1. Ingest Leads & POS| CR_Match_Job[Cloud Run Job: lead-match-job]
        Cloud_SQL <-->|Read Tables| CR_Match_Job
        
        %% Embedding and Semantic Vector Similarity Search
        CR_Match_Job -->|Generate Embeddings| Vertex_Embed[Vertex AI text-embedding-005]
        CR_Match_Job -->|Store/Index Vectors| Cloud_SQL
        
        %% Matching Pipeline
        CR_Match_Job -->|2. Run Primary Classification| CR_Match_Job
        CR_Match_Job -->|3. Update DB & ServiceNow| CR_Match_Job
        
        %% ServiceNow Sync Back
        CR_Match_Job <-->|OAuth 2.0 Post Match/CE Payloads| SNOW_API_Results[ServiceNow Matching API]
    end
```

---

## 3. High-Level Process Integration & Interactions

The system leverages several decoupled cloud and software services working in coordination:

### A. Google Drive & Apps Script
*   **Location:** `apps-script/code.js`
*   **Role:** Acts as the primary interface for POS raw data uploads. Business users drop POS transactional sheets directly into a Google Drive folder along with a dummy `process_pos_data.txt` file. 
*   **Interaction:** A time-based Apps Script polls the folder every 5 minutes. When the trigger file is detected, the script dispatches a GitHub Action workflow dispatch event and moves the trigger file to the trash to prevent duplicate triggering.

### B. Google Cloud Storage (GCS)
*   **Location Managed in:** `terraform/modules/gcs_bucket`
*   **Role:** Storage buckets act as the ingestion gateway, intermediate processing layer, and archival registry.
*   *   `gcp-gcs-lead-mgmt-<env>-pos-raw` stores raw incoming POS sheets and manifests.
*   *   `gcp-gcs-lead-mgmt-<env>` stores compiled pipeline templates, state checkpoints, configuration metadata (`config/workflow-config.json`), and temporary transaction scanned manifests.

### C. Cloud Run Jobs
*   **Location:** `cloud_run_service/`, `lead_management_job/`
*   **Role:** Serverless execution blocks running discrete stages of the pipelines.
*   *   `snow-sync-job` (Script: `lead_management_job/main.py`) performs Google Drive-to-GCS synchronizations, fetches leads/POS updates from ServiceNow, and loads them to Cloud SQL.
*   *   `lead-match-job` (Script: `lead_management_job/run.py`) processes intermediate preprocessing, executes primary classification matching, updates SQL tables, and posts final match payloads back to ServiceNow.

### D. Cloud Workflows
*   **Location:** `terraform/modules/workflows/`
*   **Role:** Orchestrates complex serverless steps, offering state maintenance, automated branching, error handling, and PSC (Private Service Connect) routing.

### E. Apache Beam / Dataflow
*   **Location:** `dataflow/`
*   **Role:** A containerized Apache Beam batch pipeline (`pos_pipeline/main.py`) executed as a Dataflow Flex Template. It streams large raw POS files from GCS, maps schema columns dynamically, and bulk-inserts them into the Cloud SQL PostgreSQL database via Private IP.

### E. Cloud SQL PostgreSQL & pgvector
*   **Location Managed in:** `terraform/modules/database/`, `postgres_resources/`
*   **Role:** Core relational and vector database. Leverages the `pgvector` extension to store 768-dimensional vector embeddings of business addresses, names, and combined fields. It runs HNSW indexes for sub-second similarity searches.

### F. Vertex AI (Vertex GenAI SDK)
*   **Location Used in:** `lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_leads.py` & `vector_db_loading_pos.py`
*   **Role:** Generates semantic embeddings using the `text-embedding-005` model to represent textual metadata, which underpins the fuzzy/semantic matching algorithms.

---

## 4. Confirming the POS Data Bypass Policy

**Architectural Fact:** **Yes, POS raw transaction data entirely bypasses ServiceNow during ingestion.**

*   **Evidence:** In the ingestion flow (`pos_dataflow_workflow.yaml` -> `dataflow/pos_pipeline/main.py`), POS transactions are copied from Google Drive into GCS, processed by a batch Dataflow pipeline, and bulk-inserted into the PostgreSQL database. **ServiceNow is never contacted, queried, or utilized to hold raw POS records.** 
*   **Matching Phase:** The only point where POS-related information touches ServiceNow is when *successfully matched* records (or leads identified as *Closed - Existing*) are pushed back to ServiceNow's match results API (`update_servicenow.py`). This payload contains only the matched POS transaction metadata linked to a Lead ID. Unmatched POS records remain private in Cloud SQL and are never shared with ServiceNow. This maintains strict data segregation and prevents overloading ServiceNow with bulk retail transaction logs.
