#!/usr/bin/env python3
"""
Warehouse Smoke Test Script (Generic & Safe)
- Takes warehouse number as argument (default: 115)
- Schema is taken dynamically from config (no hardcoding)
- Read-only queries only
- Gracefully handles missing optional tables
- Bypasses wheel dependency bugs by parsing INI config manually
"""

import argparse
import configparser
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import google.auth
import pandas as pd
import sqlalchemy
from google.auth import impersonated_credentials
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy.engine import URL


def get_target_credentials():
    target_service_account = os.environ.get("TARGET_SERVICE_ACCOUNT")
    if not target_service_account:
        return None

    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=target_service_account,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def get_db_metrics_safe(engine, schema_name: str, warehouse: str):
    """Collect metrics safely. Schema comes from config, not hardcoded."""

    results = {
        "warehouse": warehouse,
        "schema": schema_name,
        "timestamp": datetime.now().isoformat()
    }

    # 1. Total POS records for this warehouse
    try:
        query = f"""
            SELECT COUNT(*) as count 
            FROM {schema_name}.transaction 
            WHERE warehouse_number = '{warehouse}'
        """
        df = pd.read_sql(query, engine)
        results["total_pos_records"] = int(df.iloc[0]["count"])
    except Exception as e:
        results["total_pos_records"] = f"ERROR: {str(e)}"

    # 2. Discover actual distinct lead_status values in this warehouse
    try:
        query = f"""
            SELECT DISTINCT lead_status 
            FROM {schema_name}.lead 
            WHERE warehouse_number = '{warehouse}'
            ORDER BY lead_status;
        """
        df = pd.read_sql(query, engine)
        results["distinct_lead_statuses"] = df["lead_status"].tolist()
    except Exception as e:
        results["distinct_lead_statuses"] = f"ERROR: {str(e)}"

    # 3. Lead count grouped by status
    try:
        query = f"""
            SELECT lead_status, COUNT(*) as count
            FROM {schema_name}.lead 
            WHERE warehouse_number = '{warehouse}'
            GROUP BY lead_status
            ORDER BY count DESC;
        """
        df = pd.read_sql(query, engine)
        results["leads_by_status"] = df.to_dict(orient="records")
    except Exception as e:
        results["leads_by_status"] = f"ERROR: {str(e)}"

    # 4. Eligible leads count (using common status patterns)
    try:
        query = f"""
            SELECT COUNT(*) as count 
            FROM {schema_name}.lead 
            WHERE warehouse_number = '{warehouse}'
              AND lead_status IN (
                  'Open', 
                  'Closed Cold', 'Closed – Cold', 'Closed-Cold',
                  'Closed Match', 'Closed – Match', 'Closed-Match',
                  'Closed - Match', 'Closed - Cold'
              )
        """
        df = pd.read_sql(query, engine)
        results["eligible_leads_count"] = int(df.iloc[0]["count"])
    except Exception as e:
        results["eligible_leads_count"] = f"ERROR: {str(e)}"

    # 5. pos_embeddings count (optional table - wrapped safely)
    try:
        query = f"""
            SELECT COUNT(*) as count 
            FROM {schema_name}.pos_embeddings 
            WHERE warehouse_number = '{warehouse}'
        """
        df = pd.read_sql(query, engine)
        results["pos_embeddings_count"] = int(df.iloc[0]["count"])
    except Exception:
        results["pos_embeddings_count"] = "Table does not exist or inaccessible"

    # 6. leads_embeddings count (optional table - wrapped safely)
    try:
        query = f"""
            SELECT COUNT(*) as count 
            FROM {schema_name}.leads_embeddings 
            WHERE warehouse_number = '{warehouse}'
        """
        df = pd.read_sql(query, engine)
        results["leads_embeddings_count"] = int(df.iloc[0]["count"])
    except Exception:
        results["leads_embeddings_count"] = "Table does not exist or inaccessible"

    # 7. Table + Index + TOAST sizes (schema-safe)
    try:
        query = f"""
            SELECT 
                schemaname,
                relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid)) AS table_size,
                pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid) - pg_indexes_size(relid)) AS toast_size,
                pg_size_pretty(pg_indexes_size(relid)) AS index_size
            FROM pg_catalog.pg_statio_user_tables
            WHERE schemaname = '{schema_name}'
              AND relname IN ('transaction', 'lead', 'pos_embeddings', 'leads_embeddings')
            ORDER BY pg_total_relation_size(relid) DESC;
        """
        df = pd.read_sql(query, engine)
        results["table_sizes"] = df.to_dict(orient="records")
    except Exception as e:
        results["table_sizes"] = f"ERROR: {str(e)}"

    # 8. HNSW indexes (schema-safe)
    try:
        query = f"""
            SELECT 
                schemaname,
                tablename,
                indexname,
                indexdef
            FROM pg_indexes 
            WHERE schemaname = '{schema_name}'
              AND tablename IN ('pos_embeddings', 'leads_embeddings')
              AND indexdef ILIKE '%hnsw%';
        """
        df = pd.read_sql(query, engine)
        results["hnsw_indexes"] = df.to_dict(orient="records")
    except Exception as e:
        results["hnsw_indexes"] = f"ERROR: {str(e)}"

    # 9. Match configuration (optional)
    try:
        query = f"SELECT * FROM {schema_name}.match_configuration LIMIT 5;"
        df = pd.read_sql(query, engine)
        results["match_configuration_sample"] = df.to_dict(orient="records")
    except Exception:
        results["match_configuration_sample"] = "Table does not exist or inaccessible"

    return results


