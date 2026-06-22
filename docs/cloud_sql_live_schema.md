# Cloud SQL Live Schema — leadmgmt

**Instance:** `lead-mgmt-db` (ctoteam:us-central1)  
**Engine:** PostgreSQL 15, pgvector v0.8.1  
**Tier:** db-custom-4-15360 (4 vCPU, 15 GiB RAM), Regional HA  
**Schema:** `leadmgmt`  
**Snapshot date:** 2026-06-22

---

## Table Summary

| Table | Rows | Purpose |
|---|---:|---|
| **lead** | 930 | Sales leads (one per business prospect) |
| **account** | 930 | Business accounts linked to leads (name, address, contact) |
| **contact** | 930 | Contact persons per lead |
| **transaction** | 26,900 | POS transaction records (the "POS" side of matching) |
| **pos_transactions** | 26,900 | Mirror of transaction (legacy/reporting copy) |
| **leads_embeddings** | 930 | Vector embeddings of lead business identity |
| **pos_embeddings** | 18,900 | Vector embeddings of POS transaction identity |
| **match_decision_detail** | 4,953 | Fuzzy match results (lead-to-POS scored pairs) |
| match_audit | 0 | Match run audit log (unused) |
| match_configuration | 0 | Confidence band config (unused, rules in JSON instead) |
| batch_audit | 0 | Data load audit (unused) |
| api_audit | 0 | API call audit (unused) |
| error_audit | 0 | Error log (unused) |

---

## Core Tables

### lead

Primary source of sales leads. Each lead references an account.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| **lead_id** | varchar(20) | NO | PK |
| lead_source | varchar | YES | |
| account_id | varchar | YES | FK to account (indexed) |
| account_number | bigint | YES | |
| lead_status | varchar | YES | Currently all `Open` |
| confidence_level | varchar | YES | |
| membership_number | bigint | YES | |
| warehouse_number | integer | YES | Warehouse assignment |
| fiscal_period | integer | YES | |
| fiscal_year | integer | YES | |
| closed_fiscal_period | integer | YES | |
| closed_fiscal_year | integer | YES | |
| batch_id | uuid | YES | Data load batch |
| load_date | timestamptz | YES | |
| updated_by | varchar | YES | |
| updated_date | timestamptz | YES | |
| **match_result** | varchar | YES | **Currently all NULL** — not written back by pipeline yet |
| week | integer | YES | |

**Indexes:** PK on `lead_id`, btree on `account_id`, composite on `(warehouse_number, fiscal_year, fiscal_period)`

**Current data:** 930 rows. WH 115: 630, WH 569: 300. All `lead_status = 'Open'`, `match_result = NULL`.

---

### account

Business identity for each lead. Source for embedding text (name + address).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| **account_id** | varchar | NO | PK |
| batch_id | uuid | YES | |
| account_number | bigint | YES | |
| type | varchar | YES | |
| **business_name** | varchar | YES | Used for embedding |
| **address_line_one** | varchar | YES | Used for embedding |
| address_line_two | varchar | YES | |
| **city** | varchar | YES | Used for embedding |
| **state** | varchar | YES | Used for embedding |
| **zip_code** | varchar | YES | Used for embedding |
| phone | varchar | YES | Used for deterministic boost |
| email | varchar | YES | Used for deterministic boost |
| industry_code | varchar | YES | |
| bd_industry | varchar | YES | |
| updated_by | varchar | YES | |
| updated_date | timestamptz | YES | |

**Indexes:** PK on `account_id`, btree on `account_number`, btree on `business_name`

---

### transaction (POS)

