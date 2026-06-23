#!/usr/bin/env python3
"""
Warehouse Smoke Test (Safe & Config-Driven)
Runs inside Cloud Run Job. Refactored from the legacy warehouse smoke wrapper
to be completely generic (warehouse as argument) and use job_runner db connection.

Warehouse smoke test must run inside Cloud Run Job, not GitHub runner, because Cloud SQL is accessed through GCP runtime connectivity.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from lead_match_runtime.business_rules import (
    exact_authoritative_score,
    exact_match_types,
    exact_score,
    fuzzy_max_score,
    fuzzy_qualify_min_score,
    get_project_id,
    get_warehouse_scope,
    load_business_rules,
)
from lead_match_runtime.job_runner import (
    assert_isolated_runtime,
    connect,
    schema_name,
    verify_hnsw_combined_indexes,
)


BUSINESS_RULES = load_business_rules()


def _placeholders(values) -> str:
    return ", ".join(["%s"] * len(values))


def check_safety_env():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    expected_project = get_project_id(BUSINESS_RULES)
    if project != expected_project:
        raise RuntimeError(
            f"Refusing to start: GOOGLE_CLOUD_PROJECT is {project!r}, "
            f"expected {expected_project!r}"
        )

    conn = os.environ.get("CLOUDSQL_CONNECTION_NAME")
    if conn and conn not in ("ctoteam:us-central1", "ctoteam:us-central1:lead-mgmt-db"):
        raise RuntimeError(f"Refusing to start: CLOUDSQL_CONNECTION_NAME is {conn!r}, expected 'ctoteam:us-central1' or 'ctoteam:us-central1:lead-mgmt-db'")

    allow_client = os.environ.get("ALLOW_CLIENT_GCP")
    if allow_client != "false":
        raise RuntimeError(f"Refusing to start: ALLOW_CLIENT_GCP is {allow_client!r}, expected 'false'")

    allow_prod = os.environ.get("ALLOW_PRODUCTION")
    if allow_prod != "false":
        raise RuntimeError(f"Refusing to start: ALLOW_PRODUCTION is {allow_prod!r}, expected 'false'")


def check_hnsw_combined_indexes(cursor, schema: str, results: dict):
    check_group = (
        results["required_checks"]
        if "hnsw_combined_indexes" in results.get("required_checks", {})
        else results["optional_checks"]
    )
    try:
        _, missing_tables = verify_hnsw_combined_indexes(cursor, schema, fail_fast=False)
        if missing_tables:
            check_group["hnsw_combined_indexes"] = "FAIL"
            results["errors"].append(
                "HNSW index missing on "
                + ", ".join(f"{table}.combined_embedding" for table in missing_tables)
                + " - refusing to run fuzzy match, would trigger full scan"
            )
            return
        check_group["hnsw_combined_indexes"] = "PASS"
        print("[INFO] combined_embedding HNSW indexes are present")
    except Exception as e:
        check_group["hnsw_combined_indexes"] = "FAIL"
        results["errors"].append(f"Failed to check HNSW indexes: {e}")


def latest_match_run_id(cursor, schema: str) -> str | None:
    cursor.execute(
        f"""
        SELECT match_run_id
        FROM "{schema}"."match_decision_detail"
        ORDER BY created_date DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    return row[0] if row else None


