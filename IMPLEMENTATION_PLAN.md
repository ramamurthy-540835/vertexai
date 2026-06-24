# Mock Data Enhancement Implementation Plan

**Status:** Ready for Implementation  
**Directory:** `/home/appadmin/projects/gcp-vertexai/vertexai-dev`  
**Original (Stable):** `/home/appadmin/projects/gcp-vertexai/vertexai`

---

## Overview

Enhance the mock data generation to produce:
1. **Random category counts** - exact/fuzzy/unmatched vary each run
2. **Fuzzy records covering full 70-99.99% range** - naturally, not fixed bands
3. **Pre-classify exact matches only** - pipeline identifies fuzzy vs unmatched

---

## Files to Modify (3 files)

### 1. `mock_data/generate_mock_data.py`

**Changes Required:**

#### Change 1.1: Add random scenario weight function
- After line 99 (after SCENARIO_WEIGHTS definition)
- Add `randomize_scenario_weights(rng)` function
- Applies ±5% random variance to weights
- Normalizes back to 100%

#### Change 1.2: Rename scenarios
- Line 91-99: Rename `partial_single` → `fuzzy_single`
- Line 91-99: Rename `partial_multi` → `fuzzy_multi`
- Remove `closed_existing_only` and `late_cycle_unmatched` (simplify to 3 categories)

#### Change 1.3: Improve mutation functions
- Modify `mutate_business_name()` - Add `intensity` parameter
  - `'minor'` (1-2 changes): 90-99% similarity
  - `'medium'` (2-3 changes): 80-89% similarity
  - `'major'` (3+ changes): 70-79% similarity
- Modify `mutate_address()` - Same intensity approach
- Add more mutation types (typos, phonetic variants, word rearrangement)

#### Change 1.4: Update lead_identity_row()
- Line 457: Add after line 486
```python
"match_type": "Exact" if plan.scenario.startswith("exact") else "",
```

#### Change 1.5: Update build_pos_row()
- Line 492: Add after line 583
```python
"match_type": "Exact" if scenario.startswith("exact") else "",
```

#### Change 1.6: Update plan_to_weights()
- Line 244: Call `randomize_scenario_weights()` instead of using fixed weights
- Allow variance in exact/fuzzy/unmatched counts

---

### 2. `mock_data/load_mock_data.py`

**Changes Required:**

#### Change 2.1: Load leads - SQL columns
- Line 287: Add `match_type` to INSERT column list

#### Change 2.2: Load leads - Row tuple
- Line 248: Add `row.get('match_type'),` as last column before closing

#### Change 2.3: Load leads - Field count
- Line 302: Change field count from 18 to 19

#### Change 2.4: Load POS transactions - match_type
- Line 352: Change `None,  # match_type` → `row.get('match_type'),  # match_type`

#### Change 2.5: Load transaction - match_type
- Line 388: Change `None,  # match_type` → `row.get('match_type'),  # match_type`

---

### 3. `lead_match_runtime/job_runner.py`

**Changes Required:**

#### Change 3.1: exact_lead_exclusion_clause()
- Line 245: Add after the second NOT EXISTS clause
```python
AND COALESCE(
    (SELECT l.match_type FROM "{schema}"."lead" l WHERE l.lead_id = {lead_expr}),
    ''
) != 'Exact'
```

#### Change 3.2: exact_pos_exclusion_clause()
- Line 269: Add after the second NOT EXISTS clause
```python
AND COALESCE(t.match_type, '') != 'Exact'
```

---

## Data Flow After Implementation

```
GENERATION:
  generate_mock_data.py
  ├─ Random counts: exact (10-15%), fuzzy (15-25%), unmatched (remaining)
  ├─ Exact records: identical business_name + address, match_type='Exact'
  ├─ Fuzzy records: similar with random mutation intensity, match_type=''
  └─ Unmatched records: different, match_type=''
         ↓
OUTPUT:
  ├─ leads_corrected.xlsx with match_type column
  └─ pos_corrected.xlsx with match_type column

LOADING:
  load_mock_data.py
         ↓
  Cloud SQL
  ├─ leadmgmt.lead with match_type
  └─ leadmgmt.transaction with match_type

PIPELINE:
  1. exact-match job → creates match_decision_detail
  2. lead-embeddings job
     ├─ Check: l.match_type != 'Exact'?
     └─ YES → Skip embedding
  3. pos-embeddings job
     ├─ Check: t.match_type != 'Exact'?
     └─ YES → Skip embedding
  4. fuzzy-match job → calculates similarity scores
     ├─ 70-99.99%: match_type='Fuzzy'
     └─ <70%: match_type='Unmatched'

FINAL OUTPUT:
  match_decision_detail with:
  ├─ match_type='Exact' (pre-classified)
  ├─ match_type='Fuzzy' (70-99.99%, identified by pipeline)
  └─ match_type='Unmatched' (<70%, identified by pipeline)
```

---

## Expected Results

✅ Mock data with random distribution each run  
✅ Exact matches pre-classified, skipped from embedding  
✅ Fuzzy matches naturally covering 70-99.99% range  
✅ Unmatched records below 70%  
✅ Pipeline correctly identifies and classifies records  
✅ Reports show accurate match counts and fuzzy score distribution  

---

## Testing Checklist

- [ ] Generate mock data and verify match_type column exists
- [ ] Load mock data into Cloud SQL and verify match_type is populated
- [ ] Run exact-match job and verify it creates match_decision_detail
- [ ] Run lead/pos embedding jobs and verify exclusion clauses work
- [ ] Run fuzzy-match job and verify scores span 70-100%
- [ ] Generate reports and verify match_type distribution
- [ ] Run multiple times and verify counts vary randomly each run

---

## Notes

- Original stable code remains untouched in `/home/appadmin/projects/gcp-vertexai/vertexai`
- All development happens in this directory: `/home/appadmin/projects/gcp-vertexai/vertexai-dev`
- Exact matches are pre-classified; pipeline identifies fuzzy vs unmatched
- Random counts use `random.gauss()` with ±5% variance around base weights
