# How Fuzzy Protects Exact Matching Records

## Cloud SQL tables the fuzzy pipeline touches (6 tables)

| # | Table | Operation | Touches exact data? | Protection |
|---|-------|-----------|---------------------|------------|
| 1 | `leads_embeddings` | INSERT | No — skips exact-matched leads | Exclusion query |
| 2 | `pos_embeddings` | INSERT | No — skips exact-matched POS | Exclusion query |
| 3 | `match_decision_detail` | INSERT | No — own rows only, `match_type='Fuzzy'` | Separate `match_run_id` |
| 4 | `transaction` | UPDATE | **Guarded** — refuses to overwrite exact rows | Run-aware guard |
| 5 | `lead` | UPDATE | **Guarded** — refuses to overwrite exact leads | Run-aware guard |
| 6 | `match_audit` | INSERT | No — own audit row only | New row per run |

## GCS — fuzzy never writes to exact paths

| Writer | GCS path | Overlap? |
|--------|----------|----------|
| Exact (primary_matching.py) | `gs://{bucket}/{temp_folder}/{leads_classified_file_name}` | — |
| Exact archive | `gs://{bucket}/{destination_folder_output_exact}/primary_match_output_*.csv` | — |
| Fuzzy reports | `gs://{bucket}/reports/lead_match/{project}/{warehouse}/{match_run_id}/` | **No overlap** |
| Fuzzy preflight | `gs://{bucket}/preflight/lead_match/{project}/{warehouse}/` | **No overlap** |

Fuzzy writes to `reports/` and `preflight/`. Exact writes to `{temp_folder}/` and `{destination_folder_output_exact}/`. Different paths, no collision.

---

## Protection layer by layer

### Layer 1: Embedding generation — skips exact-matched records

`lead_source_rows()` at job_runner.py:430:

```sql
SELECT l.lead_id ...
FROM lead l
JOIN account a ON a.account_id = l.account_id
WHERE NOT EXISTS (SELECT 1 FROM leads_embeddings e WHERE e.lead_id = l.lead_id)
  AND NOT EXISTS (
      SELECT 1 FROM match_decision_detail exact_m
      WHERE exact_m.lead_id = l.lead_id
        AND lower(exact_m.match_type) IN ('exact','deterministic','exact match','direct match','close match')
        AND (exact_m.final_score IS NULL OR exact_m.final_score >= 80)
  )
  AND NOT EXISTS (
      SELECT 1 FROM transaction exact_t
      WHERE exact_t.lead_id = l.lead_id
        AND lower(exact_t.match_type) IN ('exact','deterministic','exact match','direct match','close match')
        AND (exact_t.match_score IS NULL OR exact_t.match_score >= 80)
  )
```

Same logic for `pos_source_rows()` at job_runner.py:468, checking `pos_id`.

**Result:** If a lead or POS has an exact match in either `match_decision_detail` or `transaction`, no embedding is generated. The record is invisible to fuzzy from this point forward.

### Layer 2: Fuzzy candidate selection — skips exact-matched records again

`_run_fuzzy_match()` at job_runner.py — the lead fetch query (line 1000):

```sql
SELECT lead_id FROM leads_embeddings
WHERE combined_embedding IS NOT NULL
  AND NOT EXISTS (... exact_m.lead_id ... match_type IN exact types ...)
  AND NOT EXISTS (... exact_t.lead_id ... match_type IN exact types ...)
```

And the POS candidate query inside the CROSS JOIN LATERAL (line 1059):

```sql
FROM pos_embeddings s
WHERE s.combined_embedding IS NOT NULL
  AND NOT EXISTS (... exact_m.pos_id ... match_type IN exact types ...)
  AND NOT EXISTS (... exact_t.pos_id ... match_type IN exact types ...)
```

**Result:** Even if an embedding somehow exists (e.g., created before exact ran), the fuzzy query still skips it.

### Layer 3: Write-back to `transaction` — refuses to overwrite exact rows

`write_back_match_results()` at job_runner.py:830 — the UPDATE has a run-aware guard:

```sql
UPDATE transaction t
SET lead_id = tx.lead_id, match_type = tx.match_type, match_score = tx.final_score ...
FROM tx_updates tx
WHERE t.pos_id = tx.pos_id
  AND (
      -- If the current run IS exact, allow self-write
      lower(tx.match_type) IN ('exact','deterministic',...)
      OR (
          -- If NOT exact (i.e. fuzzy), block if transaction already has exact
          NOT (lower(COALESCE(t.match_type,'')) IN ('exact','deterministic',...) AND t.match_score >= 80)
          -- AND block if any OTHER run has exact in match_decision_detail
          AND NOT EXISTS (
              SELECT 1 FROM match_decision_detail exact_m
              WHERE exact_m.pos_id = t.pos_id
                AND exact_m.match_run_id <> current_run_id
                AND lower(exact_m.match_type) IN ('exact','deterministic',...)
                AND (exact_m.final_score IS NULL OR exact_m.final_score >= 80)
          )
      )
  )
```

**Result:** Fuzzy can never overwrite a POS row that has `match_type='Exact'` in `transaction`, or that has an exact record from another run in `match_decision_detail`.

### Layer 4: Write-back to `lead` — refuses to overwrite exact leads

Same function, second UPDATE at job_runner.py:903:

```sql
UPDATE lead l
SET match_result = lead_states.match_result ...
FROM lead_states
WHERE l.lead_id = lead_states.lead_id
  AND (
      -- If current run has exact for this lead, allow
      EXISTS (SELECT 1 FROM match_decision_detail cur
              WHERE cur.lead_id = l.lead_id AND cur.match_run_id = current_run_id
                AND lower(cur.match_type) IN ('exact','deterministic',...))
      OR (
          -- If fuzzy, block if another run has exact
          NOT EXISTS (... exact_m from OTHER run ...)
          AND NOT EXISTS (... exact_t in transaction ...)
      )
  )
```

**Result:** Fuzzy can never overwrite `lead.match_result` if that lead has an exact match from another run.

---

## Summary: 4 layers, defense in depth

```
Exact matching runs first
    │
    ▼  writes match_type='Exact' to:
    │  - match_decision_detail (score, lead_id, pos_id)
    │  - transaction (lead_id, match_type, match_score)
    │  - lead (match_result)
    │
    ▼
Layer 1: Embedding generation SKIPS exact leads + POS
    │    (checks both match_decision_detail AND transaction)
    │
    ▼
Layer 2: Fuzzy candidate query SKIPS exact leads + POS
    │    (checks both match_decision_detail AND transaction)
    │
    ▼
Layer 3: Transaction write-back BLOCKED for exact POS
    │    (run-aware: allows exact self-write, blocks fuzzy overwrite)
    │
    ▼
Layer 4: Lead write-back BLOCKED for exact leads
         (run-aware: allows exact self-write, blocks fuzzy overwrite)
```

No exact record is ever read, embedded, matched, or overwritten by the fuzzy pipeline.

## Verified against Cloud SQL

Run `github-27951710129-1-115` (exact + fuzzy in same workflow):

| Check | Result |
|-------|--------|
| Exact leads: 40 | Fuzzy rows for same leads: **0** |
| Exact POS: 285 | Fuzzy rows for same POS: **0** |
| Exact self-write to transaction | **ALLOWED** |
| Fuzzy overwrite of exact POS | **BLOCKED** |
| Fuzzy overwrite of exact lead | **BLOCKED** |