def check_fuzzy_score_band(cursor, schema: str, results: dict):
    try:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = 'match_decision_detail'
            """,
            (schema,),
        )
        if not cursor.fetchone():
            results["optional_checks"]["fuzzy_score_band"] = "SKIPPED"
            return

        match_run_id = os.environ.get("MATCH_RUN_ID") or latest_match_run_id(cursor, schema)
        if not match_run_id:
            results["optional_checks"]["fuzzy_score_band"] = "SKIPPED"
            return

        types = exact_match_types(BUSINESS_RULES)
        placeholders = _placeholders(types)
        fuzzy_floor = fuzzy_qualify_min_score(BUSINESS_RULES)
        fuzzy_ceiling = fuzzy_max_score(BUSINESS_RULES)
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM "{schema}"."match_decision_detail"
            WHERE match_run_id = %s
              AND lower(match_type) NOT IN ({placeholders})
              AND final_score >= %s
            """,
            (match_run_id, *types, exact_score(BUSINESS_RULES)),
        )
        bad_count = int(cursor.fetchone()[0])

        cursor.execute(
            f"""
            SELECT MIN(final_score), MAX(final_score)
            FROM "{schema}"."match_decision_detail"
            WHERE match_run_id = %s
              AND lower(match_type) NOT IN ({placeholders})
            """,
            (match_run_id, *types),
        )
        min_score, max_score = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM "{schema}"."match_decision_detail"
            WHERE match_run_id = %s
              AND final_score < %s
            """,
            (match_run_id, fuzzy_floor),
        )
        below_floor_count = int(cursor.fetchone()[0])
        results["fuzzy_score_band"] = {
            "match_run_id": match_run_id,
            "min_semantic_score": float(min_score) if min_score is not None else None,
            "max_semantic_score": float(max_score) if max_score is not None else None,
            "bad_non_exact_100_plus": bad_count,
            "below_fuzzy_floor_rows": below_floor_count,
        }

        if bad_count:
            results["optional_checks"]["fuzzy_score_band"] = "FAIL"
            results["errors"].append(
                f"Found {bad_count} non-exact rows with final_score >= {exact_score(BUSINESS_RULES):.2f} "
                f"for match_run_id={match_run_id}"
            )
            return
        if below_floor_count:
            results["optional_checks"]["fuzzy_score_band"] = "FAIL"
            results["errors"].append(
                f"Found {below_floor_count} rows with final_score below {fuzzy_floor:.2f} "
                f"for match_run_id={match_run_id}"
            )
            return
        if min_score is not None and float(min_score) < fuzzy_floor:
            results["optional_checks"]["fuzzy_score_band"] = "FAIL"
            results["errors"].append(
                f"Semantic min score {float(min_score):.2f} is below {fuzzy_floor:.2f} "
                f"for match_run_id={match_run_id}"
            )
            return
        if max_score is not None and float(max_score) > fuzzy_ceiling:
            results["optional_checks"]["fuzzy_score_band"] = "FAIL"
            results["errors"].append(
                f"Semantic max score {float(max_score):.2f} is above {fuzzy_ceiling:.2f} "
                f"for match_run_id={match_run_id}"
            )
            return

        results["optional_checks"]["fuzzy_score_band"] = "PASS"
        print(
            "[INFO] Semantic score band check passed: "
            f"match_run_id={match_run_id}; min={min_score}; max={max_score}; "
            f"bad_exact_score_plus={bad_count}; below_fuzzy_floor={below_floor_count}"
        )
    except Exception as e:
        results["optional_checks"]["fuzzy_score_band"] = "FAIL"
        results["errors"].append(f"Failed to check fuzzy score band: {e}")


def check_mdd_transaction_exact_reconciliation(cursor, schema: str, warehouse: str, results: dict):
    try:
        types = exact_match_types(BUSINESS_RULES)
        placeholders = _placeholders(types)
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM "{schema}"."match_decision_detail" m
            JOIN "{schema}"."transaction" t ON t.pos_id = m.pos_id
            WHERE m.warehouse_number = %s
              AND lower(m.match_type) IN ({placeholders})
              AND (m.final_score IS NULL OR m.final_score >= %s)
              AND COALESCE(lower(t.match_type), '') NOT IN (
                  {placeholders}
              )
            """,
            (
                int(warehouse),
                *types,
                exact_authoritative_score(BUSINESS_RULES),
                *types,
            ),
        )
        mismatch_count = int(cursor.fetchone()[0])
        results["mdd_transaction_exact_reconciliation"] = {
            "warehouse": warehouse,
            "exact_mismatch_count": mismatch_count,
        }
        if mismatch_count:
            results["required_checks"]["mdd_transaction_exact_reconciliation"] = "FAIL"
            results["errors"].append(
                f"MDD/transaction exact reconciliation failed for warehouse {warehouse}: "
                f"{mismatch_count} exact MDD pos_ids are non-exact in transaction"
            )
            return
        results["required_checks"]["mdd_transaction_exact_reconciliation"] = "PASS"
        print("[INFO] MDD exact decisions reconcile to transaction")
    except Exception as e:
        results["required_checks"]["mdd_transaction_exact_reconciliation"] = "FAIL"
        results["errors"].append(f"Failed to check MDD/transaction exact reconciliation: {e}")


