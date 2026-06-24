#!/usr/bin/env python3
"""Very small internal validation for Cloud SQL presence and lead-to-POS rules."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lead_match_runtime.business_rules import (  # noqa: E402
    apply_blocking_rules,
    apply_deterministic_boost,
    apply_override_policy,
    assign_confidence_band,
    assign_lifecycle_state,
    classify_fiscal_relationship,
    closed_existing_lifecycle_state,
    exact_authoritative_score,
    exact_lifecycle_state,
    load_business_rules,
    resolve_pos_to_single_lead,
    select_primary_transaction,
)


DOTENV_PATH = REPO_ROOT / ".env.local"
OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_WAREHOUSE = os.environ.get("WAREHOUSE", "").strip()


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


def db_config() -> dict[str, Any]:
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

    raise RuntimeError("Set CLOUDSQL_CONNECTION_NAME or DB_HOST in .env.local")


def count_rows(cursor, schema: str, table: str, warehouse: str) -> int | None:
    if not warehouse or not warehouse.isdigit():
        return None
    cursor.execute(
        f'SELECT COUNT(*) FROM "{schema}"."{table}" WHERE warehouse_number = %s',
        (int(warehouse),),
    )
    return int(cursor.fetchone()[0])


def check_cloud_sql(warehouse: str, schema: str) -> dict[str, Any]:
    import pg8000.dbapi

    result = {
        "status": "FAIL",
        "warehouse": warehouse,
        "schema": schema,
        "connection": "FAIL",
        "tables": {},
        "row_counts": {},
        "errors": [],
    }
    try:
        conn = pg8000.dbapi.connect(**db_config())
    except Exception as exc:
        result["errors"].append(f"Cloud SQL connection failed: {exc}")
        return result

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
            ORDER BY table_name
            """,
            (schema,),
        )
        tables = {row[0] for row in cursor.fetchall()}
        result["connection"] = "PASS"
        expected = ["lead", "transaction", "leads_embeddings", "pos_embeddings", "match_decision_detail"]
        for table in expected:
            result["tables"][table] = "PASS" if table in tables else "MISSING"
            if table in tables:
                try:
                    row_count = count_rows(cursor, schema, table, warehouse)
                    result["row_counts"][table] = row_count if row_count is not None else "SKIPPED"
                except Exception as exc:
                    result["row_counts"][table] = f"ERROR: {exc}"
        result["status"] = "PASS" if all(result["tables"].get(t) == "PASS" for t in expected) else "FAIL"
    except Exception as exc:
        result["errors"].append(f"Cloud SQL inspection failed: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result


@dataclass
class RuleCheck:
    name: str
    status: str
    detail: str


def run_rule_checks() -> list[RuleCheck]:
    rules = load_business_rules()

    lead = {
        "warehouse_number": 115,
        "fiscal_year": 2026,
        "fiscal_period": 10,
        "week": 3,
        "business_name": "Soy House",
        "address_line_one": "123 Main St",
        "city": "Bellingham",
        "state": "WA",
        "zip_code": "98225",
        "email": "lead@example.com",
        "phone": "360-555-1212",
    }
    pos_same_warehouse_after = {
        "warehouse_number": 115,
        "fiscal_year": 2026,
        "fiscal_period": 10,
        "week": 4,
        "business_name": "Soy House",
        "address_line_one": "123 Main Street",
        "city": "BELLINGHAM",
        "state": "wa",
        "zip_code": "98225-1234",
        "email": "member@example.com",
        "phone": "3605551212",
    }
    pos_other_warehouse = dict(pos_same_warehouse_after, warehouse_number=943)
    pos_before_lead = dict(pos_same_warehouse_after, fiscal_period=9, week=5)

    checks: list[RuleCheck] = []

    checks.append(
        RuleCheck(
            name="same_warehouse_blocking",
            status="PASS" if apply_blocking_rules(lead, pos_same_warehouse_after) else "FAIL",
            detail="same warehouse allowed",
        )
    )
    checks.append(
        RuleCheck(
            name="different_warehouse_blocking",
            status="PASS" if not apply_blocking_rules(lead, pos_other_warehouse) else "FAIL",
            detail="different warehouse blocked",
        )
    )
    checks.append(
        RuleCheck(
            name="fiscal_new_match",
            status=(
                "PASS"
                if classify_fiscal_relationship(lead, pos_same_warehouse_after, rules)
                == exact_lifecycle_state(rules)
                else "FAIL"
            ),
            detail=classify_fiscal_relationship(lead, pos_same_warehouse_after, rules),
        )
    )
    checks.append(
        RuleCheck(
            name="fiscal_closed_existing",
            status=(
                "PASS"
                if classify_fiscal_relationship(lead, pos_before_lead, rules)
                == closed_existing_lifecycle_state(rules)
                else "FAIL"
            ),
            detail=classify_fiscal_relationship(lead, pos_before_lead, rules),
        )
    )

    exact_wins = apply_override_policy(
        {"score": exact_authoritative_score(rules), "match_type": rules["decision_rules"]["exact_match_type"]},
        {
            "score": min(99, rules["decision_rules"]["fuzzy_max_score"]),
            "match_type": rules["decision_rules"]["fuzzy_match_type"],
        },
        rules,
    )
    checks.append(
        RuleCheck(
            name="exact_is_authoritative",
            status=(
                "PASS"
                if exact_wins
                and exact_wins["match_type"] == rules["decision_rules"]["exact_match_type"]
                else "FAIL"
            ),
            detail=str(exact_wins),
        )
    )

    boosted = apply_deterministic_boost(90, lead, pos_same_warehouse_after, rules)
    boost_cap = rules["scoring"]["deterministic_boosts"]["cap"]
    checks.append(
        RuleCheck(
            name="deterministic_boost_cap",
            status="PASS" if boosted <= boost_cap else "FAIL",
            detail=f"boosted_score={boosted}",
        )
    )

    band = assign_confidence_band(93, rules)
    expected_band = rules["decision_rules"]["fuzzy_score_bands"][0]
    checks.append(
        RuleCheck(
            name="confidence_band_high",
            status=(
                "PASS"
                if band["name"] == expected_band["name"]
                and band["state"] == expected_band["lifecycle_state"]
                else "FAIL"
            ),
            detail=str(band),
        )
    )

    matches = [
        {
            "lead_id": "L1",
            "pos_id": "P1",
            "final_score": 95,
            "fiscal_year": 2026,
            "fiscal_period": 10,
            "week": 4,
        },
        {
            "lead_id": "L1",
            "pos_id": "P2",
            "final_score": 88,
            "fiscal_year": 2026,
            "fiscal_period": 10,
            "week": 5,
        },
    ]
    selected = select_primary_transaction(matches, rules)
    checks.append(
        RuleCheck(
            name="primary_transaction_earliest",
            status="PASS" if selected[0]["primary_transaction"] is True and selected[1]["primary_transaction"] is False else "FAIL",
            detail=str(selected),
        )
    )

    resolved = resolve_pos_to_single_lead(
        [
            {"pos_id": "P1", "lead_id": "L1", "final_score": 91},
            {"pos_id": "P1", "lead_id": "L2", "final_score": 90},
        ],
        rules,
    )
    checks.append(
        RuleCheck(
            name="pos_to_lead_resolution",
            status=(
                "PASS"
                if resolved and resolved[0]["match_type"] == rules["resolution"]["ambiguity_match_type"]
                else "FAIL"
            ),
            detail=str(resolved),
        )
    )

    lifecycle = assign_lifecycle_state({"closed_existing_flag": True, "final_score": 10}, rules)
    checks.append(
        RuleCheck(
            name="lifecycle_closed_existing",
            status="PASS" if lifecycle == closed_existing_lifecycle_state(rules) else "FAIL",
            detail=lifecycle,
        )
    )

    return checks


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Lead Match Internal Validation",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Warehouse: `{payload['warehouse']}`",
        f"- Schema: `{payload['schema']}`",
        f"- Project: `{payload['project']}`",
        f"- Overall: **{payload['overall_status']}**",
        "",
        "## Cloud SQL",
        "",
        f"- Connection: `{payload['cloud_sql']['connection']}`",
    ]
    for table, status in payload["cloud_sql"]["tables"].items():
        rows = payload["cloud_sql"]["row_counts"].get(table, "n/a")
        lines.append(f"- `{table}`: `{status}` rows=`{rows}`")
    if payload["cloud_sql"]["errors"]:
        lines.append("")
        lines.append("Errors:")
        for err in payload["cloud_sql"]["errors"]:
            lines.append(f"- {err}")
    lines.extend(["", "## Rule Checks", "", "| Check | Status | Detail |", "| :--- | :--- | :--- |"])
    for check in payload["rule_checks"]:
        lines.append(f"| {check['name']} | {check['status']} | {check['detail']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default=DEFAULT_WAREHOUSE)
    parser.add_argument("--schema", default=os.environ.get("DB_SCHEMA", "leadmgmt"))
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-rules", action="store_true")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    load_dotenv(DOTENV_PATH)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warehouse = args.warehouse or "ALL"

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "warehouse": warehouse,
        "schema": args.schema,
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT", "ctoteam"),
        "cloud_sql": {
            "status": "SKIPPED",
            "connection": "SKIPPED",
            "tables": {},
            "row_counts": {},
            "errors": [],
        },
        "rule_checks": [],
        "overall_status": "FAIL",
    }

    if not args.skip_db:
        payload["cloud_sql"] = check_cloud_sql(args.warehouse, args.schema)

    if not args.skip_rules:
        payload["rule_checks"] = [asdict(check) for check in run_rule_checks()]

    all_rules_pass = all(check["status"] == "PASS" for check in payload["rule_checks"]) if payload["rule_checks"] else False
    cloud_sql_ok = payload["cloud_sql"]["status"] == "PASS" if not args.skip_db else True
    payload["overall_status"] = "PASS" if all_rules_pass and cloud_sql_ok else "FAIL"

    summary_json = output_dir / "validation_summary.json"
    summary_md = output_dir / "validation_summary.md"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n")
    summary_md.write_text(render_markdown(payload))

    print(summary_json)
    print(summary_md)
    print(payload["overall_status"])
    return 0 if payload["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
