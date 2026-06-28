# WH 569 — Exact Engine vs Combined Output Audit
**Run ID:** github-28294622328-1-569  
**Date:** 2026-06-28  
**Auditor:** Automated comparison (claude-sonnet-4-6)

---

## Files Compared

| File | Rows | Scope |
|---|---|---|
| `reports/exact_matching/exact_matching.csv` | 677 rows (WH 569 only) | Exact engine output from primary_matching.py |
| `reports/wh569_github-28294622328-1-569/matches.csv` | 563 rows | Fuzzy Vertex AI pipeline (Match/Potential only) |
| `reports/wh569_github-28294622328-1-569/exact_fuzzy_combined_matches.csv` | 1,115 rows | Final merged + compliance-fixed output |

---

## Summary Scorecard

| Check | Result | Detail |
|---|---|---|
| Exact leads in combined (Match/Potential) | **FAIL** | 4 of 79 leads missing from Match/Potential |
| Leads completely absent from combined | **FAIL** | 3 leads have zero rows in combined output |
| Score preservation (exact > 100) | **PASS** | 210/211 correct; 1 evicted by POS rule |
| Score preservation (exact 80-99) | **WARN** | 34 rows: fuzzy 99.999 replaced exact 80-95 |
| Match result classification preserved | **PASS** | All 34 score-override rows kept same match_result |
| POS one-to-one in combined | **PASS** | 0 POS claimed by >1 lead |
| NMI / primary_transaction integrity | **PASS** | 0 leads missing NMI, 0 with duplicate NMI |
| lifecycle_state completeness | **PASS** | 0 missing (1,115/1,115) |
| is_processed completeness | **PASS** | 0 missing (1,115/1,115) |
| matching_comments coverage | **PASS** | 0 missing across all 3 output files |

---

## FAULT 1 (Critical): 3 Leads Completely Absent from Combined Output

**Root cause:** The `enforce_pos_one_to_one` post-processing rule evicts a lead's row when another lead claims the same POS with a higher or equal score (tie-break: lowest lead_id wins). When all of a lead's POS rows are evicted and it has no CE rows, the lead disappears entirely from the combined output.

These 3 leads have **zero rows** in `exact_fuzzy_combined_matches.csv`:

| Lead ID | Exact Result | Exact Score | POS Lost To | Winner Score | Reason |
|---|---|---|---|---|---|
| LEAD00627674 | Potential | 90.0 | LEAD00386916 | 90.0 | Tie-break (lower lead ID wins) |
| LEAD00646737 | **Match** | **145.0** | LEAD00627889 | 145.0 | Tie-break (lower lead ID wins) |
| LEAD00646977 | Potential | 85.0 | LEAD00545955 / LEAD00628119 | 87.68 / 85.0 | Higher score / Tie-break |

**Business impact:**
- LEAD00646737 is a **Match** lead with score 145/150 (near-perfect exact match) that will not appear in any Cloud SQL writeback or business report
- All 3 leads were legitimately matched by the exact engine but lose POS competition to other leads
- These leads will not have `lifecycle_state` or `is_processed` tracked anywhere in the pipeline output

**Recommendation:** Add a "contested-and-lost" retention pass in `merge_exact_fuzzy_output.py` — when a lead's Match/Potential rows are all evicted by POS one-to-one, emit a record with `match_result=Contested`, `primary_transaction=False`, and the losing score for audit purposes. This prevents silent data loss.

---

## FAULT 2 (Low): 1 Lead Downgraded — Potential Row Evicted, CE Rows Retained

**Lead:** LEAD00646496  
**Exact result:** Potential (GPOS68781499, score 85.0) — lost to LEAD00627640 (same score 85.0, lower lead ID wins)  
**Combined status:** Present with 7 CE rows only — Potential classification lost

This lead IS present in the combined output (7 CE rows) but its Potential match row was evicted. Not a data loss issue but the lead's best match_result is understated.

---

## WARN: 34 Rows Where Fuzzy Score (99.999) Replaced Exact Score (75–95)

**Root cause:** The merge uses `highest_score_wins` with raw numeric comparison. Exact engine uses 0–150 scale; fuzzy uses 0–99.999. For exact scores below 100, fuzzy can numerically outcompete. All 34 affected rows stayed `Potential` — no match_result classification change.

**Affected leads and score distribution:**

