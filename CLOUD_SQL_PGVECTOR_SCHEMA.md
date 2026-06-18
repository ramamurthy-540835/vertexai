# Cloud SQL PostgreSQL & pgvector Database Schema Catalog & Implementation Guide

This document provides a comprehensive technical catalog of the Cloud SQL PostgreSQL database instance, its structural schema (DDL & DML), pgvector integration, index strategies, and the highly optimized semantic search query definitions.

---

## 1. Cloud SQL Instance Architecture (Terraform)

The database layer is hosted on Google Cloud Platform using **Cloud SQL for PostgreSQL**. The infrastructure is fully defined and managed via Terraform modules.

### Infrastructure Configuration Details
*   **Database Engine & Version**: PostgreSQL 15 (`POSTGRES_15`).
*   **Machine Machine Specification**: `db-custom-4-15360` (4 vCPUs, 15 GB RAM).
*   **Edition**: `ENTERPRISE` (Standard high-performance PostgreSQL Enterprise edition).
*   **Availability Type**: `REGIONAL` (High Availability with automatic failover across multiple zones).
*   **Storage Configuration**: 100 GB SSD storage with `disk_autoresize` enabled.
*   **Database Backups**: Backups enabled with Point-In-Time Recovery (PITR) enabled.

### Networking & Security Settings
*   **IP Configuration**: Public IP is disabled (`ipv4_enabled = false`). All traffic is restricted to a private network.
*   **VPC Integration**: Private service connection through a shared VPC (Transit Hub: `projects/gcp-prj-transit-hub/global/networks/gcp-vpc-np-host`, subnetwork `gcp-snt-np-usc1-601-cloudruncloudsql-np`).
*   **Encryption**: SSL mode is configured as `ENCRYPTED_ONLY`.
*   **Database Authentication**: Password-less IAM Database Authentication enabled via `cloudsql.iam_authentication = on`. Cloud Run and Dataflow jobs authenticate securely using Google service accounts via Workload Identity.

### Database Flag Configurations
To satisfy enterprise auditing, security, and performance standards, the following database flags are explicitly configured:

| Flag Name | Value | Purpose |
| :--- | :--- | :--- |
| `cloudsql.iam_authentication` | `on` | Enforces IAM database logins for Google Cloud service accounts. |
| `cloudsql.enable_pgaudit` | `on` | Enables the PostgreSQL Auditing (`pgaudit`) extension. |
| `pgaudit.log` | `all` | Configures auditing to log all structural and query changes. |
| `log_connections` | `on` | Logs all successful database connection attempts. |
| `log_disconnections` | `on` | Logs all client disconnection events. |
| `log_checkpoints` | `on` | Logs write-ahead log checkpoints to monitor performance. |
| `log_lock_waits` | `on` | Logs statements that wait longer than deadlock timeouts to detect contention. |
| `log_temp_files` | `0` | Logs all temporary files created for sorting/hashing (helpful for query tuning). |
| `log_min_duration_statement` | `-1` | Disables logging every statement duration (query profiling handled by Insights). |

---

## 2. Database Schema & Table Catalog

The application database is named `lead-mgmt-db`. It contains a primary schema (represented as `$SCHEMA_NAME`, which resolves per environment, e.g. `lead_mgmt_prd`, `lead_mgmt_adt`).

### Table Catalog (`postgres_resources/costco_db_ddl.sql`)

#### A. Core Business Entities

