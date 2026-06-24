# Analysis Workflow: Quick Start

## One-Sentence Summary
Post-hoc Python job reads match scores, formats per-row reasoning (deterministic), calls Gemini 3.5 Flash for distribution narrative, writes to Cloud SQL and GCS.

## Run It

### GitHub UI (Easy)
Actions → lead_match_analysis.yml → Run workflow
- `warehouse`: 115
- `run_id`: codex-20260623031813-115

### Local (For Testing)
```bash
python scripts/analyze_match_distribution.py \
  --bucket lead-match-ctoteam \
  --warehouse 115 \
  --run-id codex-20260623031813-115 \
  --rules-json lead_match_runtime/lead_to_pos_match_rules.json
```

Requires: `pip install pandas google-cloud-storage google-generativeai`

## What It Does

| Stage | What | Input | Output | LLM? |
|-------|------|-------|--------|------|
| 1 | Per-row reasoning | matches.csv component scores | Formatted strings | ❌ |
| 2 | Distribution facts | matches.csv | Histogram, peak, tail, workload | ❌ |
| 3 | Narrative analysis | Facts + rules JSON | comparative_analysis.md | ✅ (Gemini 3.5 Flash) |
| 4 | Cloud SQL write-back | Reasoning strings + match_run_id | match_reasoning column | ❌ |

## Per-Row Reasoning Examples

**Fuzzy**:
```
Address 99.42 (w4) + Name 100.0 (w3) => (4*99.42+3*100.0)/7 = 99.67. 
Band: Matching High. Recall gate: combined_field 85.21 (>= 65 pass). address-driven.
```

**Exact**:
```
Deterministic field match (exact-sql); identity fields agree. Score 100, authoritative.
```

## Formula (From Rules JSON)

```
final_score = (4 * full_address_score + 3 * business_name_score) / 7
```

- **Weights** (w4=address, w3=name) — read from rules JSON
- **Denominator** (7 = 4+3) — read from rules JSON
- **Recall gate** (combined_field >= 65) — read from rules JSON
- ✅ **NEVER hardcoded** — JSON is source of truth

## Distribution "Post-Identification" Signals

The Gemini narrative reports 4 signals that shape next actions:

1. **Threshold sensitivity** — Peak within 2 points of cutoff (85, 90)?
   - Action: Prioritize threshold tuning with labeled set

2. **Tail/edge quality** — How many weak matches (70-84.999)?
   - Action: Size review queue, validate recall gate

3. **Artifacts** — Spikes (>15% in one bin) or gaps?
   - Action: Debug scoring pipeline

4. **Review workload** — Total Potential+Manual Review rows?
   - Action: Estimate ServiceNow queue per warehouse

## Files Overview

| File | Purpose |
|------|---------|
| `.github/workflows/lead_match_analysis.yml` | Workflow: trigger, gating, steps |
| `scripts/analyze_match_distribution.py` | Core logic: reasoning + facts + Gemini |
| `scripts/validate_match_analysis.py` | Post-run validation |
| `docs/analysis_workflow.md` | Full documentation |
| `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql` | Added `match_reasoning` column |
| `lead_match_reporting_app/components/AnalysisPanel.tsx` | React component (displays narrative) |
| `lead_match_reporting_app/app/api/analysis/narrative/route.ts` | API endpoint (serves markdown) |
| `lead_match_reporting_app/app/analysis/page.tsx` | Full-screen analysis view |

## View Results

### In GCS
```bash
gsutil cat gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/<run>/comparative_analysis.md
```

### In Reporting App
- URL: `https://<service>/analysis?warehouse=115&run_id=<run>`
- Also embedded in `/search` page

### In Cloud SQL (Optional)
```sql
SELECT match_reasoning FROM leadmgmt.match_decision_detail
WHERE match_run_id = 'codex-20260623031813-115' LIMIT 3;
```

## Safety Checks

✅ **Protected**:
- Separate column `match_reasoning` (no overwrite hazard)
- Scoped by `match_run_id` (idempotent, no cross-run)
- Gated by `_READY.json` (no race with engine)
- Verified arithmetic (|actual - expected| <= 0.01)
- Weights from rules JSON (never hardcoded 4, 3, 7)

❌ **Never touched**:
- match_type, final_score, lifecycle_state (engine columns)
- matching_comments (engine owns)
- analyst_comments (human owns)

## Troubleshooting

| Problem | Check |
|---------|-------|
| Workflow won't start | _READY.json exists? Match run complete? |
| Empty narrative | Gemini API key set? `GOOGLE_API_KEY` in secrets? |
| Cloud SQL write fails | IAM perms? Private service connection? |
| Narrative in GCS but not in app | Bucket/project ID correct? App has GCS perms? |

## Validate It

```bash
python scripts/validate_match_analysis.py \
  --warehouse 115 \
  --run-id codex-20260623031813-115 \
  --matches-csv matches.csv
```

Checks:
- ✅ Narrative exists
- ✅ Reasoning populated (if Cloud SQL enabled)
- ✅ 3 samples reproduce final_score

## Next (Optional)

1. Enable Cloud SQL write-back (requires private service connection)
2. Compare 2+ warehouses (see docs)
3. Track thresholds over time (detect drift)
4. Calibrate bands with labeled validation set

---

**For full details**: See `docs/analysis_workflow.md`
