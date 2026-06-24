# Leadmgmt Schema — Data Model Reference

**Database:** Cloud SQL PostgreSQL (`ctoteam:us-central1:lead-mgmt-db`)
**Schema:** `leadmgmt`
**Captured:** 2026-06-24

---

## Entity Relationship Overview

```
account (3,110)
  └── lead (3,475)
        ├── contact (3,475)
        ├── leads_embeddings (1,871)
        └── match_decision_detail (107,959) ──┐
                                               │
transaction (92,400)                           │
  └── pos_embeddings (65,877)                  │
  └── match_decision_detail ──────────────────┘

match_audit (20)           — run-level tracking
match_configuration (0)    — scoring band config
pos_transactions (92,400)  — legacy / parallel POS table
api_audit (0)              — API load tracking
batch_audit (0)            — batch load tracking
error_audit (0)            — error logging
```

---

## Table-by-Table Reference

---

### 1. `account` — 3,110 rows

**Purpose:** Master record for a business account sourced from the CRM (Salesforce/ServiceNow). Each account represents a prospective or existing Costco business customer.

| Column | Type | Notes |
|---|---|---|
| `account_id` | varchar | **PK** — CRM account identifier |
| `batch_id` | uuid | Load batch reference |
| `account_number` | bigint | Costco account number (joins to POS) |
| `type` | varchar | Account type (Business, etc.) |
| `business_name` | varchar | Legal business name |
| `address_line_one/two` | varchar | Business address |
| `city / state / zip_code` | varchar | Address components |
| `phone / email` | varchar | Contact details |
| `industry_code` | varchar | SIC/industry classification |
| `bd_industry` | varchar | BD-assigned industry label |
| `updated_by / updated_date` | varchar / timestamptz | Audit fields |

**Relationships:** Referenced by `lead.account_id`
**Indexes:** `account_number`, `business_name`

**Data model note:** This is the upstream identity anchor. Every lead is tied to one account. Account identity fields (business_name, address) feed into the fuzzy matching embeddings.

---

### 2. `lead` — 3,475 rows

**Purpose:** A sales lead — a specific opportunity created in the CRM for a warehouse, fiscal period, and account. The central entity of the entire pipeline.

| Column | Type | Notes |
|---|---|---|
| `lead_id` | varchar(30) | **PK** — lead identifier (`LEAD...`) |
| `lead_source` | varchar | Origin system |
| `account_id` | varchar | **FK → account.account_id** |
| `account_number` | bigint | Denormalized account number |
| `lead_status` | varchar | Current status (Open, Closed, etc.) |
| `confidence_level` | varchar | Matching confidence tier |
| `membership_number` | bigint | Costco membership number |
| `warehouse_number` | int | Costco warehouse (e.g. 947) |
| `fiscal_period / fiscal_year` | int | Fiscal reporting period |
| `closed_fiscal_period / year` | int | Period when lead was closed |
| `match_result` | varchar | Final match outcome written back |
| `week` | int | Fiscal week |
| `batch_id / load_date` | uuid / timestamptz | Load tracking |

**Relationships:** FK to `account`. Referenced by `contact`, `leads_embeddings`, `match_decision_detail`, `transaction`
**Indexes:** `account_id`, `(warehouse_number, fiscal_period, fiscal_year)`

**Data model note:** `warehouse_number` + `fiscal_period` + `fiscal_year` is the natural partition key for all matching runs. `match_result` is the write-back field updated by the engine after a match run completes.

---

### 3. `contact` — 3,475 rows

**Purpose:** Primary contact person associated with a lead. One contact per lead in current data (1:1 cardinality observed).

| Column | Type | Notes |
|---|---|---|
| `contact_id` | varchar | **PK** |
| `lead_id` | varchar | **FK → lead.lead_id** |
| `first_name / last_name` | varchar | Contact name |
| `email / phone` | varchar | Contact details |
| `membership_number` | bigint | Costco membership |
| `job_title` | varchar | Role at the business |
| `batch_id / updated_date` | uuid / timestamptz | Audit fields |