POS sales transactions. Each row is one transaction for a business at a warehouse. This is the "POS side" that gets matched to leads.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| **pos_id** | varchar(120) | NO | PK |
| sales_reference_id | varchar | YES | |
| account_number | bigint | YES | |
| **lead_id** | varchar | YES | **Currently all NULL** — match writeback not implemented |
| **match_score** | double | YES | **Currently all NULL** |
| **match_type** | varchar | YES | **Currently all NULL** |
| batch_id | uuid | YES | |
| membership_number | bigint | YES | |
| order_amount | double | YES | |
| transaction_count | integer | YES | |
| fiscal_period | integer | YES | |
| fiscal_year | integer | YES | |
| week | integer | YES | |
| shop_type | varchar | YES | |
| warehouse_number | bigint | YES | Warehouse number |
| bd_industry | varchar | YES | |
| **business_name** | varchar | YES | Used for embedding |
| **address_line_one** | varchar | YES | Used for embedding |
| address_line_two | varchar | YES | |
| **city** | varchar | YES | Used for embedding |
| **state** | varchar | YES | Used for embedding |
| **zip_code** | varchar | YES | Used for embedding |
| phone | varchar | YES | |
| first_name | varchar | YES | |
| last_name | varchar | YES | |
| email | varchar | YES | |
| sic_code | bigint | YES | |
| industry_description | varchar | YES | |
| load_date | timestamptz | YES | |
| updated_by | varchar | YES | |
| updated_date | timestamptz | YES | |
| primary_transaction | boolean | YES | |
| oms_company | varchar | YES | OMS data fields (18 columns) |
| oms_company_2 | varchar | YES | |
| oms_email_1 | varchar | YES | |
| oms_email_2 | varchar | YES | |
| oms_email_3 | varchar | YES | |
| oms_phone_1 | varchar | YES | |
| oms_phone_2 | varchar | YES | |
| oms_phone_3 | varchar | YES | |
| oms_cell_1 | varchar | YES | |
| oms_cell_2 | varchar | YES | |
| oms_first_name | varchar | YES | |
| oms_middle_name | varchar | YES | |
| oms_last_name | varchar | YES | |
| oms_address_line_1 | varchar | YES | |
| oms_city | varchar | YES | |
| oms_state | varchar | YES | |
| oms_zip | varchar | YES | |
| oms_address_line_1_v2 | varchar | YES | |
| oms_address_line_2 | varchar | YES | |
| oms_address_line_3 | varchar | YES | |
| oms_address_line_4 | varchar | YES | |
| oms_address_line_5 | varchar | YES | |
| oms_address_line_6 | varchar | YES | |
| oms_city_2 | varchar | YES | |
| oms_state_2 | varchar | YES | |
| oms_zip_2 | varchar | YES | |
| oms_zip_3 | varchar | YES | |
| oms_zip_4 | varchar | YES | |
| matching_comments | text | YES | |
| **is_processed** | boolean | NO | **Currently all `false`** |
| process_datetime | timestamp | YES | |

**Indexes:** PK on `pos_id`, btree on `account_number`, composite on `(warehouse_number, fiscal_year, fiscal_period, week)`

**Current data:** 26,900 rows. All have `lead_id = NULL`, `match_type = NULL`, `is_processed = false`. No exact-match or any match data has been written back.

---

## Embedding Tables

### leads_embeddings

Vector embeddings for lead business identity (name + address).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| lead_id | varchar | YES | References lead.lead_id (no FK constraint, no unique index live) |
| combined_field | varchar | YES | Embedding source text: business_name + address |
| business_name | varchar | YES | Embedding source text |
| business_address | varchar | YES | Embedding source text: address parts joined |
| **combined_embedding** | vector(768) | YES | Used for HNSW recall gate |
| **address_embedding** | vector(768) | YES | Used for precision scoring (weight 4) |
| **name_embedding** | vector(768) | YES | Used for precision scoring (weight 3) |
| updated_date | timestamptz | YES | |
| warehouse_number | integer | YES | |
| fiscal_year | integer | YES | |
| fiscal_period | integer | YES | |

**Live indexes:** btree on `(warehouse_number, fiscal_year, fiscal_period)`

**Missing indexes (defined in schema SQL, not created live):**
- `idx_leads_embeddings_lead_id_unique` — UNIQUE on `lead_id`
- `idx_leads_embeddings_combined_hnsw` — HNSW on `combined_embedding vector_cosine_ops`

**Current data:** 930 rows. WH 115: 630, WH 569: 300. All have embeddings populated.

---

### pos_embeddings

Vector embeddings for POS transaction business identity.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| pos_id | varchar | YES | References transaction.pos_id (no FK, no unique index live) |
| account_number | bigint | YES | |
| combined_field | varchar | YES | Embedding source text |
| business_name | varchar | YES | Embedding source text |
| business_address | varchar | YES | Embedding source text |
| **combined_embedding** | vector(768) | YES | Used for HNSW recall gate |
| **address_embedding** | vector(768) | YES | Used for precision scoring (weight 4) |
| **name_embedding** | vector(768) | YES | Used for precision scoring (weight 3) |
| load_date | timestamptz | YES | |
| warehouse_number | integer | YES | |
| fiscal_year | integer | YES | |
| fiscal_period | integer | YES | |
| week | integer | YES | |

**Live indexes:** btree on `(warehouse_number, fiscal_year, fiscal_period, week)`

**Missing indexes (defined in schema SQL, not created live):**
- `idx_pos_embeddings_pos_id_unique` — UNIQUE on `pos_id`
- `idx_pos_embeddings_combined_hnsw` — HNSW on `combined_embedding vector_cosine_ops`

**Current data:** 18,900 rows (all WH 115). **WH 569: 0 rows** — 8,000 POS transactions waiting to be embedded.

---

## Match Output Table

### match_decision_detail

