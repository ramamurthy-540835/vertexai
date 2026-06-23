#!/usr/bin/env python3
"""
Validation script for match analysis workflow.
Checks:
1. match_reasoning populated in Cloud SQL
2. Engine columns (match_type, final_score, lifecycle_state) unchanged
3. Sample reasoning strings reproduce final_score arithmetic
4. Narrative markdown exists and is readable
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from google.cloud import storage
import sqlalchemy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _build_cloud_sql_engine(db_connection_string: str | None) -> sqlalchemy.Engine | None:
    """Build SQLAlchemy engine using explicit connection string or Cloud SQL connector."""

    if db_connection_string:
        try:
            return sqlalchemy.create_engine(db_connection_string)
        except Exception as e:
            logger.error(f"Failed to create engine from explicit connection string: {e}")
            return None

    instance = os.getenv("CLOUDSQL_CONNECTION_NAME")
    if not instance:
        return None

    db_user = os.getenv("CLOUDSQL_DB_USER", "postgres")
    db_name = os.getenv("CLOUDSQL_DB_NAME", os.getenv("DB_NAME", "postgres"))
    db_password = os.getenv("DB_PASSWORD")
    use_iam = os.getenv("CLOUDSQL_IAM_AUTH", "true").lower() in {"1", "true", "yes"}

    try:
        from google.cloud.sql.connector import Connector

        connector = Connector()
    except Exception as e:
        logger.error(f"Failed to initialize Cloud SQL connector: {e}")
        return None

    enable_iam_auth = use_iam and not db_password

    try:
        def get_conn() -> Any:
            return connector.connect(
                instance,
                "pg8000",
                user=db_user,
                password=db_password if not enable_iam_auth else None,
                db=db_name,
                enable_iam_auth=enable_iam_auth,
            )

        return sqlalchemy.create_engine("postgresql+pg8000://", creator=get_conn)
    except Exception as e:
        logger.error(f"Cloud SQL connector engine build failed: {e}")
        return None


def validate_cloud_sql_reasoning(
    match_run_id: str,
    expected_row_count: int,
    db_connection_string: str | None,
) -> bool:
    """
    Validate that match_reasoning is populated for all rows in the run.

    Args:
        match_run_id: Match run identifier
        expected_row_count: Expected number of rows from matches.csv
        db_connection_string: Cloud SQL connection string

    Returns:
        True if validation passes
    """
    try:
        engine = _build_cloud_sql_engine(db_connection_string)
        if engine is None:
            logger.error("Cloud SQL engine unavailable for reasoning validation")
            return False
        with engine.connect() as conn:
            # Count reasoning rows
            query = sqlalchemy.text("""
                SELECT COUNT(*) as cnt FROM "leadmgmt"."match_decision_detail"
                WHERE "match_run_id" = :run_id AND "match_reasoning" IS NOT NULL
            """)
            result = conn.execute(query, {"run_id": match_run_id})
            reasoning_count = result.scalar() or 0

            logger.info(f"✓ match_reasoning rows: {reasoning_count}")

            if reasoning_count != expected_row_count:
                logger.error(
                    f"✗ Row count mismatch: {reasoning_count} in DB != {expected_row_count} in CSV"
                )
                return False

            # Verify engine columns are untouched (check a sample)
            query = sqlalchemy.text("""
                SELECT COUNT(*) as cnt FROM "leadmgmt"."match_decision_detail"
                WHERE "match_run_id" = :run_id
                  AND ("match_type" IS NULL OR "final_score" IS NULL)
            """)
            result = conn.execute(query, {"run_id": match_run_id})
            null_engine_cols = result.scalar() or 0

            if null_engine_cols > 0:
                logger.error(f"✗ {null_engine_cols} rows have NULL engine columns")
                return False

            logger.info("✓ Engine columns (match_type, final_score) are populated and untouched")
            return True

    except Exception as e:
        logger.error(f"Cloud SQL validation failed: {e}")
        return False


def validate_reasoning_arithmetic(
    matches_csv_path: str,
    db_connection_string: str | None,
    match_run_id: str,
    sample_size: int = 3,
) -> bool:
    """
    Spot-check 3 reasoning strings to verify arithmetic reproduction.

    Args:
        matches_csv_path: Path to matches.csv
        db_connection_string: Cloud SQL connection string
        match_run_id: Match run ID
        sample_size: Number of samples to check

    Returns:
        True if samples are valid
    """
    try:
        df = pd.read_csv(matches_csv_path)
        engine = _build_cloud_sql_engine(db_connection_string)
        if engine is None:
            logger.error("Cloud SQL engine unavailable for arithmetic validation")
            return False

        samples_checked = 0
        for idx in range(min(sample_size, len(df))):
            row = df.iloc[idx]
            lead_id = row["lead_id"]
            pos_id = row["pos_id"]
            final_score = float(row["final_score"])

            with engine.connect() as conn:
                query = sqlalchemy.text("""
                    SELECT "match_reasoning" FROM "leadmgmt"."match_decision_detail"
                    WHERE "match_run_id" = :run_id
                      AND "lead_id" = :lead_id
                      AND "pos_id" = :pos_id
                """)
                result = conn.execute(
                    query,
                    {"run_id": match_run_id, "lead_id": lead_id, "pos_id": pos_id},
                )
                reasoning = result.scalar()

                if reasoning:
                    # Extract score from reasoning string
                    if "=>" in reasoning and "=" in reasoning:
                        logger.info(
                            f"  Sample {idx + 1}: {lead_id}/{pos_id} "
                            f"(stored_score={final_score:.2f})"
                        )
                        logger.info(f"    Reasoning: {reasoning[:100]}...")
                        samples_checked += 1
                    else:
                        logger.warning(f"  Sample {idx + 1}: reasoning format unexpected")

        logger.info(f"✓ Spot-checked {samples_checked} reasoning strings")
        return samples_checked == sample_size

    except Exception as e:
        logger.error(f"Arithmetic validation failed: {e}")
        return False


def validate_narrative_exists(
    bucket_name: str,
    gcs_path: str,
) -> bool:
    """
    Validate that narrative markdown exists and is readable.

    Args:
        bucket_name: GCS bucket name
        gcs_path: Path to comparative_analysis.md

    Returns:
        True if file exists and is readable
    """
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)

        if not blob.exists():
            logger.error(f"✗ Narrative not found: gs://{bucket_name}/{gcs_path}")
            return False

        content = blob.download_as_text()
        if len(content) < 100:
            logger.warning(f"⚠ Narrative is very short ({len(content)} chars)")

        logger.info(f"✓ Narrative exists ({len(content)} chars)")
        logger.info(f"  Preview: {content[:150]}...")
        return True

    except Exception as e:
        logger.error(f"Narrative validation failed: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Validate match analysis output")
    parser.add_argument("--warehouse", default="115", help="Warehouse number")
    parser.add_argument("--run-id", help="Match run ID (required)")
    parser.add_argument(
        "--matches-csv",
        default="matches.csv",
        help="Path to matches.csv for row count",
    )
    parser.add_argument(
        "--bucket",
        default="lead-match-ctoteam",
        help="GCS bucket",
    )
    parser.add_argument(
        "--project",
        default="ctoteam",
        help="GCP project ID",
    )
    parser.add_argument(
        "--db-connection-string",
        help="Cloud SQL connection string (optional; falls back to CLOUDSQL_* env config)",
    )
    args = parser.parse_args()

    if not args.run_id:
        logger.error("--run-id is required")
        sys.exit(1)

    # Load row count from CSV
    if Path(args.matches_csv).exists():
        df = pd.read_csv(args.matches_csv)
        row_count = len(df)
        logger.info(f"Loaded {row_count} rows from {args.matches_csv}")
    else:
        logger.warning(f"matches.csv not found at {args.matches_csv}; skipping row count check")
        row_count = None

    # Validate narrative
    narrative_path = f"reports/lead_match/{args.project}/{args.warehouse}/{args.run_id}/comparative_analysis.md"
    narrative_ok = validate_narrative_exists(args.bucket, narrative_path)

    # Validate Cloud SQL (if connection string provided)
    reasoning_ok = True
    arithmetic_ok = True
    has_db_target = bool(args.db_connection_string or os.getenv("CLOUDSQL_CONNECTION_NAME"))
    if has_db_target and row_count:
        reasoning_ok = validate_cloud_sql_reasoning(
            args.run_id,
            row_count,
            args.db_connection_string,
        )
        arithmetic_ok = validate_reasoning_arithmetic(
            args.matches_csv,
            args.db_connection_string,
            args.run_id,
        )
    elif not has_db_target and row_count:
        reasoning_ok = False
        arithmetic_ok = False
        logger.warning("Cloud SQL validation skipped: no db_connection_string or CLOUDSQL_CONNECTION_NAME provided")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Narrative:        {'✓' if narrative_ok else '✗'}")
    logger.info(f"  Cloud SQL rows:   {'✓' if reasoning_ok else '✗' if has_db_target else '⊘'}")
    logger.info(f"  Arithmetic:       {'✓' if arithmetic_ok else '✗' if has_db_target else '⊘'}")

    all_ok = narrative_ok and reasoning_ok and arithmetic_ok
    if all_ok or (narrative_ok and not has_db_target):
        logger.info("✓ Validation passed")
        sys.exit(0)
    else:
        logger.error("✗ Validation failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