**Relationships:** FK to `lead`
**Indexes:** `lead_id`

**Data model note:** Contact details (email, phone, membership_number) are used as secondary signals in exact matching. Not currently used in fuzzy embeddings.

---

### 4. `transaction` — 92,400 rows

**Purpose:** POS (Point of Sale) transaction records from Costco warehouses. Each row represents a unique POS event identified by `pos_id`. This is the **primary POS table** used by the matching engine.

| Column | Type | Notes |
|---|---|---|
| `pos_id` | varchar(120) | **PK** — POS transaction ID (`POS...`) |
| `sales_reference_id` | varchar | External sales reference |
| `account_number` | bigint | Costco account number |
| `lead_id` | varchar | **FK → lead.lead_id** — written back after match |
| `match_score / match_type` | double / varchar | Match result written back |
| `membership_number` | bigint | Buyer membership |
| `order_amount` | double | Transaction value |
| `transaction_count` | int | Number of sub-transactions |
| `fiscal_period / fiscal_year / week` | int | Reporting period |
| `warehouse_number` | bigint | Costco warehouse |
| `business_name` | varchar | Business name at point of sale |
| `address_line_one/two, city, state, zip_code` | varchar | POS address |
| `phone / email / first_name / last_name` | varchar | Contact at POS |
| `sic_code / industry_description` | bigint / varchar | Industry classification |
| `primary_transaction` | boolean | Whether this is the primary POS row for the account |
| `shop_type` | varchar | Type of Costco shop |
| `oms_*` columns (25+) | varchar | OMS system identity fields (company, address, contact) |
| `matching_comments` | text | Manual analyst notes |
| `is_processed / process_datetime` | boolean / timestamp | Processing status |

**Relationships:** Referenced by `pos_embeddings`, `match_decision_detail`
**Indexes:** `account_number`, `(warehouse_number, fiscal_period, fiscal_year)`

**Data model note:** The `oms_*` columns (25 of them) carry enriched identity data from the Order Management System — these include alternate business names, multiple addresses, multiple phones/emails. They serve as supplemental fuzzy matching signals and are the reason `transaction` is richer than `pos_transactions`.

---

### 5. `pos_transactions` — 92,400 rows

**Purpose:** Parallel POS table — same row count as `transaction`. Appears to be an earlier or alternate load of POS data, missing the `oms_*` enrichment columns and `matching_comments`.

| Notable differences from `transaction` | |
|---|---|
| Has `sic_description` instead of `industry_description` | Column renamed between loads |
| Missing all `oms_*` columns | No OMS enrichment |
| Missing `matching_comments`, `is_processed`, `process_datetime` | No workflow state |
| `warehouse_number` is `bigint` (same as transaction) | |

**Data model note:** Treat `pos_transactions` as the **raw/staging POS load**. `transaction` is the enriched working copy used by the matching engine. Both have 92,400 rows suggesting they were loaded from the same source extract but `transaction` was subsequently enriched with OMS data.

---

### 6. `leads_embeddings` — 1,871 rows

**Purpose:** Vector embedding store for leads. Stores the pre-computed Gemini embeddings used for semantic fuzzy matching. Not all 3,475 leads have embeddings — only those in active matching scope.

| Column | Type | Notes |
|---|---|---|
| `lead_id` | varchar | **FK → lead.lead_id** (unique) |
| `combined_field` | varchar | Concatenated text used for embedding |
| `business_name` | varchar | Normalized business name |
| `business_address` | varchar | Normalized address |
| `combined_embedding` | vector(768) | Gemini `gemini-embedding-001` full embedding |
| `address_embedding` | vector(768) | Address-only embedding |
| `name_embedding` | vector(768) | Business name-only embedding |
| `warehouse_number / fiscal_year / fiscal_period` | int | Partition key |
| `updated_date` | timestamptz | Last embedding refresh |

**Indexes:** HNSW index on `combined_embedding` (ANN search), unique on `lead_id`, period composite