| Exact Score Range | Row Count | Leads Affected |
|---|---|---|
| 90–99 | 2 | LEAD00646519 |
| 80–89 | 30 | LEAD00646768, LEAD00646799, LEAD00646863, LEAD00646868, LEAD00646935 |
| 75–79 | 2 | LEAD00646863 |

**Full override table (34 rows):**

| Lead ID | POS ID | Exact Score | Combined Score | Result |
|---|---|---|---|---|
| LEAD00646519 | GPOS67767075 | 95.0 | 99.999 | Potential |
| LEAD00646519 | GPOS68894966 | 95.0 | 99.999 | Potential |
| LEAD00646768 | GPOS57417120 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS62756452 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS64964068 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS64964069 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS64964070 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS67197803 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS68390327 | 85.0 | 99.999 | Potential |
| LEAD00646799 | GPOS68935274 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS66759247 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS66759248 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS66759249 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS67169283 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS67780813 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS68364953 | 85.0 | 99.999 | Potential |
| LEAD00646868 | GPOS68772485 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS59869833 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS59882043 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS59882044 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS62650233 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS62650234 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS62650235 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS64735889 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS64735891 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS64747541 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS64747542 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS68366429 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS68774029 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS68816866 | 85.0 | 99.999 | Potential |
| LEAD00646935 | GPOS68909446 | 85.0 | 99.999 | Potential |
| LEAD00646768 | GPOS64969214 | 85.0 | 99.999 | Potential |
| LEAD00646863 | GPOS60494669 | 75.0 | 99.999 | Potential |
| LEAD00646863 | GPOS60494670 | 75.0 | 99.999 | Potential |

**Note:** In all 34 cases, match_result stayed `Potential` — no misclassification. The numeric score in the Cloud SQL transaction table will reflect the fuzzy confidence (99.999) instead of the exact engine score (75–95). This is arguably correct since fuzzy incorporates more dimensions (address + name + email + phone boost), but the `matched_by` provenance field loses the `System` (exact) tag.

**Recommendation:** Add a `source_engine` column to the combined output to preserve provenance independently of score. Low priority — no classification impact.

---

## What Passed

### Score Preservation for Exact Engine Scores > 100
All 211 exact rows with score > 100 were preserved correctly in combined — the merge correctly gives exact scores ≥ 100 priority because they numerically dominate the fuzzy max of 99.999. Only 1 exception is LEAD00646737 (score 145.0) which was evicted by POS one-to-one.

### NMI Flagging
- 0 leads missing `primary_transaction=True`
- 0 leads with duplicate `primary_transaction=True`
- NMI correctly set to earliest qualifying POS (fiscal_year ASC → fiscal_period ASC → week ASC, score ≥ 70)

### POS One-to-One
- 0 POS IDs claimed by more than one lead in combined output
- Enforcement: highest score wins; tie → lexicographically lowest lead_id

### Derived Columns
- `lifecycle_state`: 100% populated (Match=322, Potential=349, Closed - Existing=444)
- `is_processed`: 100% populated

### Comment Coverage (all 3 output files)
| File | Rows | Missing matching_comments |
|---|---|---|
| matches_enriched.csv | 563 | 0 |
| matches.csv | 563 | 0 |
| exact_fuzzy_combined_matches.csv | 1,115 | 0 |

---

## Overall Lead Count Reconciliation

| Source | Unique Leads | Match/Potential |
|---|---|---|
| exact_matching.csv (WH 569) | 79 | 79 |
| fuzzy matches.csv | 21 | 21 (subset of exact) |
| combined (Match/Potential) | **75** | 75 |
| Combined shortfall | 4 | 3 absent + 1 downgraded |

The 21 fuzzy leads are all a subset of the 79 exact leads — no net-new leads were found by fuzzy for WH 569. The fuzzy pipeline added semantic confidence (99.999 scores) for these leads' POS rows but did not expand the matched lead population.

---

## Action Items

| Priority | Item | Owner |
|---|---|---|
| HIGH | Implement "contested-and-lost" retention in `merge_exact_fuzzy_output.py` so evicted leads still appear in output with `match_result=Contested` | Engineering |
| LOW | Add `source_engine` column (exact/fuzzy) to combined output for provenance tracking | Engineering |
| REVIEW | Manual business review of 3 absent leads (LEAD00627674, LEAD00646737, LEAD00646977) — particularly LEAD00646737 (Match, 145/150) | Business team |
