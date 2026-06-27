#!/usr/bin/env python3
"""Write merged match results (exact_fuzzy_combined_matches.csv) to Cloud SQL.

Reads all config from lead_manager.json (writeback section).
Follows the Costco temp-table upsert pattern:
  1. transaction table — dedup by pos_id, highest match_score wins
  2. lead table — dedup by lead_id, Match > Potential > Open
  3. CE lead status — SET lead_status='Closed - Existing'
  4. mark processed — SET is_processed=true

Usage:
    python3 lead_match_codebase/src/costco/leadmgmt/components/fuzzy_vertexai_matching/scripts/reporting/writeback_merged_to_cloudsql.py \
        --csv-path /tmp/exact_fuzzy_combined_matches.csv \
        --warehouse 115
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import pg8000.dbapi

from lead_match_runtime.business_rules import load_business_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def connect(rules):
    cloud_sql = rules["environment"]["cloud_sql"]
    db_user = os.environ.get("DB_USER", "")
    db_password = os.environ.get("DB_PASSWORD", "")
    if not db_user or not db_password:
        raise RuntimeError("DB_USER and DB_PASSWORD env vars are required")
    db_host = os.environ.get("DB_HOST", cloud_sql.get("host", ""))
    if not db_host:
        raise RuntimeError("DB_HOST env var or cloud_sql.host in JSON is required")
    return pg8000.dbapi.connect(
        host=db_host,
        port=int(cloud_sql.get("port", 5432)),
        database=cloud_sql.get("database", "postgres"),
        user=db_user,
        password=db_password,
    )


def load_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    logger.info("Loaded %d rows from %s", len(df), csv_path)
    return df


def update_transactions(cursor, conn, df, schema, wb_cfg):
    match_filter = wb_cfg["transaction_update"]["filter"]
    pos_df = df.query(match_filter).copy()
    if pos_df.empty:
        logger.info("No Match/Potential rows for transaction update")
        return 0

    pos_df["match_score"] = pd.to_numeric(pos_df.get("similarity_score", pd.Series(dtype=float)), errors="coerce").fillna(0)
    pos_df = pos_df.sort_values("match_score", ascending=False).drop_duplicates(subset=["pos_id"], keep="first")
    pos_df["updated_by"] = wb_cfg.get("updated_by", "GCP")
    pos_df["primary_transaction"] = pos_df["primary_transaction"].apply(
        lambda v: str(v).strip().lower() in ("true", "1")
    )

    cursor.execute(f'DROP TABLE IF EXISTS "{schema}"."temp_transaction"')
    cursor.execute(f"""
        CREATE TABLE "{schema}"."temp_transaction" (
            pos_id varchar(150) PRIMARY KEY,
            lead_id varchar(150),
            match_type varchar(100),
            match_score double precision,
            primary_transaction boolean,
            updated_date timestamp DEFAULT current_timestamp,
            updated_by VARCHAR(100),
            matching_comments TEXT
        )
    """)

    cols = ["pos_id", "lead_id", "match_type", "match_score", "primary_transaction", "updated_by", "matching_comments"]
    chunk_size = 5000
    inserted = 0
    for i in range(0, len(pos_df), chunk_size):
        chunk = pos_df.iloc[i:i + chunk_size]
        for _, row in chunk.iterrows():
            cursor.execute(
                f'INSERT INTO "{schema}"."temp_transaction" (pos_id, lead_id, match_type, match_score, primary_transaction, updated_by, matching_comments) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                [row.get("pos_id", ""), row.get("lead_id", ""), row.get("match_type", ""),
                 float(row.get("match_score", 0)), bool(row.get("primary_transaction", False)),
                 row.get("updated_by", "GCP"), row.get("matching_comments", "")]
            )
            inserted += 1
        logger.info("Inserted %d/%d rows into temp_transaction", inserted, len(pos_df))

    cursor.execute(f"""
        INSERT INTO "{schema}"."transaction" (
            pos_id, lead_id, match_type, match_score, primary_transaction,
            updated_by, updated_date, matching_comments
        )
        SELECT pos_id, lead_id, match_type, match_score, primary_transaction,
               updated_by, updated_date, matching_comments
        FROM "{schema}"."temp_transaction"
        ON CONFLICT (pos_id) DO UPDATE SET
            lead_id = EXCLUDED.lead_id,
            updated_date = EXCLUDED.updated_date,
            updated_by = EXCLUDED.updated_by,
            match_type = EXCLUDED.match_type,
            match_score = EXCLUDED.match_score,
            primary_transaction = EXCLUDED.primary_transaction,
            matching_comments = EXCLUDED.matching_comments
        WHERE "{schema}"."transaction".match_score IS NULL
           OR "{schema}"."transaction".match_score < EXCLUDED.match_score
    """)
    updated = cursor.rowcount
    cursor.execute(f'DROP TABLE IF EXISTS "{schema}"."temp_transaction"')
    conn.commit()
    logger.info("Transaction upsert: %d rows updated", updated)
    return updated


def update_leads(cursor, conn, df, schema, wb_cfg):
    match_filter = wb_cfg["lead_update"]["filter"]
    lead_df = df.query(match_filter).copy()
    if lead_df.empty:
        logger.info("No Match/Potential rows for lead update")
        return 0

    lead_df["match_score"] = pd.to_numeric(lead_df.get("similarity_score", pd.Series(dtype=float)), errors="coerce").fillna(0)
    lead_df = lead_df.sort_values("match_score", ascending=False).drop_duplicates(subset=["lead_id"], keep="first")
    lead_df["updated_by"] = wb_cfg.get("updated_by", "GCP")

    cursor.execute(f'DROP TABLE IF EXISTS "{schema}"."temp_lead"')
    cursor.execute(f"""
        CREATE TABLE "{schema}"."temp_lead" (
            lead_id varchar(150) PRIMARY KEY,
            account_number bigint,
            match_result varchar(100),
            updated_date timestamp DEFAULT current_timestamp,
            updated_by VARCHAR(100)
        )
    """)

    chunk_size = 5000
    inserted = 0
    for i in range(0, len(lead_df), chunk_size):
        chunk = lead_df.iloc[i:i + chunk_size]
        for _, row in chunk.iterrows():
            acct = row.get("account_number", "")
            try:
                acct_int = int(float(acct)) if acct else None
            except (ValueError, TypeError):
                acct_int = None
            cursor.execute(
                f'INSERT INTO "{schema}"."temp_lead" (lead_id, account_number, match_result, updated_by) VALUES (%s, %s, %s, %s)',
                [row.get("lead_id", ""), acct_int, row.get("match_result", ""), row.get("updated_by", "GCP")]
            )
            inserted += 1
        logger.info("Inserted %d/%d rows into temp_lead", inserted, len(lead_df))

    cursor.execute(f"""
        INSERT INTO "{schema}"."lead" (
            lead_id, account_number, match_result, updated_date, updated_by
        )
        SELECT lead_id, account_number, match_result, updated_date, updated_by
        FROM "{schema}"."temp_lead"
        ON CONFLICT (lead_id) DO UPDATE SET
            account_number = EXCLUDED.account_number,
            match_result = EXCLUDED.match_result,
            updated_date = EXCLUDED.updated_date,
            updated_by = EXCLUDED.updated_by
        WHERE (
            CASE "{schema}"."lead".match_result
                WHEN 'Potential' THEN 1
                WHEN 'Match' THEN 2
                ELSE 0
            END
            <
            CASE EXCLUDED.match_result
                WHEN 'Potential' THEN 1
                WHEN 'Match' THEN 2
                ELSE 0
            END
        ) OR "{schema}"."lead".lead_status = 'Open'
    """)
    updated = cursor.rowcount
    cursor.execute(f'DROP TABLE IF EXISTS "{schema}"."temp_lead"')
    conn.commit()
    logger.info("Lead upsert: %d rows updated", updated)
    return updated


def update_ce_leads(cursor, conn, df, schema, wb_cfg):
    ce_df = df[df.get("closed_existing_flag", pd.Series(dtype=str)).str.strip().str.lower().isin(["true", "1", "yes"])]
    ce_lead_ids = sorted(ce_df["lead_id"].dropna().unique().tolist())
    if not ce_lead_ids:
        logger.info("No Closed-Existing leads to update")
        return 0

    batch_size = int(wb_cfg.get("closed_existing_update", {}).get("batch_size", 5000))
    total = 0
    for i in range(0, len(ce_lead_ids), batch_size):
        batch = ce_lead_ids[i:i + batch_size]
        cursor.execute(
            f"""UPDATE "{schema}"."lead"
                SET lead_status = 'Closed - Existing',
                    updated_by = 'GCP',
                    updated_date = current_timestamp
                WHERE lead_id = ANY(%s)""",
            [batch],
        )
        total += cursor.rowcount
    conn.commit()
    logger.info("CE lead status: %d leads set to Closed - Existing", total)
    return total


def mark_processed(cursor, conn, df, schema, wb_cfg):
    match_df = df[df.get("match_result", pd.Series(dtype=str)).str.strip().isin(["Match", "Potential"])]
    pos_ids = sorted(match_df["pos_id"].dropna().unique().tolist())
    pos_ids = [p for p in pos_ids if p.strip()]
    if not pos_ids:
        logger.info("No POS IDs to mark processed")
        return 0

    batch_size = int(wb_cfg.get("mark_processed", {}).get("batch_size", 5000))
    total = 0
    for i in range(0, len(pos_ids), batch_size):
        batch = pos_ids[i:i + batch_size]
        cursor.execute(
            f"""UPDATE "{schema}"."transaction"
                SET is_processed = true,
                    process_datetime = current_timestamp
                WHERE pos_id = ANY(%s)""",
            [batch],
        )
        total += cursor.rowcount
    conn.commit()
    logger.info("Mark processed: %d transactions set is_processed=true", total)
    return total


def main():
    parser = argparse.ArgumentParser(description="Write merged match results to Cloud SQL")
    parser.add_argument("--csv-path", required=True, help="Path to exact_fuzzy_combined_matches.csv")
    parser.add_argument("--warehouse", required=True, help="Warehouse number")
    parser.add_argument("--dry-run", action="store_true", help="Log what would happen without writing")
    args = parser.parse_args()

    rules = load_business_rules()
    schema = rules["environment"]["cloud_sql"]["schema"]
    wb_cfg = rules.get("writeback", {})

    if not wb_cfg:
        logger.error("No 'writeback' section in business rules JSON")
        sys.exit(1)

    df = load_csv(args.csv_path)

    if args.dry_run:
        match_df = df[df.get("match_result", pd.Series(dtype=str)).str.strip().isin(["Match", "Potential"])]
        ce_df = df[df.get("closed_existing_flag", pd.Series(dtype=str)).str.strip().str.lower().isin(["true", "1"])]
        logger.info("DRY RUN — would update:")
        logger.info("  Transaction: %d rows (dedup by pos_id)", match_df["pos_id"].nunique())
        logger.info("  Lead: %d rows (dedup by lead_id)", match_df["lead_id"].nunique())
        logger.info("  CE leads: %d", ce_df["lead_id"].nunique())
        logger.info("  Mark processed: %d POS", match_df["pos_id"].nunique())
        return

    conn = connect(rules)
    cursor = conn.cursor()
    logger.info("Connected to Cloud SQL (schema=%s, warehouse=%s)", schema, args.warehouse)

    try:
        tx_count = update_transactions(cursor, conn, df, schema, wb_cfg)
        lead_count = update_leads(cursor, conn, df, schema, wb_cfg)
        ce_count = update_ce_leads(cursor, conn, df, schema, wb_cfg)
        processed_count = mark_processed(cursor, conn, df, schema, wb_cfg)

        logger.info("=" * 60)
        logger.info("WRITEBACK COMPLETE — warehouse %s", args.warehouse)
        logger.info("  Transaction upsert: %d", tx_count)
        logger.info("  Lead upsert: %d", lead_count)
        logger.info("  CE lead status: %d", ce_count)
        logger.info("  Mark processed: %d", processed_count)
        logger.info("=" * 60)
    except Exception as e:
        logger.error("Writeback failed: %s", e)
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
