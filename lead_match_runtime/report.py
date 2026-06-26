import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import storage

from lead_match_runtime.business_rules import (
    exact_lifecycle_state,
    fuzzy_match_types,
    fuzzy_max_score,
    fuzzy_qualify_min_score,
    get_cloudsql_connection_name,
    get_dry_run,
    get_project_id,
    get_report_bucket,
    lifecycle_state_for_match_type,
    normalize_fuzzy_final_score,
)
from lead_match_runtime.job_runner import (
    BUSINESS_RULES,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    connect,
    schema_name,
    warehouse_scope,
    warehouse_scope_label,
)


FUZZY_MATCH_TYPES = set(fuzzy_match_types(BUSINESS_RULES))


def _scope_clause(alias: str, params: list) -> str:
    scope = warehouse_scope()
    if scope.is_all:
        return ""
    placeholders = ", ".join(["%s"] * len(scope.values))
    params.extend(scope.values)
    return f"AND {alias}.warehouse_number IN ({placeholders})"


def _count(cursor, schema: str, table: str, warehouse_column: str | None = "warehouse_number") -> int:
    params = []
    clause = _scope_clause("x", params) if warehouse_column else ""
    cursor.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}" x WHERE 1=1 {clause}', params)
    return int(cursor.fetchone()[0])