**Data model note:** Three embeddings per lead (combined, address, name) allow the engine to score address similarity and name similarity independently, then combine with the weighted formula `(4×address + 3×name) / 7`.

---

### 7. `pos_embeddings` — 65,877 rows

**Purpose:** Vector embedding store for POS transactions. Same structure as `leads_embeddings` but for the POS side. Only a subset of 92,400 POS rows have embeddings (those in active warehouse/period scope).

| Column | Type | Notes |
|---|---|---|
| `pos_id` | varchar | **FK → transaction.pos_id** (unique) |
| `account_number` | bigint | Costco account number |
| `combined_field` | varchar | Text used for embedding |
| `business_name / business_address` | varchar | Normalized fields |
| `combined_embedding` | vector(768) | Full embedding |
| `address_embedding` | vector(768) | Address-only embedding |
| `name_embedding` | vector(768) | Business name-only embedding |
| `warehouse_number / fiscal_year / fiscal_period / week` | int | Partition key |
| `load_date` | timestamptz | Load timestamp |

**Indexes:** HNSW index on `combined_embedding`, unique on `pos_id`, period composite

**Data model note:** The HNSW index enables sub-second approximate nearest-neighbor search across 65K+ vectors. This is the core of the fuzzy match recall step — leads query against this index to find candidate POS records.

---

### 8. `match_decision_detail` — 107,959 rows

**Purpose:** The primary output table of the matching engine. Every lead×POS candidate pair that passes the scoring floor (≥70) gets a row here with its scores, match type, and AI reasoning. This is the table downstream systems and analysts consume.

| Column | Type | Notes |
|---|---|---|
| `match_run_id` | varchar(100) | **PK (part 1)** — run identifier (e.g. `github-28033272045-1-947`) |
| `lead_id` | varchar(30) | **PK (part 2) / FK → lead.lead_id** |
| `pos_id` | varchar(120) | **PK (part 3) / FK → transaction.pos_id** |
| `warehouse_number` | int | Warehouse scope |
| `match_type` | varchar(100) | `exact` or `fuzzy` |
| `final_score` | double | Weighted composite score |
| `combined_field_score` | double | Semantic similarity (ANN recall) |
| `full_address_score` | double | Address fuzzy score |
| `business_name_score` | double | Business name fuzzy score |
| `weight_formula` | varchar(100) | Formula applied e.g. `(4*addr+3*name)/7` |
| `embedding_model` | varchar(100) | Model used for embeddings |
| `created_date` | timestamptz | Row creation (defaults to now) |
| `analyst_decision` | varchar(50) | Human review outcome |
| `analyst_comments` | text | Analyst notes |
| `updated_by_analyst / updated_at_analyst` | varchar / timestamptz | Analyst audit |
| `match_reasoning` | text | AI-generated row-level explanation |

**Composite PK:** `(match_run_id, lead_id, pos_id)` — multiple runs accumulate; each run is a full snapshot.
**Indexes:** `(match_run_id)`, `lead_id`

**Data model note:** 107,959 rows across 20 runs (avg ~5,400 per run). The most recent warehouse 947 run added 3,463 rows. `match_reasoning` is the Gemini-generated per-row explanation added by the analysis workflow — it is never used to decide `final_score` or `match_type`, only to explain them.

---

### 9. `match_audit` — 20 rows

**Purpose:** One row per match engine run. Tracks run-level statistics and status.

| Column | Type | Notes |
|---|---|---|
| `match_id` | uuid | **PK** — run identifier |
| `lead_count / pos_count / match_count` | int | Volume metrics |
| `stats` | varchar | JSON or summary stats |
| `status` | varchar | Run status |
| `start_date / end_date / update_date` | timestamptz | Run timing |
| `comments` | text | Run notes or error details |

**Data model note:** 20 rows = 20 historical runs. Used for observability and to prevent duplicate runs.

---

### 10. `match_configuration` — 0 rows

