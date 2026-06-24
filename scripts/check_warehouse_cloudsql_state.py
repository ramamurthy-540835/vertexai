#!/usr/bin/env python3
"""
Warehouse Cloud SQL State Diagnostic

Read-only diagnostic tool that queries row counts per warehouse across all
lead match tables in Cloud SQL. Designed for comparing warehouse data
availability (e.g., warehouse 115 vs 569).

Usage:
  python scripts/check_warehouse_cloudsql_state.py --warehouses 115,569
  python scripts/check_warehouse_cloudsql_state.py --warehouses 115,569 --json

Guards:
  - Requires RUN_CLOUDSQL_INTEGRATION_TESTS=true or .env.local present
  - Read-only: no INSERT, UPDATE, DELETE, or DDL
  - Uses pg8000.dbapi (same driver as lead_match_runtime)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pg8000.dbapi

REPO_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = REPO_ROOT / ".env.local"

TABLES_WITH_WAREHOUSE = (
    "lead",
    "transaction",
    "leads_embeddings",
    "pos_embeddings",
    "match_decision_detail",
)

TABLES_GLOBAL = ("match_audit",)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def check_access_guard() -> None:
    if os.environ.get("RUN_CLOUDSQL_INTEGRATION_TESTS") == "true":
        return
    if DOTENV_PATH.exists():
        return
    print(
        "Access denied: set RUN_CLOUDSQL_INTEGRATION_TESTS=true or provide .env.local",
        file=sys.stderr,
    )
    sys.exit(1)


def db_config() -> dict:
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise RuntimeError("DB_PASSWORD is required")

    host = os.environ.get("DB_HOST")
    port = int(os.environ.get("DB_PORT", "5432"))
    database = os.environ.get("DB_NAME", "postgres")
    user = os.environ.get("DB_USER", "postgres")

    conn_name = os.environ.get("CLOUDSQL_CONNECTION_NAME")
    socket_dir = os.environ.get("CLOUDSQL_SOCKET_DIR", "/cloudsql")

    if conn_name and Path(socket_dir).exists():
        return {
            "unix_sock": f"{socket_dir}/{conn_name}/.s.PGSQL.5432",
            "database": database,
            "user": user,
            "password": password,
        }

    if host:
        return {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }

    raise RuntimeError(
        "No database connection available: set CLOUDSQL_CONNECTION_NAME (with socket) or DB_HOST"
    )


def connect() -> pg8000.dbapi.Connection:
    return pg8000.dbapi.connect(**db_config())


def table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return cursor.fetchone() is not None


def count_rows(cursor, schema: str, table: str, warehouse: int) -> int | None:
    if not table_exists(cursor, schema, table):
        return None
    cursor.execute(
        f'SELECT COUNT(*) FROM "{schema}"."{table}" WHERE warehouse_number = %s',
        (warehouse,),
    )
    return int(cursor.fetchone()[0])


def count_global_rows(cursor, schema: str, table: str) -> int | None:
    if not table_exists(cursor, schema, table):
        return None
    cursor.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
    return int(cursor.fetchone()[0])


def latest_match_run_id(cursor, schema: str, warehouse: int) -> str | None:
    if not table_exists(cursor, schema, "match_decision_detail"):
        return None
    cursor.execute(
        f"""
        SELECT match_run_id
        FROM "{schema}"."match_decision_detail"
        WHERE warehouse_number = %s
        ORDER BY created_date DESC
        LIMIT 1
        """,
        (warehouse,),
    )
    row = cursor.fetchone()
    return str(row[0]) if row else None


def query_warehouse_state(
    cursor, schema: str, warehouses: list[int]
) -> dict[int, dict[str, int | None]]:
    state: dict[int, dict[str, int | None]] = {}
    for wh in warehouses:
        row_counts: dict[str, int | None] = {}
        for table in TABLES_WITH_WAREHOUSE:
            row_counts[table] = count_rows(cursor, schema, table, wh)
        for table in TABLES_GLOBAL:
            row_counts[table] = count_global_rows(cursor, schema, table)
        row_counts["latest_match_run_id"] = latest_match_run_id(cursor, schema, wh)
        state[wh] = row_counts
    return state


def diagnose_issues(state: dict[int, dict]) -> list[dict]:
    findings: list[dict] = []
    for wh, counts in state.items():
        leads = counts.get("lead") or 0
        pos = counts.get("transaction") or 0
        lead_emb = counts.get("leads_embeddings") or 0
        pos_emb = counts.get("pos_embeddings") or 0
        matches = counts.get("match_decision_detail") or 0

        if leads == 0 and pos == 0 and lead_emb == 0 and pos_emb == 0 and matches == 0:
            findings.append({
                "warehouse": wh,
                "severity": "ERROR",
                "message": f"Warehouse {wh} has no data in any table",
            })
            continue

        if leads > 0 and pos == 0:
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": f"Warehouse {wh} has {leads} leads but no POS transaction rows",
            })

        if pos > 0 and leads == 0:
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": f"Warehouse {wh} has {pos} POS transactions but no lead rows",
            })

        if leads > 0 and lead_emb == 0:
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": (
                    f"Warehouse {wh} has {leads} leads but 0 lead embeddings "
                    "-- lead embedding generation may not have run"
                ),
            })
        elif leads > 0 and lead_emb < leads:
            pct = round(100 * lead_emb / leads, 1)
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": (
                    f"Warehouse {wh} has {leads} leads but only {lead_emb} "
                    f"lead embeddings ({pct}% coverage)"
                ),
            })

        if pos > 0 and pos_emb == 0:
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": (
                    f"Warehouse {wh} has {pos} POS rows but 0 pos_embeddings "
                    "-- POS embedding generation may not have run"
                ),
            })

        if lead_emb > 0 and pos_emb > 0 and matches == 0:
            findings.append({
                "warehouse": wh,
                "severity": "WARNING",
                "message": (
                    f"Warehouse {wh} has embeddings but 0 match decisions "
                    "-- fuzzy matching may not have run"
                ),
            })

    return findings


def _status_for_warehouse(counts: dict, findings: list[dict], wh: int) -> str:
    wh_errors = [f for f in findings if f["warehouse"] == wh and f["severity"] == "ERROR"]
    wh_warnings = [f for f in findings if f["warehouse"] == wh and f["severity"] == "WARNING"]
    if wh_errors:
        return "FAIL"
    if wh_warnings:
        return "WARNING"
    return "PASS"


def format_comparison_table(state: dict, findings: list[dict]) -> str:
    warehouses = sorted(state.keys())
    all_tables = list(TABLES_WITH_WAREHOUSE) + list(TABLES_GLOBAL) + ["latest_match_run_id", "status"]

    col_width = 28
    wh_width = 14

    lines = []
    header = f"{'Table':<{col_width}}"
    for wh in warehouses:
        header += f" | {'WH ' + str(wh):>{wh_width}}"
    lines.append(header)
    lines.append("-" * col_width + ("-+-" + "-" * wh_width) * len(warehouses))

    for table in all_tables:
        row = f"{table:<{col_width}}"
        for wh in warehouses:
            if table == "status":
                val = _status_for_warehouse(state[wh], findings, wh)
            elif table == "latest_match_run_id":
                val = state[wh].get("latest_match_run_id") or "none"
                if len(val) > wh_width:
                    val = val[:wh_width - 2] + ".."
            else:
                count = state[wh].get(table)
                val = str(count) if count is not None else "N/A"
            row += f" | {val:>{wh_width}}"
        lines.append(row)

    if findings:
        lines.append("")
        lines.append("Diagnostics:")
        for f in findings:
            lines.append(f"  [{f['severity']}] {f['message']}")

    return "\n".join(lines)


def format_json_output(state: dict, findings: list[dict], schema: str) -> str:
    warehouses = sorted(state.keys())
    warehouse_results = []
    for wh in warehouses:
        warehouse_results.append({
            "warehouse": wh,
            "row_counts": {k: v for k, v in state[wh].items() if k != "latest_match_run_id"},
            "latest_match_run_id": state[wh].get("latest_match_run_id"),
            "status": _status_for_warehouse(state[wh], findings, wh),
        })
    output = {
        "schema": schema,
        "warehouses": warehouse_results,
        "diagnostics": findings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(output, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Warehouse Cloud SQL State Diagnostic (read-only)"
    )
    parser.add_argument(
        "--warehouses",
        required=True,
        help="Comma-separated warehouse numbers, e.g. 115,569",
    )
    parser.add_argument(
        "--schema",
        default=os.environ.get("DB_SCHEMA", "leadmgmt"),
        help="Database schema (default: leadmgmt)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output JSON instead of table",
    )
    parser.add_argument(
        "--output",
        help="Write results to file",
    )
    args = parser.parse_args()

    warehouses: list[int] = []
    for token in args.warehouses.split(","):
        token = token.strip()
        if not token.isdigit():
            print(f"Invalid warehouse value: {token!r}", file=sys.stderr)
            sys.exit(1)
        warehouses.append(int(token))

    if not warehouses:
        print("No warehouses specified", file=sys.stderr)
        sys.exit(1)

    check_access_guard()
    load_dotenv(DOTENV_PATH)

    print("=" * 60)
    print("WAREHOUSE CLOUD SQL STATE DIAGNOSTIC (READ-ONLY)")
    print("=" * 60)
    print(f"Warehouses : {', '.join(str(w) for w in warehouses)}")
    print(f"Schema     : {args.schema}")
    print("=" * 60)

    try:
        conn = connect()
    except Exception as e:
        print(f"Cloud SQL connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        cursor = conn.cursor()
        state = query_warehouse_state(cursor, args.schema, warehouses)
        findings = diagnose_issues(state)

        if args.json_output:
            output = format_json_output(state, findings, args.schema)
        else:
            output = format_comparison_table(state, findings)

        print()
        print(output)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output + "\n")
            print(f"\nResults written to: {args.output}")

    finally:
        try:
            conn.close()
        except Exception:
            pass

    has_errors = any(f["severity"] == "ERROR" for f in findings)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
