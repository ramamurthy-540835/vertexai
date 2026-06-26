# update_source_data.py — Analysis for Fuzzy Layer 2

## Code Status: WORKS AS-IS FOR FUZZY

The production code handles blank `account_number` correctly:
```python
leads_dataframe['account_number'] = leads_dataframe['account_number'].astype('Int64')
```
`Int64` is pandas nullable integer — NaN/blank stays as `<NA>`, no crash.

## What the code does (6 steps)

### Step 1: Load CSV + minor transforms
- Reads final match CSV from GCS (our `primary_match_output_merged.csv`)
- Renames `similarity_score` → `match_score`
- Adds `updated_by = 'GCP'`
- **Works with our merged CSV: YES** — same 36-column schema

### Step 2: Extract CE lead IDs
- Finds rows where `closed_existing_flag = True`
- Collects unique `lead_id` list
- **Works with our merged CSV: YES** — if CE stubs are included in merge

### Step 3: Build pos_dataframe → UPDATE transaction table
- Filter: `match_result IN ('Match', 'Potential')`
- Columns: pos_id, lead_id, match_type, match_score, updated_by, updated_date, primary_transaction, matching_comments
- Dedup: by pos_id, keep highest match_score
- Temp table upsert: INSERT ON CONFLICT DO UPDATE
- **Works with our merged CSV: YES** — all columns present

### Step 4: Build leads_dataframe → UPDATE lead table
- Filter: `match_result IN ('Match', 'Potential')`
- Columns: lead_id, account_number, match_result, updated_date, updated_by
- Dedup: by lead_id, keep highest match_score
- `account_number.astype('Int64')` — handles blanks safely
- Temp table upsert: INSERT ON CONFLICT DO UPDATE
- **Works with our merged CSV: YES** — Int64 handles blank account_number

### Step 5: CE lead status update
- Direct UPDATE: `SET lead_status = 'Closed - Existing'`
- Batched IN-list (5000 per batch)
- **Works with our merged CSV: YES** — if CE stubs included

### Step 6: Mark transactions processed
- Streams `processed_pos_ids.csv` manifest from GCS into staging table
- Single set-based UPDATE join (not row-by-row)
- NON-FATAL: if fails, rows re-scanned next cycle
- **Needs adaptation**: our pipeline doesn't produce `processed_pos_ids.csv` manifest

## Dependencies needed to run

| Dependency | What it is | Our equivalent |
|---|---|---|
| `JobConfig(config_file_path)` | Reads config.ini for DB, queries, GCS paths | Our `lead_to_pos_match_rules.json` + env vars |
| `db_config.get_engine()` | SQLAlchemy engine via Cloud SQL Connector + IAM auth | We use password auth via pg8000 |
| `query_config.create_temp_table_*` | SQL DDL from config.ini | Need to write these 2 queries |
| `query_config.insert_query_*` | SQL upsert from config.ini | Need to write these 2 queries |
| `load_file_from_gcs()` | Utility to download CSV from GCS into pandas | `pd.read_csv` after `gcloud storage cp` |
| `processed_pos_ids.csv` | 20M-row manifest of all scanned POS | We can generate from merged CSV pos_ids |

## What needs to change for our pipeline

### Option A: Use this code directly
- Write a thin adapter that creates a `JobConfig`-compatible object from our JSON config
- Write the 4 SQL queries (create temp tables + upsert queries)
- Replace IAM auth engine with our password auth engine
- Generate `processed_pos_ids.csv` from merged CSV

### Option B: Write a standalone version
- Same logic, fewer dependencies
- Direct pg8000/SQLAlchemy connection using our existing `business_rules.py` config
- Same temp-table upsert pattern
- Same dedup logic
- Skip the `processed_pos_ids.csv` manifest — use pos_ids from merged CSV directly

## Conclusion
The core logic (steps 1-5) works with our merged CSV unchanged. The only adaptation needed is the plumbing: config system, DB connection, SQL queries, and the processed manifest.