Stores scored lead-to-POS match pairs from the fuzzy match pipeline.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| **match_run_id** | varchar(100) | NO | PK part 1. Run identifier (e.g., `github-12345-1-115`) |
| **lead_id** | varchar(20) | NO | PK part 2. FK to lead |
| **pos_id** | varchar(120) | NO | PK part 3. FK to transaction |
| warehouse_number | integer | YES | |
| **match_type** | varchar(100) | YES | `Fuzzy` or `Manual Review` (no `Exact` rows exist) |
| **final_score** | double | YES | Weighted: `(4*address + 3*name) / 7` |
| combined_field_score | double | YES | Cosine similarity on combined embedding |
| full_address_score | double | YES | Cosine similarity on address embedding |
| business_name_score | double | YES | Cosine similarity on name embedding |
| weight_formula | varchar(100) | YES | Always `(4*address + 3*name)/7` |
| embedding_model | varchar(100) | YES | `gemini-embedding-001` |
| created_date | timestamptz | YES | Default `CURRENT_TIMESTAMP` |
| analyst_decision | varchar(50) | YES | Human review: Approved/Rejected (all NULL) |
| analyst_comments | text | YES | Human review notes (all NULL) |
| updated_by_analyst | varchar(100) | YES | (all NULL) |
| updated_at_analyst | timestamptz | YES | (all NULL) |

**Constraints:** PK on `(match_run_id, lead_id, pos_id)`, FK to `lead(lead_id)`, FK to `transaction(pos_id)`

**Indexes:** btree on `match_run_id`, btree on `lead_id`

**Current data:** 4,953 rows from single run `jobchain-115-20260620215920` (WH 115 only).
- `Fuzzy`: 3,934 rows (score 84.1–100.0, avg 93.8)
- `Manual Review`: 1,019 rows (score 84.8–94.1, avg 90.8)
- **No `Exact` match_type rows exist.**

---

## Data Flow and Match Writeback Status

```
lead (930)  ──FK──►  account (930)
  │                    │
  │ lead_id            │ business_name, address
  ▼                    ▼
leads_embeddings (930)          ◄── Embedding pipeline reads account, writes here
  │
  │  CROSS JOIN LATERAL (cosine similarity)
  ▼
pos_embeddings (18,900)         ◄── Embedding pipeline reads transaction, writes here
  │
  ▼
match_decision_detail (4,953)   ◄── Fuzzy match writes scored pairs here
  │
  ▼
transaction (26,900)            ◄── NOTHING WRITTEN BACK YET
  lead_id = NULL (all rows)        match_type = NULL (all rows)
  match_score = NULL (all rows)    is_processed = false (all rows)
  
lead (930)                      ◄── NOTHING WRITTEN BACK YET
  match_result = NULL (all rows)
```

**Key observation:** The pipeline currently writes fuzzy match results to `match_decision_detail` and generates a report, but **does not write back** to:
- `transaction.lead_id` / `transaction.match_type` / `transaction.match_score`
- `lead.match_result`

The older pandas pipeline (`lead_match_codebase/fuzzy_matching_sql.py`) had logic to merge fuzzy results onto exact-match output and update transaction fields, but the new Cloud Run pipeline does not include this step.

---

## Exact-Match Guard (Already Implemented)

The fuzzy match in `job_runner.py` **already protects exact matches**. Two functions implement this:

- **`exact_lead_exclusion_clause()`** (line 162): Skips leads that have `match_type IN ('exact', 'deterministic')` in either `match_decision_detail` or `transaction.match_type`.
- **`exact_pos_exclusion_clause()`** (line 185): Skips POS records that already have an exact match via the same check.

These clauses are applied at:
- Lead fetch query (line 685-703): `{exact_lead_clause}` filters lead IDs
- CROSS JOIN LATERAL subquery (line 763): `{exact_pos_clause}` filters POS candidates

The guard types are configurable via `EXACT_MATCH_TYPES` env var (default: `"Exact,Deterministic"`).

**Current state:** No exact matches exist in Cloud SQL yet (all `transaction.match_type = NULL`, no `Exact` rows in `match_decision_detail`), so the guard is a no-op today but will protect data when exact matches are added.

---

## Index Management

### Automated via Workflow

The `ensure-indexes` task in `job_runner.py` creates missing indexes idempotently. It runs as a Cloud Run job (`lead-match-ensure-indexes`) in the workflow after embeddings and before fuzzy match.

Indexes managed:
1. `idx_leads_embeddings_lead_id_unique` — UNIQUE on `lead_id`
2. `idx_pos_embeddings_pos_id_unique` — UNIQUE on `pos_id`
3. `idx_leads_embeddings_combined_hnsw` — HNSW on `combined_embedding` (m=16, ef_construction=128)
4. `idx_pos_embeddings_combined_hnsw` — HNSW on `combined_embedding` (m=16, ef_construction=128)

### Manual Runbook

For manual application or other projects, use `schema/lead_match_hnsw_indexes.sql`:

```sql
-- Unique constraints
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_lead_id_unique
    ON leadmgmt.leads_embeddings (lead_id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_pos_id_unique
    ON leadmgmt.pos_embeddings (pos_id);

-- HNSW vector indexes
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