##### 1. `account`
Stores profile records of corporate business accounts linked to leads.
*   **Primary Key**: `account_id VARCHAR(20)`
*   **Fields**:
    *   `batch_id` (`UUID`)
    *   `account_number` (`BIGINT`)
    *   `type` (`VARCHAR(50)`)
    *   `business_name` (`VARCHAR(150)`)
    *   `address_line_one` (`VARCHAR(100)`)
    *   `address_line_two` (`VARCHAR(100)`)
    *   `city` (`VARCHAR(50)`)
    *   `state` (`VARCHAR(50)`)
    *   `zip_code` (`VARCHAR(10)`)
    *   `phone` (`VARCHAR(40) NULL`)
    *   `email` (`VARCHAR(100) NULL`)
    *   `industry_code` (`VARCHAR(40) NULL`)
    *   `bd_industry` (`VARCHAR(50)`)
    *   `updated_by` (`VARCHAR(100) NULL`)
    *   `updated_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
*   **Constraints**:
    *   `uniq_account_name_addr_01`: `UNIQUE (business_name, address_line_one, address_line_two, city, state, zip_code)`
*   **Special Indexes**:
    *   `account_unique_with_nulls_as_value`: A unique index defined on coalesce-formatted fields to prevent duplicate business-key records containing null fields.
    ```sql
    CREATE UNIQUE INDEX IF NOT EXISTS account_unique_with_nulls_as_value
    ON "$SCHEMA_NAME".account (
        COALESCE(business_name, '__NULL__'),
        COALESCE(address_line_one, '__NULL__'),
        COALESCE(address_line_two, '__NULL__'),
        COALESCE(city, '__NULL__'),
        COALESCE(state, '__NULL__'),
        COALESCE(zip_code, '__NULL__')
    );
    ```

##### 2. `lead`
Stores the active sales/marketing leads imported from ServiceNow.
*   **Primary Key**: `lead_id VARCHAR(20)`
*   **Fields**:
    *   `lead_source` (`VARCHAR(100)`)
    *   `account_id` (`VARCHAR(20)`) -> **Foreign Key** referencing `account(account_id)`
    *   `account_number` (`BIGINT NULL`)
    *   `lead_status` (`VARCHAR(100) NULL`)
    *   `confidence_level` (`VARCHAR(100) NULL`)
    *   `match_result` (`VARCHAR(10)`)
    *   `membership_number` (`BIGINT NULL`)
    *   `warehouse_number` (`INT NULL`)
    *   `fiscal_period` (`INT`)
    *   `fiscal_year` (`INT`)
    *   `closed_fiscal_period` (`INT NULL`)
    *   `closed_fiscal_year` (`INT NULL`)
    *   `batch_id` (`UUID`)
    *   `load_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
    *   `updated_by` (`VARCHAR(100)`)
    *   `updated_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)

##### 3. `contact`
Stores primary contacts and communication channels attached directly to leads.
*   **Primary Key**: `contact_id VARCHAR(200)`
*   **Fields**:
    *   `lead_id` (`VARCHAR(20)`) -> **Foreign Key** referencing `lead(lead_id)`
    *   `first_name` (`VARCHAR(100)`)
    *   `last_name` (`VARCHAR(100)`)
    *   `email` (`VARCHAR(100) NULL`)
    *   `phone` (`VARCHAR(100) NULL`)
    *   `membership_number` (`BIGINT NULL`)
    *   `job_title` (`VARCHAR(100) NULL`)
    *   `batch_id` (`UUID`)
    *   `updated_by` (`VARCHAR(100)`)
    *   `updated_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)

##### 4. `transaction`
A high-volume transactional table storing all retail Point-of-Sale (POS) purchases.
*   **Primary Key**: `pos_id VARCHAR(120)` (Auto-generated with sequence and a string prefix, e.g. `GPOS00168029`).
*   **Sequence DDL**:
    ```sql
    CREATE SEQUENCE IF NOT EXISTS "$SCHEMA_NAME".transaction_pos_id_seq START 168029;
    ```
*   **Fields**:
    *   `pos_id` default: `('GPOS' || LPAD(nextval('"$SCHEMA_NAME".transaction_pos_id_seq')::text, 8, '0'))`
    *   `sales_reference_id` (`VARCHAR(130)`)
    *   `account_number` (`BIGINT`)
    *   `lead_id` (`VARCHAR(20) NULL`) -> **Foreign Key** referencing `lead(lead_id)`
    *   `match_score` (`FLOAT`)
    *   `match_type` (`VARCHAR(20)`)
    *   `batch_id` (`UUID`)
    *   `membership_number` (`BIGINT`)
    *   `order_amount` (`FLOAT`)
    *   `transaction_count` (`INT`)
    *   `fiscal_period` (`INT`)
    *   `fiscal_year` (`INT`)
    *   `week` (`INT`)
    *   `shop_type` (`VARCHAR(40)`)
    *   `warehouse_number` (`BIGINT`)
    *   `bd_industry` (`VARCHAR(200)`)
    *   `business_name` (`VARCHAR(100)`)
    *   `address_line_one` (`VARCHAR(100)`)
    *   `address_line_two` (`VARCHAR(100)`)
    *   `city` (`VARCHAR(100)`)
    *   `state` (`VARCHAR(100)`)
    *   `zip_code` (`VARCHAR(100)`)
    *   `phone` (`VARCHAR(100) NULL`)
    *   `first_name` (`VARCHAR(100) NULL`)
    *   `last_name` (`VARCHAR(100)`)
    *   `email` (`VARCHAR(100) NULL`)
    *   `sic_code` (`BIGINT`)
    *   `industry_description` (`VARCHAR(1000)`)
    *   `primary_transaction` (`BOOLEAN`)
    *   `load_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
    *   `updated_by` (`VARCHAR(20) NULL`)
    *   `updated_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
    *   `oms_company`... (Various order management system audit strings)
    *   `matching_comments` (`TEXT`)