def _latest_match_run_id(cursor, schema: str) -> str | None:
    params = []
    clause = _scope_clause("m", params)
    cursor.execute(
        f"""
        SELECT match_run_id
        FROM "{schema}"."match_decision_detail" m
        WHERE 1=1 {clause}
        ORDER BY created_date DESC
        LIMIT 1
        """,
        params,
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _state_counts(cursor) -> dict[str, int]:
    cursor.execute(
        """
        SELECT COALESCE(state, 'unknown') AS state, COUNT(*)
        FROM pg_stat_activity
        GROUP BY COALESCE(state, 'unknown')
        ORDER BY state
        """
    )
    return {str(row[0]): int(row[1]) for row in cursor.fetchall()}


def _build_matching_comments(row: dict) -> str:
    match_type = str(row.get("match_type") or "").strip()
    score = row.get("final_score") or 0
    parts = []
    if match_type.lower() == "exact":
        parts.append(f"Complete Match (score {score}/{score}).")
        fields = []
        if row.get("pos_business_name"):
            fields.append("business_name -> oms_company")
        if row.get("address_line_one"):
            fields.append("address_line_one -> oms_address_line_1")
        if row.get("email"):
            fields.append("email -> oms_email_1")
        if row.get("phone"):
            fields.append("phone -> oms_phone_1")
        if row.get("zip_code"):
            fields.append("zip_code -> oms_zip")
        if row.get("city"):
            fields.append("city -> oms_city")
        if row.get("state"):
            fields.append("state -> oms_state")
        if fields:
            parts.append("Fields matched: " + ", ".join(fields) + ".")
    else:
        winning_set = row.get("winning_set") or ""
        name_score = row.get("business_name_score")
        addr_score = row.get("full_address_score")
        email_boost = row.get("email_boost") or 0
        phone_boost = row.get("phone_boost") or 0
        parts.append(f"Fuzzy match (set {winning_set}, score {score:.2f}).")
        if name_score is not None:
            parts.append(f"name_score={name_score:.1f}")
        if addr_score is not None:
            parts.append(f"addr_score={addr_score:.1f}")
        if email_boost:
            parts.append(f"email_boost=+{email_boost:.0f}")
        if phone_boost:
            parts.append(f"phone_boost=+{phone_boost:.0f}")
    if row.get("primary_transaction"):
        parts.append("Designated as primary transaction (earliest fiscal period for this lead).")
    return " ".join(parts)


def _fetch_match_rows(cursor, schema: str, match_run_id: str) -> list[dict]:
    params = [match_run_id]
    clause = _scope_clause("m", params)
    cursor.execute(
        f"""
        WITH base AS (
            SELECT
                m.match_run_id,
                m.lead_id,
                m.pos_id,
                m.warehouse_number,
                m.match_type,
                m.final_score,
                m.combined_field_score,
                m.full_address_score,
                m.business_name_score,
                COALESCE(m.email_boost, 0) AS email_boost,
                COALESCE(m.phone_boost, 0) AS phone_boost,
                m.winning_set,
                m.embedding_model,
                m.lifecycle_state,
                m.created_date,
                l.fiscal_year AS lead_fiscal_year,
                l.fiscal_period AS lead_fiscal_period,
                COALESCE(l.week, 0) AS lead_week,
                t.fiscal_year AS pos_fiscal_year,
                t.fiscal_period AS pos_fiscal_period,
                COALESCE(t.week, 0) AS pos_week,
                a.business_name AS lead_business_name,
                t.business_name AS pos_business_name,
                t.order_amount,
                t.account_number,
                t.transaction_count,
                t.membership_number,
                t.sales_reference_id,
                t.shop_type,
                t.bd_industry,
                t.industry_description,
                t.first_name,
                t.last_name,
                t.address_line_one,
                t.address_line_two,
                t.city,
                t.state,
                t.zip_code,
                t.email,
                t.phone,
                t.primary_transaction AS tx_primary_transaction,
                CASE WHEN m.lifecycle_state = 'Closed - Existing' THEN true ELSE false END AS closed_existing_flag,
                t.updated_date AS tx_updated_date
            FROM "{schema}"."match_decision_detail" m
            JOIN "{schema}"."lead" l ON l.lead_id = m.lead_id
            JOIN "{schema}"."account" a ON a.account_id = l.account_id
            JOIN "{schema}"."transaction" t ON t.pos_id = m.pos_id
            WHERE m.match_run_id = %s
              {clause}
        )
        SELECT *
        FROM base
        ORDER BY final_score DESC, lead_id, pos_id
        """,
        params,
    )
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        row["primary_transaction"] = False
        match_type = str(row.get("match_type") or "").strip().lower()
        if match_type in FUZZY_MATCH_TYPES and row.get("final_score") is not None:
            normalized = normalize_fuzzy_final_score(
                row["final_score"],
                config=BUSINESS_RULES,
                lead_id=row.get("lead_id"),
                pos_id=row.get("pos_id"),
                reject_below_floor=False,
            )
            if normalized is not None:
                row["final_score"] = normalized
        if not row.get("lifecycle_state"):
            row["lifecycle_state"] = lifecycle_state_for_match_type(
                row.get("match_type"),
                row.get("final_score"),
                BUSINESS_RULES,
            )
        score = row.get("final_score") or 0
        if match_type in FUZZY_MATCH_TYPES:
            row["match_result"] = "Potential"
        elif score >= 100:
            row["match_result"] = "Match"
        else:
            row["match_result"] = ""
    closed_state = exact_lifecycle_state(BUSINESS_RULES)
    closed_rows_by_lead: dict = {}
    for row in rows:
        if row.get("lifecycle_state") == closed_state:
            closed_rows_by_lead.setdefault(row.get("lead_id"), []).append(row)
    for lead_rows in closed_rows_by_lead.values():
        ordered = sorted(
            lead_rows,
            key=lambda row: (
                row.get("pos_fiscal_year") or 0,
                row.get("pos_fiscal_period") or 0,
                row.get("pos_week") or 0,
                row.get("pos_id") or "",
            ),
        )
        if ordered:
            ordered[0]["primary_transaction"] = True
    for row in rows:
        row["matched_by"] = "System"
        row["matching_comments"] = _build_matching_comments(row)
        row["similarity_score"] = row.get("final_score", 0)
        row["business_name_transaction"] = row.get("pos_business_name", "")
        oa = row.get("order_amount") or 0
        row["u_matched_lead_number"] = row.get("lead_id", "")
        row["u_order_amount"] = oa
        row["u_order_amount_rounded"] = round(float(oa), 2) if oa else 0
        row["fiscal_year_transaction"] = row.get("pos_fiscal_year", "")
        row["fiscal_period_transaction"] = row.get("pos_fiscal_period", "")
        row["week"] = row.get("pos_week", "")
        if row.get("tx_updated_date"):
            row["updated_date"] = row["tx_updated_date"]
    return rows


PRIMARY_MATCH_OUTPUT_COLUMNS = [
    "lead_id", "pos_id", "match_result", "similarity_score", "winning_set",
    "match_type", "primary_transaction", "matched_by", "matching_comments",
    "closed_existing_flag", "account_number", "transaction_count",
    "business_name_transaction", "membership_number", "warehouse_number",
    "sales_reference_id", "fiscal_year_transaction", "fiscal_period_transaction",
    "week", "shop_type", "bd_industry", "order_amount", "industry_description",
    "first_name", "last_name", "address_line_one", "address_line_two",
    "city", "state", "zip_code", "email", "phone",
    "u_matched_lead_number", "u_order_amount", "u_order_amount_rounded",
    "updated_date",
]

INTERNAL_REPORT_COLUMNS = [
    "match_run_id", "lead_id", "pos_id", "warehouse_number", "match_type",
    "lifecycle_state", "primary_transaction", "final_score",
    "combined_field_score", "full_address_score", "business_name_score",
    "email_boost", "phone_boost", "winning_set", "embedding_model",
    "lead_fiscal_year", "lead_fiscal_period", "lead_week",
    "pos_fiscal_year", "pos_fiscal_period", "pos_week",
    "lead_business_name", "pos_business_name", "order_amount",
    "created_date",
]


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PRIMARY_MATCH_OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_internal_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=INTERNAL_REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _upload(bucket_name: str, local_path: Path, object_name: str) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(local_path))
    return f"gs://{bucket_name}/{object_name}"