**Purpose:** Intended to store scoring band configuration (min/max score → confidence level mapping). Currently empty — configuration is hardcoded in `lead_to_pos_match_rules.json`.

| Column | Type | Notes |
|---|---|---|
| `confidence_level` | varchar | e.g. `Matching High`, `Potential Medium` |
| `min_score / max_score` | double | Score band bounds |
| `match_result` | varchar | Outcome label |

**Data model note:** This table is the right place to externalize the scoring thresholds currently in the JSON rules file. Populating it would allow threshold changes without a code deploy.

---

### 11. `api_audit` — 0 rows

**Purpose:** Tracks API-based data loads (vs batch file loads). Currently unused — all loads are batch-based.

| Column | Type | Notes |
|---|---|---|
| `id / batch_id` | uuid | Load identifiers |
| `data_type` | varchar | Type of data loaded |
| `total_volume / success_count` | int | Load volume metrics |
| `stage / status` | varchar | Pipeline stage and status |
| `start_date / end_date` | timestamptz | Load window |
| `comments` | text | Notes |

---

### 12. `batch_audit` — 0 rows

**Purpose:** Tracks batch file data loads. Same structure as `api_audit` but for file-based ingestion. Currently unused — loads are tracked informally via `batch_id` columns on entity tables.

---

### 13. `error_audit` — 0 rows

**Purpose:** Central error log for any entity that fails to load or process.

| Column | Type | Notes |
|---|---|---|
| `error_log_id` | uuid | **PK** |
| `entity_type / entity_id` | varchar | What failed (e.g. `lead`, `LEAD123`) |
| `error_message` | text | Error detail |
| `created_at` | timestamptz | When error occurred |
| `is_processed / processed_at` | boolean / timestamptz | Whether error was resolved |
| `batch_id` | uuid | Which load batch caused the error |

---

## Data Flow Summary

```
[CRM / ServiceNow]
      │
      ▼
  account  ──────────────────────────────────────────────┐
      │                                                    │
      ▼                                                    │
    lead  ──► contact                                     │
      │                                                    │
      ▼                                                    │
leads_embeddings                                          │
  (Gemini vector)                                         │
      │                                                    │
      │         [Costco POS Extract]                      │
      │               │                                    │
      │               ▼                                    │
      │         pos_transactions (raw)                    │
      │               │ enrich with OMS                   │
      │               ▼                                    │
      │         transaction ──► pos_embeddings            │
      │                          (Gemini vector)           │
      │                                                    │
      ▼                                                    │
[Matching Engine]  ◄──────────────────────────────────────┘
      │  exact SQL match (score=100)
      │  fuzzy ANN search (score 70-99.99)
      │
      ▼
match_decision_detail  ◄── match_reasoning (Gemini analysis)
      │
      ▼
match_audit (run summary)
      │
      ▼
[ServiceNow write-back]
lead.match_result updated
transaction.lead_id / match_score updated
```

---

## Key Design Observations

| Observation | Impact |
|---|---|
| `transaction` and `pos_transactions` are identical in row count (92,400) but `transaction` has 25+ OMS columns | Use `transaction` as the authoritative POS table; `pos_transactions` may be safe to deprecate |
| `match_configuration` is empty | Scoring bands are hardcoded in JSON; populating this table would enable no-deploy threshold changes |
| `leads_embeddings` has 1,871 rows vs 3,475 leads | ~1,600 leads have no embedding — likely out-of-scope or excluded by warehouse/period filter |
| `match_decision_detail` composite PK includes `match_run_id` | Multiple run snapshots accumulate; queries without a `match_run_id` filter will scan all 107,959 rows |
| `match_reasoning` is text, not JSON | Reasoning is free-form prose; not queryable by field — consider structured JSON if downstream analytics needed |
| All three audit tables (`api_audit`, `batch_audit`, `error_audit`) have 0 rows | Audit infrastructure is built but not wired into the load pipeline |
| HNSW indexes on embedding columns | Approximate nearest-neighbor search is enabled; `ivfflat` alternative not present |