*   **Constraints & Indexes**:
    *   `txn_uniq_sales_reference_id_idx`: `UNIQUE INDEX ON (sales_reference_id)`

---

#### B. Audit & Logging Tables

The system logs pipeline steps, processing volumes, and failures into four dedicated audit tables:

1.  **`batch_audit`**: Tracks staging ingestion volume, overall stages, execution durations, and statuses.
    *   *Fields*: `id` (UUID), `batch_id`, `data_type`, `load_date`, `total_volume`, `success_count`, `stage`, `status`, `start_date`, `end_date`, `comments`.
2.  **`match_audit`**: Tracks daily matching statistics, linking total lead and POS transaction volumes with matching success rates.
    *   *Fields*: `match_id` (UUID), `lead_count`, `pos_count`, `match_count`, `stats`, `status`, `start_date`, `end_date`, `update_date`, `comments`.
3.  **`error_audit`**: Real-time pipeline failure logs mapping entity errors directly to batch and error messages.
    *   *Fields*: `error_log_id` (UUID), `batch_id`, `entity_type`, `entity_id`, `error_message`, `created_at`, `is_processed`, `processed_at`.
4.  **`api_audit`**: Operational logger tracking API sync request states and payload volumes.
    *   *Fields*: `id` (UUID), `batch_id`, `data_type`, `load_date`, `total_volume`, `success_count`, `stage`, `status`, `start_date`, `end_date`, `comments`.

---

#### C. Vector Database Tables (pgvector)

These tables store generated 768-dimensional textual embeddings for semantic similarity scoring.

##### 1. `leads_embeddings`
Stores the semantic vectors generated for the leads data.
*   **Fields**:
    *   `lead_id` (`VARCHAR`)
    *   `combined_field` (`VARCHAR`) -> Unified text representation of the lead (Name + Address + City + State + ZIP)
    *   `business_name` (`VARCHAR`)
    *   `business_address` (`VARCHAR`)
    *   `combined_embedding` (`VECTOR(768)`)
    *   `address_embedding` (`VECTOR(768)`)
    *   `name_embedding` (`VECTOR(768)`)
    *   `updated_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
    *   `warehouse_number` (`INT`)
    *   `fiscal_year` (`INT`)
    *   `fiscal_period` (`INT`)

##### 2. `pos_embeddings`
Stores the semantic vectors generated for the POS transaction records.
*   **Fields**:
    *   `pos_id` (`VARCHAR`)
    *   `account_number` (`BIGINT`)
    *   `combined_field` (`VARCHAR`) -> Unified text representation of the customer purchase record
    *   `business_name` (`VARCHAR`)
    *   `business_address` (`VARCHAR`)
    *   `combined_embedding` (`VECTOR(768)`)
    *   `address_embedding` (`VECTOR(768)`)
    *   `name_embedding` (`VECTOR(768)`)
    *   `load_date` (`TIMESTAMP WITH TIME ZONE` DEFAULT `UTC`)
    *   `warehouse_number` (`INT`)
    *   `fiscal_year` (`INT`)
    *   `fiscal_period` (`INT`)
    *   `week` (`INT`)

---

#### D. Matching Settings Table & Seed Data

##### `match_configuration`
Holds thresholds mapping similarity scores to discrete results classification.
*   **Fields**:
    *   `confidence_level` (`VARCHAR(20) UNIQUE`)
    *   `min_score` (`FLOAT`)
    *   `max_score` (`FLOAT`)
    *   `match_result` (`VARCHAR(10)`)

##### Seed Data (`postgres_resources/costco_db_dml.sql`)
The default score configurations seeded during setup:
```sql
INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score, match_result)
VALUES 
  ('High',     90,      100,    'Match'),
  ('Medium',   85,      89.999, 'Potential'),
  ('Low',      80,      84.999, 'Potential'),
  ('No Match',  0,      79.999, 'No Match')
ON CONFLICT (confidence_level) 
DO UPDATE SET
  min_score    = EXCLUDED.min_score,
  max_score    = EXCLUDED.max_score,
  match_result = EXCLUDED.match_result;
