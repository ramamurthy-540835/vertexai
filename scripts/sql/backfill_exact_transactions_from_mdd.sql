-- Remediation only: sync transaction from authoritative exact MDD rows.
-- Usage:
--   psql ... -v warehouse=115 -f scripts/backfill_exact_transactions_from_mdd.sql
--
-- Review the dry_count result before running the UPDATE in production.

\set ON_ERROR_STOP on

SELECT COUNT(*) AS dry_count
FROM leadmgmt.transaction t
JOIN leadmgmt.match_decision_detail m ON m.pos_id = t.pos_id
WHERE m.match_type = 'Exact'
  AND m.final_score = 100
  AND m.warehouse_number = :warehouse
  AND (t.match_type IS NULL OR t.match_type <> 'Exact');

UPDATE leadmgmt.transaction t
SET
    match_type = m.match_type,
    match_score = m.final_score,
    lead_id = m.lead_id,
    is_processed = true,
    process_datetime = CURRENT_TIMESTAMP,
    updated_by = 'exact_backfill',
    updated_date = CURRENT_TIMESTAMP,
    matching_comments = CONCAT('match_run_id=', m.match_run_id, '; backfilled_exact')
FROM leadmgmt.match_decision_detail m
WHERE m.pos_id = t.pos_id
  AND m.match_type = 'Exact'
  AND m.final_score = 100
  AND m.warehouse_number = :warehouse
  AND (t.match_type IS NULL OR t.match_type <> 'Exact');
