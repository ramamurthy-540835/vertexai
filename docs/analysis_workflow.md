# Match Analysis Workflow

Post-hoc deterministic per-row reasoning + Gemini 3.5 Flash distribution narrative. Runs after match engine completes.

## Architecture

**Three stages, all post-hoc:**

```
Match Engine                     Analysis Workflow
   ↓                                  ↓
Write to Cloud SQL           Read matches.csv from GCS
Write to GCS staging                 ↓
Write _READY.json        Stage 1: Deterministic reasoning
   ↓                    - Read component scores
   └→ [Trigger]         - Format per-row reasoning strings
                         - Verify arithmetic from rules JSON
                                  ↓
                        Stage 2: Distribution facts
                        - Histogram, peak, tail, artifacts
                        - Review workload, post-id signals
                                  ↓
                        Stage 3: Gemini narrative
                        - Call Gemini 3.5 Flash
                        - Write comparative_analysis.md
                        - (Optional) Write match_reasoning to Cloud SQL
```

## Data Flow

1. **Trigger**: Workflow manual dispatch or automatic after match run completes (_READY.json exists)
2. **Input**: `gs://lead-match-ctoteam/reports/lead_match/<project>/<wh>/<run>/matches.csv`
3. **Processing**: Python (pandas, no Spark) — ~8.5K rows/warehouse
4. **Output**:
   - `gs://lead-match-ctoteam/reports/lead_match/<project>/<wh>/<run>/comparative_analysis.md` (Gemini narrative)
   - (Optional) `leadmgmt.match_decision_detail.match_reasoning` (per-row reasoning in Cloud SQL)

## Workflow Files

### `.github/workflows/lead_match_analysis.yml`

**Trigger**:
- Manual via `workflow_dispatch` (provide `warehouse`, `run_id`)
- Automatic after `lead_match_semantic_workflow.yml` succeeds (reads run from context)

**Steps**:
1. **Verify _READY.json exists** — gate to ensure match run is complete
2. **Download matches.csv** from GCS
3. **Run analysis** — `scripts/analyze_match_distribution.py`
4. **Upload narrative** — to GCS
5. **Optional Cloud SQL write** — per-row reasoning (requires connection setup)
6. **Validate** — check narrative exists, report metrics

### `scripts/analyze_match_distribution.py`

**Input**: matches.csv with columns:
- `lead_id`, `pos_id`, `match_run_id`
- `final_score`, `full_address_score`, `business_name_score`, `combined_field_score`
- `match_type`, `band`

**Output**:
- `comparative_analysis.md` (Gemini narrative)
- (Optional) Cloud SQL UPDATE to `match_reasoning`

**Key functions**:

| Function | Purpose |
| --- | --- |
| `load_rules_json()` | Load weights/formula from lead_to_pos_match_rules.json |
| `read_matches_csv_from_gcs()` | Fetch matches.csv from GCS |
| `generate_per_row_reasoning()` | Format reasoning string from scores (deterministic, no LLM) |
| `compute_distribution_facts()` | Histogram, peak, tail, artifact check, workload counts |
| `call_gemini_analysis()` | Call Gemini 3.5 Flash with facts + system prompt |
| `write_reasoning_to_cloud_sql()` | Batch UPDATE match_reasoning, scoped by match_run_id |

## Per-Row Reasoning Format

**Fuzzy match** (AI-inferred):
```
Address 99.42 (w4) + Name 100.0 (w3) => (4*99.42+3*100.0)/7 = 99.67. Band: Matching High. Recall gate: combined_field 85.21 (>= 65 pass). address-driven.
```

**Exact match** (deterministic):
```
Deterministic field match (exact-sql); identity fields agree. Score 100, authoritative. Not AI-inferred.
```

**Weights from rules JSON** (never hardcoded):
- address weight: 4
- name weight: 3
- denominator: 7 (sum of weights)
- recall gate threshold: 65

## Distribution Facts (Deterministic)

Computed before Gemini call:

```json
{
  "total_rows": 8511,
  "histogram": { "88": 245, "89": 312, "90": 418, ... },
  "band_counts": {
    "Matching High": 1850,
    "Potential (Medium)": 2100,
    "Potential (Low)": 1833,
    "No Match": 2728
  },
  "statistics": {
    "mean": 82.45,
    "median": 85.10,
    "std": 12.34,
    "min": 70.0,
    "max": 99.99
  },
  "peak_bin": "Interval(85, 90]",
  "peak_count": 418,
  "peak_percentage": 4.9,
  "tail_volume": 1833,
  "tail_percentage": 21.5,
  "review_workload": 3933,
  "review_percentage": 46.2,
  "artifact_flag": false
}
```

## Gemini Narrative (Stage 3)

**Model**: `gemini-3.5-flash` (configurable via `GEMINI_MODEL` env var)

**System prompt**: Analyze score distribution, explain shape, report 4 post-identification signals.

**Output sections**:
1. **Distribution Interpretation** — where peak sits, shape (normal/skewed/flat)
2. **Post-Identification Findings** (4 signals):
   - **Threshold sensitivity**: Peak within 2 points of cutoff (85, 90)? → recommend threshold tuning
   - **Tail/edge quality**: Review workload volume → human queue size
   - **Artifacts**: Spikes/gaps in histogram → flag scoring bugs
   - **Review workload**: Potential+Manual Review rows → ServiceNow queue projection
3. **Recommended Actions** for each signal
4. **Caveat**: Bands are starting priors; final tuning needs labeled validation set