```

---

## 3. Core Database Index Architecture

The database indexes are heavily optimized to ensure fast exact query execution and sub-second multi-dimensional vector searches.

```sql
-- Core Relational and Query B-Tree Indexes
CREATE INDEX IF NOT EXISTS lead_status_index 
    ON "$SCHEMA_NAME".lead (lead_status);

CREATE INDEX IF NOT EXISTS contact_lead_id_idx 
    ON "$SCHEMA_NAME".contact (lead_id);

CREATE INDEX IF NOT EXISTS transaction_lead_id_idx 
    ON "$SCHEMA_NAME".transaction (lead_id);

CREATE INDEX IF NOT EXISTS lead_fiscal_year_idx 
    ON "$SCHEMA_NAME".lead (fiscal_year);

-- Indexing on embedding foreign keys to support swift filtering before joins
CREATE INDEX IF NOT EXISTS warehouse_index_leads  
    ON "$SCHEMA_NAME".leads_embeddings (warehouse_number);
 
CREATE INDEX IF NOT EXISTS warehouse_index_pos  
    ON "$SCHEMA_NAME".pos_embeddings (warehouse_number);

CREATE INDEX IF NOT EXISTS lead_id_indx 
    ON "$SCHEMA_NAME".leads_embeddings (lead_id);
 
CREATE INDEX IF NOT EXISTS pos_id_indx 
    ON "$SCHEMA_NAME".pos_embeddings (pos_id);

-- Composite query index to optimize regional temporal queries
CREATE INDEX IF NOT EXISTS pos_embeddings_warehouse_fiscal_idx 
    ON "$SCHEMA_NAME".pos_embeddings (warehouse_number, fiscal_year, fiscal_period);