def run_smoke_test(warehouse: str, fiscal_year: int = None, fiscal_period: int = None):
    # Safety Check first
    check_safety_env()
    assert_isolated_runtime()

    project = get_project_id(BUSINESS_RULES)
    conn_name = os.environ.get("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1")
    schema = schema_name()

    results = {
        "project": project,
        "cloudsql_connection_name": conn_name,
        "schema": schema,
        "warehouse": warehouse,
        "status": "FAIL",
        "required_checks": {
            "db_connection": "FAIL",
            "schema_exists": "FAIL",
            "required_tables": "FAIL",
            "lead_rows": "FAIL",
            "pos_rows": "FAIL",
            "hnsw_combined_indexes": "FAIL",
            "mdd_transaction_exact_reconciliation": "FAIL"
        },
        "optional_checks": {
            "lead_embeddings": "SKIPPED",
            "pos_embeddings": "SKIPPED",
            "match_audit": "SKIPPED",
            "fuzzy_score_band": "SKIPPED"
        },
        "errors": []
    }

    conn = None
    try:
        # DB connection works
        try:
            conn = connect()
            results["required_checks"]["db_connection"] = "PASS"
        except Exception as e:
            results["required_checks"]["db_connection"] = "FAIL"
            results["errors"].append(f"DB Connection failed: {e}")
            return results

        cursor = conn.cursor()

        # Schema exists check
        try:
            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
                (schema,)
            )
            row = cursor.fetchone()
            if row:
                results["required_checks"]["schema_exists"] = "PASS"
            else:
                results["required_checks"]["schema_exists"] = "FAIL"
                results["errors"].append(f"Schema {schema!r} does not exist")
        except Exception as e:
            results["required_checks"]["schema_exists"] = "FAIL"
            results["errors"].append(f"Failed to check schema existence: {e}")

        # If schema doesn't exist, we can't check tables
        if results["required_checks"]["schema_exists"] != "PASS":
            return results

        # Required tables exist
        required_tables_list = ["lead", "transaction"]
        missing_tables = []
        for table in required_tables_list:
            try:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (schema, table)
                )
                if not cursor.fetchone():
                    missing_tables.append(table)
            except Exception as e:
                missing_tables.append(table)
                results["errors"].append(f"Error checking table {table}: {e}")

        if not missing_tables:
            results["required_checks"]["required_tables"] = "PASS"
        else:
            results["required_checks"]["required_tables"] = "FAIL"
            results["errors"].append(f"Missing required tables: {missing_tables}")

        # Row counts for lead table
        if "lead" not in missing_tables:
            try:
                query = f'SELECT COUNT(*) FROM "{schema}"."lead" WHERE warehouse_number = %s'
                params = [int(warehouse)]
                if fiscal_year is not None:
                    query += " AND fiscal_year = %s"
                    params.append(fiscal_year)
                if fiscal_period is not None:
                    query += " AND fiscal_period = %s"
                    params.append(fiscal_period)

                cursor.execute(query, params)
                lead_count = cursor.fetchone()[0]
                results["required_checks"]["lead_rows"] = "PASS"
                print(f"[INFO] Lead rows count: {lead_count}")
            except Exception as e:
                results["required_checks"]["lead_rows"] = "FAIL"
                results["errors"].append(f"Failed to query lead rows: {e}")
        else:
            results["required_checks"]["lead_rows"] = "FAIL"

        # Row counts for POS transaction table
        if "transaction" not in missing_tables:
            try:
                query = f'SELECT COUNT(*) FROM "{schema}"."transaction" WHERE warehouse_number = %s'
                params = [int(warehouse)]
                if fiscal_year is not None:
                    query += " AND fiscal_year = %s"
                    params.append(fiscal_year)
                if fiscal_period is not None:
                    query += " AND fiscal_period = %s"
                    params.append(fiscal_period)

                cursor.execute(query, params)
                pos_count = cursor.fetchone()[0]
                results["required_checks"]["pos_rows"] = "PASS"
                print(f"[INFO] POS rows count: {pos_count}")
            except Exception as e:
                results["required_checks"]["pos_rows"] = "FAIL"
                results["errors"].append(f"Failed to query POS transaction rows: {e}")
        else:
            results["required_checks"]["pos_rows"] = "FAIL"

        # Optional embeddings tables exist & can be queried
        # 1. leads_embeddings
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'leads_embeddings'
                """,
                (schema,)
            )
            if cursor.fetchone():
                try:
                    query = f'SELECT COUNT(*) FROM "{schema}"."leads_embeddings" WHERE warehouse_number = %s'
                    params = [int(warehouse)]
                    if fiscal_year is not None:
                        query += " AND fiscal_year = %s"
                        params.append(fiscal_year)
                    if fiscal_period is not None:
                        query += " AND fiscal_period = %s"
                        params.append(fiscal_period)

                    cursor.execute(query, params)
                    lead_emb_count = cursor.fetchone()[0]
                    results["optional_checks"]["lead_embeddings"] = "PASS"
                    print(f"[INFO] Lead embeddings rows count: {lead_emb_count}")
                except Exception as e:
                    results["optional_checks"]["lead_embeddings"] = "FAIL"
                    results["errors"].append(f"Failed to query leads_embeddings rows: {e}")
            else:
                results["optional_checks"]["lead_embeddings"] = "SKIPPED"
        except Exception as e:
            results["optional_checks"]["lead_embeddings"] = "FAIL"
            results["errors"].append(f"Failed to check leads_embeddings existence: {e}")

        # 2. pos_embeddings
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'pos_embeddings'
                """,
                (schema,)
            )
            if cursor.fetchone():
                try:
                    query = f'SELECT COUNT(*) FROM "{schema}"."pos_embeddings" WHERE warehouse_number = %s'
                    params = [int(warehouse)]
                    if fiscal_year is not None:
                        query += " AND fiscal_year = %s"
                        params.append(fiscal_year)
                    if fiscal_period is not None:
                        query += " AND fiscal_period = %s"
                        params.append(fiscal_period)

                    cursor.execute(query, params)
                    pos_emb_count = cursor.fetchone()[0]
                    results["optional_checks"]["pos_embeddings"] = "PASS"
                    print(f"[INFO] POS embeddings rows count: {pos_emb_count}")
                except Exception as e:
                    results["optional_checks"]["pos_embeddings"] = "FAIL"
                    results["errors"].append(f"Failed to query pos_embeddings rows: {e}")
            else:
                results["optional_checks"]["pos_embeddings"] = "SKIPPED"
        except Exception as e:
            results["optional_checks"]["pos_embeddings"] = "FAIL"
            results["errors"].append(f"Failed to check pos_embeddings existence: {e}")

        # 3. match_audit
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'match_audit'
                """,
                (schema,)
            )
            if cursor.fetchone():
                try:
                    query = f'SELECT COUNT(*) FROM "{schema}"."match_audit"'
                    cursor.execute(query)
                    match_audit_count = cursor.fetchone()[0]
                    results["optional_checks"]["match_audit"] = "PASS"
                    print(f"[INFO] Match audit rows count: {match_audit_count}")
                except Exception as e:
                    results["optional_checks"]["match_audit"] = "FAIL"
                    results["errors"].append(f"Failed to query match_audit rows: {e}")
            else:
                results["optional_checks"]["match_audit"] = "SKIPPED"
        except Exception as e:
            results["optional_checks"]["match_audit"] = "FAIL"
            results["errors"].append(f"Failed to check match_audit existence: {e}")

        check_hnsw_combined_indexes(cursor, schema, results)
        check_fuzzy_score_band(cursor, schema, results)
        check_mdd_transaction_exact_reconciliation(cursor, schema, warehouse, results)

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # Determine overall status
    all_required_pass = all(
        val == "PASS" for val in results["required_checks"].values()
    )
    if all_required_pass:
        results["status"] = "PASS"
    else:
        results["status"] = "FAIL"

    return results


def generate_markdown_report(results: dict, warehouse: str, fiscal_year: int, fiscal_period: int) -> str:
    timestamp = datetime.utcnow().isoformat() + "Z"
    md = []
    md.append(f"# Warehouse Smoke Test Report")
    md.append(f"")
    md.append(f"- **Warehouse**: `{warehouse}`")
    md.append(f"- **Fiscal Year**: `{fiscal_year or 'Any'}`")
    md.append(f"- **Fiscal Period**: `{fiscal_period or 'Any'}`")
    md.append(f"- **Project**: `{results['project']}`")
    md.append(f"- **Cloud SQL Connection**: `{results['cloudsql_connection_name']}`")
    md.append(f"- **Schema**: `{results['schema']}`")
    md.append(f"- **Timestamp**: `{timestamp}`")
    md.append(f"- **Overall Status**: **{results['status']}**")
    md.append(f"")
    md.append(f"## Required Checks")
    md.append(f"")
    md.append(f"| Check Name | Status |")
    md.append(f"| :--- | :--- |")
    for name, status in results["required_checks"].items():
        status_str = f"✅ {status}" if status == "PASS" else f"❌ {status}"
        md.append(f"| {name} | {status_str} |")
    md.append(f"")
    md.append(f"## Optional Checks")
    md.append(f"")
    md.append(f"| Check Name | Status |")
    md.append(f"| :--- | :--- |")
    for name, status in results["optional_checks"].items():
        if status == "PASS":
            status_str = f"✅ {status}"
        elif status == "SKIPPED":
            status_str = f"⚠️ {status}"
        else:
            status_str = f"❌ {status}"
        md.append(f"| {name} | {status_str} |")
    md.append(f"")
    if results["errors"]:
        md.append(f"## Errors Encountered")
        md.append(f"")
        for err in results["errors"]:
            md.append(f"- {err}")
        md.append(f"")
    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser(description="Warehouse Smoke Test (Safe & Config-Driven)")
    parser.add_argument(
        "--warehouse",
        default=(
            ",".join(str(value) for value in get_warehouse_scope(BUSINESS_RULES).values)
            if not get_warehouse_scope(BUSINESS_RULES).is_all
            else ""
        ),
        help="Warehouse number to validate",
    )
    parser.add_argument("--fiscal-year", type=int, help="Optional fiscal year filter")
    parser.add_argument("--fiscal-period", type=int, help="Optional fiscal period filter")
    parser.add_argument("--output-json", help="Path to write JSON output")
    parser.add_argument("--output-md", help="Path to write Markdown output")

    args = parser.parse_args()

    if not args.warehouse or not args.warehouse.isdigit():
        print(
            f"[ERROR] Smoke test requires one numeric warehouse, got: {args.warehouse!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=" * 70)
    print("WAREHOUSE SMOKE TEST (READ-ONLY, RUNTIME CONNECTIVITY)")
    print("=" * 70)
    print(f"Warehouse     : {args.warehouse}")
    print(f"Fiscal Year   : {args.fiscal_year}")
    print(f"Fiscal Period : {args.fiscal_period}")
    print("=" * 70)

    try:
        results = run_smoke_test(args.warehouse, args.fiscal_year, args.fiscal_period)
    except Exception as e:
        print(f"[ERROR] Smoke test runtime execution error: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n--- RESULTS SUMMARY ---")
    print(f"Overall Status: {results['status']}")
    print("Required Checks:")
    for k, v in results["required_checks"].items():
        print(f"  - {k}: {v}")
    print("Optional Checks:")
    for k, v in results["optional_checks"].items():
        print(f"  - {k}: {v}")
    print("-----------------------")

    if results["errors"]:
        print("\nErrors:")
        for err in results["errors"]:
            print(f"  - {err}", file=sys.stderr)

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n[INFO] JSON results written to: {output_path.absolute()}")

    if args.output_md:
        output_path = Path(args.output_md)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        md_content = generate_markdown_report(results, args.warehouse, args.fiscal_year, args.fiscal_period)
        with open(output_path, "w") as f:
            f.write(md_content + "\n")
        print(f"[INFO] Markdown report written to: {output_path.absolute()}")

    print("=" * 70)

    if results["status"] == "PASS":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