def main():
    parser = argparse.ArgumentParser(description="Warehouse Smoke Test (Safe & Config-Driven)")
    parser.add_argument("--config", help="Path to Costco-style INI configuration file")
    parser.add_argument("--warehouse", default="115", help="Warehouse number to validate (default: 115)")
    parser.add_argument("--output", default="warehouse_smoke_test_results.json", help="Output JSON file")

    args = parser.parse_args()

    print("=" * 70)
    print("WAREHOUSE SMOKE TEST (READ-ONLY, CONFIG DRIVEN)")
    print("=" * 70)
    print(f"Config File : {args.config}")
    print(f"Warehouse   : {args.warehouse}")
    print(f"Timestamp   : {datetime.now().isoformat()}")
    print("=" * 70)

    if not args.warehouse.isdigit():
        print(f"[ERROR] Warehouse must be a single numeric value, got: {args.warehouse}")
        sys.exit(1)

    # Parse INI manually when supplied; otherwise use direct ctoteam env vars.
    try:
        if args.config:
            config = configparser.ConfigParser()
            config.read(args.config)

            instance_connection_name = config.get("DATABASE", "db_connection_name")
            db_name = config.get("DATABASE", "postgres_db_name")
            schema_name = config.get("DATABASE", "db_schema")
            db_user = config.get("DATABASE", "postgres_db_user")
            sql_ip_type = config.get("DATABASE", "cloud_sql_ip_type", fallback="PRIVATE")

            ip_type = IPTypes.PRIVATE if sql_ip_type == "PRIVATE" else IPTypes.PUBLIC
            target_credentials = get_target_credentials()

            print(f"\n[INFO] Loaded DB user from INI: {db_user}")
            print(f"[INFO] Target schema: {schema_name}")
            if target_credentials:
                print(f"[INFO] Using impersonated credentials for: {os.environ['TARGET_SERVICE_ACCOUNT']}")

            def get_conn():
                connector = Connector(credentials=target_credentials)
                conn = connector.connect(
                    instance_connection_name,
                    "pg8000",
                    user=db_user,
                    db=db_name,
                    enable_iam_auth=True,
                    ip_type=ip_type
                )
                return conn

            engine = sqlalchemy.create_engine(
                "postgresql+pg8000://",
                creator=get_conn,
                pool_pre_ping=True
            )
        else:
            required_env = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
            missing = [name for name in required_env if not os.environ.get(name)]
            if missing:
                raise RuntimeError(
                    "Missing DB env vars for direct ctoteam connection: "
                    + ", ".join(missing)
                )

            schema_name = os.environ.get("DB_SCHEMA", "leadmgmt")
            url = URL.create(
                "postgresql+pg8000",
                username=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                host=os.environ["DB_HOST"],
                port=int(os.environ.get("DB_PORT", "5432")),
                database=os.environ["DB_NAME"],
            )
            engine = sqlalchemy.create_engine(
                url,
                connect_args={"timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))},
                pool_pre_ping=True,
            )
            print(f"\n[INFO] Using direct DB host: {os.environ['DB_HOST']}")
            print(f"[INFO] Target schema: {schema_name}")

        print("[OK] SQLAlchemy engine initialized successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to parse config or initialize manual engine: {e}")
        sys.exit(1)

    # Collect metrics
    print(f"\n[INFO] Collecting metrics for Warehouse {args.warehouse}...")
    metrics = get_db_metrics_safe(engine, schema_name, args.warehouse)
    print("[OK] Metrics collection completed.")

    # Print key summary
    print("\n--- KEY RESULTS ---")
    print(f"Total POS Records     : {metrics.get('total_pos_records')}")
    print(f"Eligible Leads        : {metrics.get('eligible_leads_count')}")
    print(f"pos_embeddings        : {metrics.get('pos_embeddings_count')}")
    print(f"leads_embeddings      : {metrics.get('leads_embeddings_count')}")
    print("-------------------")

    # Save full results
    final_results = {
        "run_info": {
            "timestamp": datetime.now().isoformat(),
            "warehouse": args.warehouse,
            "schema": schema_name,
            "config_file": args.config
        },
        "metrics": metrics
    }

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(final_results, f, indent=2, default=str)

    print(f"\n[INFO] Full results written to: {output_path.absolute()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