-- Highly Optimized HNSW (Hierarchical Navigable Small World) pgvector Indexes
-- Configured specifically with vector_cosine_ops for cosine similarity metrics
CREATE INDEX IF NOT EXISTS leads_combined_embedding_idx 
    ON "$SCHEMA_NAME".leads_embeddings USING hnsw (combined_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS pos_combined_embedding_idx 
    ON "$SCHEMA_NAME".pos_embeddings USING hnsw (combined_embedding vector_cosine_ops);
```

---

## 4. pgvector Extension & Semantic Search Implementation

The matching engine employs Google Vertex AI model API (`text-embedding-004`) to generate 768-dimensional float vectors, representing the semantic meaning of corporate business accounts and retail purchase logs. The pgvector extension stores these vectors as a native postgres `vector(768)` data type.

### Extension Activation
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### The Cosine Distance Operator (`<=>`)
To measure the semantic similarity, the system uses the `<=>` cosine distance operator in PostgreSQL.
*   **Cosine Distance** = `l.combined_embedding <=> s.combined_embedding` (ranges from `0.0` for identical vectors to `2.0` for opposite vectors).
*   **Cosine Similarity Score** = `(1 - (l.combined_embedding <=> s.combined_embedding)) * 100` (rescales similarity to a percentage between `0` and `100`).

---

### Optimized Semantic Search Query Definitions

In the semantic similarity module (`fuzzy_matching_sql.py`), the system executes batch vector matching. Rather than executing an exhaustive cartesian cross-join ($O(N \times M)$) across the tables, the system uses an extremely performant **`CROSS JOIN LATERAL` HNSW query**.

The `LATERAL` block behaves like a SQL `foreach` loop. For every single focused lead, it evaluates POS embeddings sharing the same warehouse, queries the index for the top **20 nearest neighbors** (`LIMIT 20` utilizing the HNSW index), and filters down only to matches that achieve a `combined_field_score >= 80`.

#### A. Warehouse-Scoped Fuzzy Search Query (`query_fuzzy_wh`)
Used when a lead is assigned a designated warehouse. It restricts the nearest-neighbor search space strictly to that warehouse's POS dataset.

```sql
WITH lead_in_focus AS (
    SELECT
        lead_id,
        combined_embedding,
        address_embedding,
        name_embedding,
        warehouse_number,
        fiscal_year,
        fiscal_period,
        business_name,
        business_address
    FROM "$SCHEMA_NAME".leads_embeddings
    WHERE lead_id IN :leads_id_batch AND warehouse_number IS NOT NULL
)
SELECT
    l.lead_id,
    s.pos_id,
    s.account_number,
    s.business_name_score,
    s.combined_field_score,
    s.full_address_score,
    s.warehouse_number,
    s.business_name,
    s.fiscal_year,
    s.fiscal_period,
    s.week
FROM
    lead_in_focus l
    CROSS JOIN LATERAL (
        SELECT
            s.pos_id,
            s.warehouse_number,
            s.fiscal_year,
            s.fiscal_period,
            s.week,
            s.account_number,
            s.combined_embedding,
            s.address_embedding,
            s.name_embedding,
            s.business_name,
            s.business_address,
            (1 - (s.combined_embedding <=> l.combined_embedding)) * 100              AS combined_field_score,
            (1 - (s.address_embedding  <=> l.address_embedding))  * 100              AS full_address_score,
            (1 - COALESCE(NULLIF(s.name_embedding <=> l.name_embedding, 'NaN'::float), 1)) * 100 AS business_name_score
        FROM "$SCHEMA_NAME".pos_embeddings s
        WHERE s.warehouse_number = l.warehouse_number
          AND s.combined_embedding IS NOT NULL
          AND (
                s.fiscal_year > l.fiscal_year
                OR (s.fiscal_year = l.fiscal_year AND s.fiscal_period >= l.fiscal_period)
          )
        ORDER BY s.combined_embedding <=> l.combined_embedding
        LIMIT 20                                   
    ) s
WHERE s.combined_field_score >= 80 
ORDER BY combined_field_score DESC;
```

#### B. Global Fuzzy Search Query for Unassigned Warehouses (`query_fuzzy_null_wh`)
Invoked when a lead's warehouse number is `NULL`. It scans globally across the full transactional dataset using HNSW indices, filtering out temporal inconsistencies before returning matching records.

```sql
WITH lead_in_focus AS (
    SELECT 
        lead_id,
        combined_embedding,
        address_embedding,
        name_embedding,
        warehouse_number,
        fiscal_year,
        fiscal_period,
        business_name,
        business_address
    FROM "$SCHEMA_NAME".leads_embeddings
    WHERE lead_id IN :leads_id_batch AND warehouse_number IS NULL
)
SELECT 
    l.lead_id,
    s.pos_id,
    s.account_number,
    s.business_name_score,
    s.combined_field_score,
    s.full_address_score
FROM 
    lead_in_focus l                               
    CROSS JOIN LATERAL (
        SELECT 
            s.pos_id,
            s.account_number,
            s.combined_embedding,
            s.address_embedding,
            s.name_embedding,
            s.business_name,
            s.business_address,
            (1 - (s.combined_embedding <=> l.combined_embedding)) * 100              AS combined_field_score,
            (1 - (s.address_embedding  <=> l.address_embedding))  * 100              AS full_address_score,
            (1 - COALESCE(NULLIF(s.name_embedding <=> l.name_embedding, 'NaN'::float), 1)) * 100 AS business_name_score
        FROM "$SCHEMA_NAME".pos_embeddings s
        WHERE s.combined_embedding IS NOT NULL
          AND (
                s.fiscal_year > l.fiscal_year
                OR (s.fiscal_year = l.fiscal_year AND s.fiscal_period >= l.fiscal_period)
          )
        ORDER BY s.combined_embedding <=> l.combined_embedding
        LIMIT 20                                   
    ) s
WHERE s.combined_field_score >= 80 
ORDER BY combined_field_score DESC;
```

---

### Scoring Metric Calculation Formula

The matching engine extracts individual cosine similarities for three critical facets of business profiles (combined fields, physical addresses, and corporate names). To produce a single consolidated score, the engine computes a weighted average:

$$\text{Weighted Similarity Score} = \frac{\text{Combined Field Score} + (4 \times \text{Full Address Score}) + (3 \times \text{Business Name Score})}{8}$$

In Python (`fuzzy_matching_sql.py`), this calculation is performed in parallel using Pandas vectorized operations:

```python
master_df["similarity_score"] = (
    (master_df["combined_field_score"]
     + 4 * master_df["full_address_score"]
     + 3 * master_df["business_name_score"]) / 8
)
```

The matching status is subsequently evaluated based on the configuration records fetched from the `match_configuration` database table (e.g. scores $\ge 90$ qualify as a direct `'High'` confidence match).

---

### Architectural Advisory & "Silent Bypass"
As noted in `RISKS_AND_RECOMMENDATIONS.md`, while the PostgreSQL `pgvector` HNSW indexes and semantic matching scripts are highly optimized and fully provisioned, the main orchestrator (`primary_matching.py`) currently relies strictly on deterministic/exact matching rules. Activating this pgvector module (`fuzzy_matching_sql.py`) within the automated production pipeline holds massive potential for boosting match yields on records that fail exact text alignments.
