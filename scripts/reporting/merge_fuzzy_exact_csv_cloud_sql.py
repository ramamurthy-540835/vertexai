#!/usr/bin/env python3
"""Write merged match results (exact + fuzzy) from GCS CSV to Cloud SQL.

Adapted from the production update_source_data.py pattern:
  lead_match_codebase/src/costco/leadmgmt/components/update_source_data.py

Uses load_business_rules() as the single config source instead of
JobConfig/config.ini. Same temp-table upsert pattern, same dedup logic.

Usage:
    python3 scripts/reporting/merge_fuzzy_exact_csv_cloud_sql.py \
        --csv-path reports/lead_match/ctoteam/115/<run_id>/primary_match_output_merged.csv \
        --warehouse 115 \
        --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import pg8000.dbapi
from lead_match_runtime.business_rules import (
    load_business_rules,
    get_db_host,
    get_db_name,
    get_db_port,
    get_schema,
    get_cloudsql_connection_name,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RULES = load_business_rules()
SCHEMA = get_schema(RULES)


def _load_env():
    env_path = Path(__file__).resolve().parents[2] / ".env.local"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_connection():
    _load_env()
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "")
    if not db_password:
        raise RuntimeError("DB_PASSWORD not set")

    host = os.environ.get("DB_HOST") or get_db_host(RULES)
    port = get_db_port(RULES)
    database = get_db_name(RULES)

    return pg8000.dbapi.connect(
        host=host, port=port, database=database,
        user=db_user, password=db_password,
    )


def _is_truthy(v) -> bool:
    if pd.isna(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "t", "yes")
    return bool(v)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    logger.info(f"Loaded {len(df)} rows from {path}")
    return df


def transaction_table_update(conn, cursor, pos_df):
    if pos_df.empty:
        logger.info("No POS rows to update")
        return 0

    cursor.execute(f"""
        CREATE TEMP TABLE IF NOT EXISTS temp_transaction (
            pos_id text PRIMARY KEY,
            lead_id text,
            match_type text,
            match_score double precision,
            updated_by text,
            updated_date timestamp,
            primary_transaction boolean,
            matching_comments text
        ) ON COMMIT DROP
    """)

    inserted = 0
    for _, row in pos_df.iterrows():
        cursor.execute(
            f"""INSERT INTO temp_transaction
                (pos_id, lead_id, match_type, match_score, updated_by, updated_date, primary_transaction, matching_comments)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pos_id) DO NOTHING""",
            (
                row["pos_id"], row["lead_id"], row["match_type"],
                float(row["match_score"]) if row["match_score"] else None,
                row["updated_by"],
                datetime.now(),
                str(row.get("primary_transaction", "")).lower() in ("true", "1"),
                row.get("matching_comments", ""),
            ),
        )
        inserted += 1

    cursor.execute(f"""
        UPDATE "{SCHEMA}"."transaction" t
        SET lead_id = tmp.lead_id,
            match_type = tmp.match_type,
            match_score = tmp.match_score,
            updated_by = tmp.updated_by,
            updated_date = tmp.updated_date,
            primary_transaction = tmp.primary_transaction,
            matching_comments = tmp.matching_comments,
            is_processed = true,
            process_datetime = CURRENT_TIMESTAMP
        FROM temp_transaction tmp
        WHERE t.pos_id = tmp.pos_id
    """)
    updated = cursor.rowcount
    conn.commit()
    logger.info(f"transaction table: {updated} rows updated from {inserted} temp rows")
    return updated


def lead_table_update(conn, cursor, leads_df):
    if leads_df.empty:
        logger.info("No lead rows to update")
        return 0

    cursor.execute(f"""
        CREATE TEMP TABLE IF NOT EXISTS temp_lead (
            lead_id text PRIMARY KEY,
            account_number text,
            match_result text,
            updated_date timestamp,
            updated_by text
        ) ON COMMIT DROP
    """)

    inserted = 0
    for _, row in leads_df.iterrows():
        cursor.execute(
            f"""INSERT INTO temp_lead
                (lead_id, account_number, match_result, updated_date, updated_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (lead_id) DO NOTHING""",
            (
                row["lead_id"],
                row.get("account_number", "") or "",
                row["match_result"],
                datetime.now(),
                row["updated_by"],
            ),
        )
        inserted += 1

    cursor.execute(f"""
        UPDATE "{SCHEMA}"."lead" l
        SET match_result = tmp.match_result,
            updated_date = tmp.updated_date,
            updated_by = tmp.updated_by
        FROM temp_lead tmp
        WHERE l.lead_id = tmp.lead_id
    """)
    updated = cursor.rowcount
    conn.commit()
    logger.info(f"lead table: {updated} rows updated from {inserted} temp rows")
    return updated


def lead_status_closed_existing_update(conn, cursor, ce_lead_ids, batch_size=5000):
    if not ce_lead_ids:
        logger.info("No CE leads to update")
        return 0

    total = 0
    now = datetime.now()
    for i in range(0, len(ce_lead_ids), batch_size):
        batch = ce_lead_ids[i:i + batch_size]
        placeholders = ",".join(["%s"] * len(batch))
        cursor.execute(
            f"""UPDATE "{SCHEMA}"."lead"
                SET lead_status = 'Closed - Existing',
                    updated_date = %s,
                    updated_by = 'GCP'
                WHERE lead_id IN ({placeholders})""",
            [now] + batch,
        )
        total += cursor.rowcount
    conn.commit()
    logger.info(f"CE lead_status update: {total} rows across {len(ce_lead_ids)} leads")
    return total


def mark_transactions_processed(conn, cursor, pos_ids, batch_size=5000):
    if not pos_ids:
        return 0

    total = 0
    now = datetime.now()
    for i in range(0, len(pos_ids), batch_size):
        batch = pos_ids[i:i + batch_size]
        placeholders = ",".join(["%s"] * len(batch))
        cursor.execute(
            f"""UPDATE "{SCHEMA}"."transaction"
                SET is_processed = true,
                    process_datetime = %s
                WHERE pos_id IN ({placeholders})
                  AND is_processed = false""",
            [now] + batch,
        )
        total += cursor.rowcount
    conn.commit()
    logger.info(f"Marked is_processed=true: {total} transaction rows")
    return total


def main():
    parser = argparse.ArgumentParser(description="Write merged CSV to Cloud SQL")
    parser.add_argument("--csv-path", required=True, help="Path to merged CSV")
    parser.add_argument("--warehouse", type=str, help="Warehouse number (for logging)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen without writing")
    args = parser.parse_args()

    final_df = load_csv(args.csv_path)
    final_df.rename(columns={"similarity_score": "match_score"}, inplace=True)
    final_df["match_score"] = pd.to_numeric(final_df["match_score"], errors="coerce")
    final_df["updated_by"] = "GCP"

    # CE leads
    if "closed_existing_flag" in final_df.columns:
        ce_mask = final_df["closed_existing_flag"].apply(_is_truthy)
        ce_lead_ids = (
            final_df.loc[ce_mask, "lead_id"]
            .dropna().astype(str).str.strip()
        )
        ce_lead_ids = ce_lead_ids[ce_lead_ids != ""].unique().tolist()
    else:
        ce_lead_ids = []
    logger.info(f"CE leads: {len(ce_lead_ids)}")

    # pos_dataframe — Match/Potential, dedup by pos_id highest score
    pos_df = final_df[final_df["match_result"].isin(["Match", "Potential"])].copy()
    pos_df = pos_df[["pos_id", "lead_id", "match_type", "match_score",
                      "updated_by", "updated_date", "primary_transaction", "matching_comments"]]
    pos_df = pos_df.sort_values("match_score", ascending=False)
    pos_df = pos_df.drop_duplicates(subset="pos_id", keep="first").reset_index(drop=True)
    logger.info(f"pos_dataframe: {len(pos_df)} rows")

    # leads_dataframe — Match/Potential, dedup by lead_id highest score
    leads_df = final_df[final_df["match_result"].isin(["Match", "Potential"])].copy()
    leads_df = leads_df.sort_values("match_score", ascending=False)
    leads_df = leads_df.drop_duplicates(subset="lead_id", keep="first").reset_index(drop=True)
    leads_df = leads_df[["lead_id", "account_number", "match_result", "updated_date", "updated_by"]]
    logger.info(f"leads_dataframe: {len(leads_df)} rows")

    # All pos_ids for marking processed
    all_pos_ids = final_df["pos_id"].dropna().astype(str).str.strip()
    all_pos_ids = all_pos_ids[all_pos_ids != ""].unique().tolist()

    if args.dry_run:
        logger.info("=== DRY RUN — no SQL writes ===")
        logger.info(f"  transaction UPDATE: {len(pos_df)} rows")
        logger.info(f"  lead UPDATE: {len(leads_df)} rows")
        logger.info(f"  CE lead_status: {len(ce_lead_ids)} leads")
        logger.info(f"  mark_processed: {len(all_pos_ids)} pos_ids")
        return

    conn = get_connection()
    cursor = conn.cursor()
    logger.info(f"Connected to Cloud SQL ({SCHEMA})")

    try:
        transaction_table_update(conn, cursor, pos_df)
        lead_table_update(conn, cursor, leads_df)
        lead_status_closed_existing_update(conn, cursor, ce_lead_ids)
        mark_transactions_processed(conn, cursor, all_pos_ids)
        logger.info("All updates complete")
    except Exception as e:
        conn.rollback()
        logger.error(f"SQL writeback failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