**Safety**:
- Defensive parsing (check `ok` + `content-type`, strip code fences)
- Try/catch on API errors — never crash pipeline
- Falls back gracefully if model unavailable

## Cloud SQL Write-Back (Optional, Stage 4)

**Column**: `leadmgmt.match_decision_detail.match_reasoning` (nullable TEXT)

**Safety constraints**:
- **Exclusive ownership**: Analysis workflow ONLY writes `match_reasoning`. Never touches `match_type`, `final_score`, `lifecycle_state`, `analyst_comments`, or any engine column.
- **Scoped by match_run_id**: Every UPDATE includes `WHERE match_run_id = :run`. Re-runs are idempotent — only this run's rows are updated.
- **Batched**: 1000 rows/transaction. Progress logged.
- **Verified arithmetic**: Before writing, confirms `(4*addr + 3*name)/7 == final_score` within 0.01 tolerance.

**SQL**:
```sql
UPDATE "leadmgmt"."match_decision_detail"
SET "match_reasoning" = :r
WHERE "match_run_id" = :run_id
  AND "lead_id" = :l
  AND "pos_id" = :p
```

## Reporting App Integration

### AnalysisPanel Component

Displays narrative markdown in `/search`, `/analysis`, and embedded views.

```tsx
<AnalysisPanel warehouse="115" runId="codex-20260623031813-115" />
```

### API Route: `/api/analysis/narrative`

Fetches `comparative_analysis.md` from GCS.

**Query params**:
- `warehouse` — warehouse ID
- `run_id` — match run ID

**Response**: Markdown text, status 404 if not found.

### New Page: `/analysis`

Full-screen view of distribution analysis narrative.

URL: `https://<service>/analysis?warehouse=115&run_id=codex-20260623031813-115`

### Navigation

Added "Analysis" link to main nav (between "Search" and "Graph").

## Environment Variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `BUCKET_NAME` | GCS bucket | `lead-match-ctoteam` |
| `PROJECT_ID` | GCP project | `ctoteam` |
| `WAREHOUSE` | Warehouse number | (from input) |
| `RUN_ID` | Match run ID | (from input) |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.5-flash` |
| `GOOGLE_API_KEY` | Gemini API key (ADC if not set) | (from ADC) |

## Running the Workflow

### Manual Dispatch

GitHub UI → Actions → lead_match_analysis.yml → Run workflow

**Inputs**:
- `warehouse`: `115`
- `run_id`: `codex-20260623031813-115`
- `bucket`: `lead-match-ctoteam` (default)

### Automatic (After Match Run)

No action needed. Triggers automatically when match workflow succeeds.

### Local Testing

```bash
python scripts/analyze_match_distribution.py \
  --bucket lead-match-ctoteam \
  --warehouse 115 \
  --run-id codex-20260623031813-115 \
  --rules-json lead_match_runtime/lead_to_pos_match_rules.json
```

Requires GCS access (ADC auth) and optional Gemini API key.

## Validation

### Check Narrative Exists

```bash
gsutil cat gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/codex-20260623031813-115/comparative_analysis.md
```

### Validate Cloud SQL (if enabled)

```bash
python scripts/validate_match_analysis.py \
  --warehouse 115 \
  --run-id codex-20260623031813-115 \
  --matches-csv matches.csv \
  --db-connection-string "postgresql://user:pass@host/leadmgmt"
```

Checks:
- Row count: `match_reasoning` populated for all CSV rows
- Engine columns: untouched
- Arithmetic: 3 samples verified

## Constraints & Guarantees

✅ **What it does**:
- Reads matches.csv (deterministic, post-hoc)
- Formats reasoning strings (no LLM involved)
- Computes distribution facts (Python, deterministic)
- Calls Gemini 3.5 Flash for narrative only (not per-row)
- Writes to match_reasoning column exclusively (safe, scoped)
- Publishes narrative to GCS (read-only, idempotent)

❌ **What it does NOT do**:
- Modify matching engine or its columns
- Touch other runs' data (scoped by match_run_id)
- Hardcode weights or formula (reads from rules JSON)
- Overwrite analyst_comments or matching_comments
- Race the engine (gated by _READY.json)

## Troubleshooting

| Issue | Diagnosis | Fix |
| --- | --- | --- |
| `_READY.json not found` | Match run still in progress | Wait for match workflow to complete |
| `Gemini call failed` | API unavailable or quota | Check GOOGLE_API_KEY, Vertex API quota |
| `matches.csv row count mismatch` | Cloud SQL write failed | Check IAM, connection string, network access |
| `Narrative is empty or very short` | Gemini returned no content | Check logs, try manual Gemini call, validate API key |
| `analysis/narrative endpoint 404` | GCS path wrong or bucket access denied | Verify bucket name, project ID, GCS permissions |

## Next Steps

1. **Deploy**: Merge `.github/workflows/lead_match_analysis.yml` to main
2. **Test**: Run on wh-115 run `codex-20260623031813-115` (manual dispatch)
3. **Monitor**: Check narrative in GCS, validate in reporting app
4. **Enable Cloud SQL**: If Cloud SQL write-back needed, configure private service connection and run validation script
5. **Iterate**: Refine Gemini prompt based on narrative quality

## References

- Rules JSON: `lead_match_runtime/lead_to_pos_match_rules.json`
- Schema: `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql` (table `match_decision_detail`)
- Reporting app: `lead_match_reporting_app/`
- Match workflow: `.github/workflows/lead_match_semantic_workflow.yml`