def run_report() -> dict:
    schema = schema_name()
    warehouse = warehouse_scope_label()
    project = get_project_id(BUSINESS_RULES)
    bucket = get_report_bucket(BUSINESS_RULES)
    match_run_id = os.environ.get("MATCH_RUN_ID")
    dry_run = get_dry_run(BUSINESS_RULES)
    generated_at = datetime.now(UTC)

    conn = connect()
    cursor = conn.cursor()
    try:
        if not match_run_id:
            match_run_id = _latest_match_run_id(cursor, schema)
        if not match_run_id:
            raise RuntimeError("No match_run_id supplied and no previous match run was found")

        cursor.execute("SELECT pg_backend_pid()")
        backend_pid = int(cursor.fetchone()[0])

        rows = _fetch_match_rows(cursor, schema, match_run_id)
        state_counts = _state_counts(cursor)
        summary = {
            "project": project,
            "schema": schema,
            "warehouse": warehouse,
            "match_run_id": match_run_id,
            "dry_run": dry_run,
            "generated_at": generated_at.isoformat(),
            "cloudsql_connection_name": get_cloudsql_connection_name(BUSINESS_RULES),
            "cloudsql_backend_pid": backend_pid,
            "cloudsql_session_state_counts": state_counts,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "lead_rows": _count(cursor, schema, "lead"),
            "pos_rows": _count(cursor, schema, "transaction"),
            "lead_embedding_rows": _count(cursor, schema, "leads_embeddings"),
            "pos_embedding_rows": _count(cursor, schema, "pos_embeddings"),
            "match_rows": len(rows),
            "match_type_counts": {},
            "lifecycle_state_counts": {},
            "primary_transaction_count": 0,
            "fuzzy_score_band": {
                "floor": fuzzy_qualify_min_score(BUSINESS_RULES),
                "ceiling": fuzzy_max_score(BUSINESS_RULES),
            },
        }
        for row in rows:
            summary["match_type_counts"][row["match_type"]] = (
                summary["match_type_counts"].get(row["match_type"], 0) + 1
            )
            summary["lifecycle_state_counts"][row["lifecycle_state"]] = (
                summary["lifecycle_state_counts"].get(row["lifecycle_state"], 0) + 1
            )
            if row["primary_transaction"]:
                summary["primary_transaction_count"] += 1

        report_dir = Path("/tmp/lead_match_report")
        report_dir.mkdir(parents=True, exist_ok=True)
        summary_path = report_dir / "summary.json"
        csv_path = report_dir / "matches.csv"
        internal_csv_path = report_dir / "matches_internal.csv"
        md_path = report_dir / "report.md"
        summary_path.write_text(json.dumps(summary, indent=2, default=_json_default) + "\n")
        _write_csv(csv_path, rows)
        _write_internal_csv(internal_csv_path, rows)
        md_path.write_text(
            "\n".join(
                [
                    "# Lead Match Run Report",
                    "",
                    f"- Project: `{project}`",
                    f"- Schema: `{schema}`",
                    f"- Warehouse: `{warehouse}`",
                    f"- Match run ID: `{match_run_id}`",
                    f"- Dry run: `{dry_run}`",
                    f"- Generated UTC: `{generated_at.isoformat()}`",
                    f"- Embedding model: `{EMBEDDING_MODEL}`",
                    f"- Embedding dimension: `{EMBEDDING_DIMENSION}`",
                    f"- Cloud SQL connection: `{summary['cloudsql_connection_name']}`",
                    f"- Cloud SQL backend PID: `{backend_pid}`",
                    "",
                    "## Counts",
                    "",
                    f"- Leads: `{summary['lead_rows']}`",
                    f"- POS transactions: `{summary['pos_rows']}`",
                    f"- Lead embeddings: `{summary['lead_embedding_rows']}`",
                    f"- POS embeddings: `{summary['pos_embedding_rows']}`",
                    f"- Match rows: `{summary['match_rows']}`",
                    f"- Primary transactions: `{summary['primary_transaction_count']}`",
                    "",
                    "## Match Types",
                    "",
                    "```json",
                    json.dumps(summary["match_type_counts"], indent=2),
                    "```",
                    "",
                    "## Lifecycle States",
                    "",
                    "```json",
                    json.dumps(summary["lifecycle_state_counts"], indent=2),
                    "```",
                    "",
                    "## Cloud SQL Sessions",
                    "",
                    "```json",
                    json.dumps(state_counts, indent=2),
                    "```",
                    "",
                ]
            )
            + "\n"
        )

        prefix = os.environ.get(
            "REPORT_PREFIX",
            f"reports/lead_match/{project}/{warehouse}/{match_run_id}",
        ).strip("/")
        if dry_run:
            summary_name = "dryrun_summary.json"
            csv_name = "dryrun_matches.csv"
            md_name = "dryrun_report.md"
        else:
            summary_name = "summary.json"
            csv_name = "matches.csv"
            md_name = "report.md"
        if dry_run:
            internal_csv_name = "dryrun_matches_internal.csv"
        else:
            internal_csv_name = "matches_internal.csv"
        summary["report_uris"] = {
            "summary_json": _upload(bucket, summary_path, f"{prefix}/{summary_name}"),
            "matches_csv": _upload(bucket, csv_path, f"{prefix}/{csv_name}"),
            "matches_internal_csv": _upload(bucket, internal_csv_path, f"{prefix}/{internal_csv_name}"),
            "report_md": _upload(bucket, md_path, f"{prefix}/{md_name}"),
        }
        summary_path.write_text(json.dumps(summary, indent=2, default=_json_default) + "\n")
        _upload(bucket, summary_path, f"{prefix}/{summary_name}")
        print(json.dumps(summary, indent=2, default=_json_default))
        return summary
    finally:
        conn.close()
