import argparse
import os
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import numpy as np
import pg8000.dbapi
from google import genai
from google.genai import types

from lead_match_runtime.business_rules import (
    build_embedding_text,
    get_project_id,
    get_schema,
    get_warehouse_scope,
    load_business_rules,
)


BUSINESS_RULES = load_business_rules()
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", BUSINESS_RULES["embeddings"]["model"])
EMBEDDING_DIMENSION = int(
    os.environ.get("EMBEDDING_DIMENSION", BUSINESS_RULES["embeddings"]["output_dimensionality"])
)
DEFAULT_FISCAL_YEAR = int(os.environ.get("DEFAULT_FISCAL_YEAR", "2026"))
DEFAULT_FISCAL_PERIOD = int(os.environ.get("DEFAULT_FISCAL_PERIOD", "10"))
DEFAULT_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.environ.get("EMBEDDING_MAX_WORKERS", "3")))
EMBEDDING_MAX_RETRIES = max(1, int(os.environ.get("EMBEDDING_MAX_RETRIES", "6")))
EMBEDDING_RETRY_BASE_DELAY = float(os.environ.get("EMBEDDING_RETRY_BASE_DELAY", "1.5"))
EMBEDDING_RETRY_MAX_DELAY = float(os.environ.get("EMBEDDING_RETRY_MAX_DELAY", "90"))
MATCH_BATCH_SIZE = max(1, int(os.environ.get("MATCH_BATCH_SIZE", "100")))
MATCH_STATEMENT_TIMEOUT_MS = int(os.environ.get("MATCH_STATEMENT_TIMEOUT_MS", "900000"))

EXPECTED_PROJECT = os.environ.get("EXPECTED_PROJECT_ID", BUSINESS_RULES["environment"]["project_id"])
EXPECTED_CLOUDSQL_CONNECTION_NAME = "ctoteam:us-central1:lead-mgmt-db"


def _require_env(name, expected):
    actual = os.environ.get(name)
    if actual != expected:
        raise RuntimeError(
            f"Refusing to start: env {name}={actual!r}, expected {expected!r}"
        )


def assert_isolated_runtime():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project != EXPECTED_PROJECT:
        raise RuntimeError(
            f"Refusing to start: GOOGLE_CLOUD_PROJECT={project!r}, "
            f"expected {EXPECTED_PROJECT!r}"
        )
    conn = os.environ.get("CLOUDSQL_CONNECTION_NAME")
    if conn and conn not in (EXPECTED_CLOUDSQL_CONNECTION_NAME, "ctoteam:us-central1"):
        raise RuntimeError(
            f"Refusing to start: CLOUDSQL_CONNECTION_NAME={conn!r}, "
            f"expected {EXPECTED_CLOUDSQL_CONNECTION_NAME!r} or 'ctoteam:us-central1'"
        )
    _require_env("ALLOW_CLIENT_GCP", "false")
    _require_env("ALLOW_PRODUCTION", "false")


def db_config():
    missing = [
        name
        for name in ("DB_NAME", "DB_USER", "DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required DB env vars: {', '.join(missing)}")

    conn_name = os.environ.get("CLOUDSQL_CONNECTION_NAME")
    if conn_name:
        socket_dir = os.environ.get("CLOUDSQL_SOCKET_DIR", "/cloudsql")
        return {
            "unix_sock": f"{socket_dir}/{conn_name}/.s.PGSQL.5432",
            "database": os.environ["DB_NAME"],
            "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"],
        }

    if os.environ.get("ALLOW_LOCAL_DB", "false").lower() != "true":
        raise RuntimeError(
            "CLOUDSQL_CONNECTION_NAME is not set and ALLOW_LOCAL_DB is not 'true'; "
            "refusing to fall back to DB_HOST/DB_PORT."
        )
    if not os.environ.get("DB_HOST"):
        raise RuntimeError("Missing DB_HOST for local DB connection")
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "database": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def connect():
    assert_isolated_runtime()
    return pg8000.dbapi.connect(**db_config())


def schema_name():
    return get_schema(BUSINESS_RULES)


def warehouse_scope():
    return get_warehouse_scope(BUSINESS_RULES)


def warehouse_sql_filter(alias, params):
    scope = warehouse_scope()
    if scope.is_all:
        return ""
    placeholders = ", ".join(["%s"] * len(scope.values))
    params.extend(scope.values)
    return f"AND {alias}.warehouse_number IN ({placeholders})"


def warehouse_scope_label():
    scope = warehouse_scope()
    return "ALL" if scope.is_all else ",".join(str(value) for value in scope.values)


def vertex_client():
    project = os.environ.get("VERTEX_PROJECT_ID") or get_project_id(BUSINESS_RULES)
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    if not project:
        raise RuntimeError("Missing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT")
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(api_version="v1"),
    )


def vector_literal(values):
    if values is None:
        return None
    arr = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm:
        arr = arr / norm
    return "[" + ",".join(f"{float(value):.8f}" for value in arr.tolist()) + "]"


def is_retryable_embedding_error(exc):
    error = str(exc).lower()
    retryable_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "deadline",
        "temporarily unavailable",
        "timeout",
        "connection reset",
        "resource exhausted",
        "quota",
        "rate limit",
    )
    return any(marker in error for marker in retryable_markers)


def embed_texts(client, texts, label="embedding"):
    normalized = [(text or "").strip() for text in texts]
    if not any(normalized):
        return [None] * len(normalized)

    started = time.monotonic()
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=normalized,
                config=types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY",
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            duration = time.monotonic() - started
            print(
                f"Embedded {label}: rows={len(normalized)} attempt={attempt} "
                f"duration_seconds={duration:.2f}"
            )
            return [vector_literal(embedding.values) for embedding in response.embeddings]
        except Exception as exc:
            if attempt >= EMBEDDING_MAX_RETRIES or not is_retryable_embedding_error(exc):
                print(
                    f"Embedding failed for {label}: rows={len(normalized)} "
                    f"attempt={attempt} error={exc}",
                    file=sys.stderr,
                )
                raise
            delay = min(
                EMBEDDING_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1.5),
                EMBEDDING_RETRY_MAX_DELAY,
            )
            print(
                f"Retrying {label}: rows={len(normalized)} attempt={attempt} "
                f"delay_seconds={delay:.2f} error={exc}"
            )
            time.sleep(delay)

    raise RuntimeError(f"Embedding failed for {label}")


def embed_field_batches(client, field_texts):
    if DEFAULT_MAX_WORKERS == 1 or len(field_texts) == 1:
        return {
            field: embed_texts(client, texts, label=field)
            for field, texts in field_texts.items()
        }

    results = {}
    max_workers = min(DEFAULT_MAX_WORKERS, len(field_texts))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_field = {
            executor.submit(embed_texts, client, texts, field): field
            for field, texts in field_texts.items()
        }
        for future in as_completed(future_to_field):
            field = future_to_field[future]
            results[field] = future.result()
    return results


def chunks(rows, size):
    for idx in range(0, len(rows), size):
        yield rows[idx:idx + size]


def execute_many_values(cursor, insert_prefix, rows, fields_per_row, chunk_size=250):
    if not rows:
        return
    row_placeholder = "(" + ", ".join(["%s"] * fields_per_row) + ")"
    for chunk in chunks(rows, chunk_size):
        placeholders = ", ".join([row_placeholder] * len(chunk))
        params = [value for row in chunk for value in row]
        cursor.execute(f"{insert_prefix} VALUES {placeholders}", params)


def lead_source_rows(cursor):
    schema = schema_name()
    params = [DEFAULT_FISCAL_YEAR, DEFAULT_FISCAL_PERIOD]
    warehouse_clause = warehouse_sql_filter("l", params)

    cursor.execute(
        f"""
        SELECT
            l.lead_id,
            l.warehouse_number,
            COALESCE(l.fiscal_year, %s) AS fiscal_year,
            COALESCE(l.fiscal_period, %s) AS fiscal_period,
            COALESCE(a.business_name, '') AS business_name,
            COALESCE(a.address_line_one, '') AS address_line_one,
            COALESCE(a.city, '') AS city,
            COALESCE(a.state, '') AS state,
            COALESCE(a.zip_code, '') AS zip_code
        FROM "{schema}"."lead" l
        JOIN "{schema}"."account" a ON a.account_id = l.account_id
        WHERE NOT EXISTS (
            SELECT 1 FROM "{schema}"."leads_embeddings" e WHERE e.lead_id = l.lead_id
        )
          {warehouse_clause}
        ORDER BY l.lead_id;
        """,
        params,
    )
    return cursor.fetchall()


def pos_source_rows(cursor):
    schema = schema_name()
    params = []
    warehouse_clause = warehouse_sql_filter("t", params)

    cursor.execute(
        f"""
        SELECT
            t.pos_id,
            t.account_number,
            t.warehouse_number,
            t.fiscal_year,
            t.fiscal_period,
            t.week,
            COALESCE(t.business_name, '') AS business_name,
            COALESCE(t.address_line_one, '') AS address_line_one,
            COALESCE(t.city, '') AS city,
            COALESCE(t.state, '') AS state,
            COALESCE(t.zip_code, '') AS zip_code
        FROM "{schema}"."transaction" t
        WHERE NOT EXISTS (
            SELECT 1 FROM "{schema}"."pos_embeddings" e WHERE e.pos_id = t.pos_id
        )
          {warehouse_clause}
        ORDER BY t.pos_id;
        """,
        params,
    )
    return cursor.fetchall()


def generate_lead_embeddings():
    job_started = time.monotonic()
    client = vertex_client()
    conn = connect()
    cursor = conn.cursor()
    rows = lead_source_rows(cursor)
    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"Lead rows needing embeddings: {len(rows)}")
    print(f"Embedding batch size: {DEFAULT_BATCH_SIZE}; max workers: {DEFAULT_MAX_WORKERS}")

    inserted = 0
    now = datetime.now(UTC)
    total_batches = (len(rows) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE
    for batch_number, batch in enumerate(chunks(rows, DEFAULT_BATCH_SIZE), start=1):
        batch_started = time.monotonic()
        records = [
            {
                "business_name": row[4],
                "address_line_one": row[5],
                "city": row[6],
                "state": row[7],
                "zip_code": row[8],
            }
            for row in batch
        ]
        combined_texts = [build_embedding_text(record, "combined_field") for record in records]
        address_texts = [build_embedding_text(record, "full_address") for record in records]
        name_texts = [build_embedding_text(record, "business_name") for record in records]
        vectors = embed_field_batches(
            client,
            {
                f"lead_batch_{batch_number}_combined": combined_texts,
                f"lead_batch_{batch_number}_address": address_texts,
                f"lead_batch_{batch_number}_name": name_texts,
            },
        )
        combined_vectors = vectors[f"lead_batch_{batch_number}_combined"]
        address_vectors = vectors[f"lead_batch_{batch_number}_address"]
        name_vectors = vectors[f"lead_batch_{batch_number}_name"]
        insert_rows = []
        for row, record, combined, address, name in zip(batch, records, combined_vectors, address_vectors, name_vectors):
            insert_rows.append((
                row[0],
                build_embedding_text(record, "combined_field"),
                build_embedding_text(record, "business_name"),
                build_embedding_text(record, "full_address"),
                combined,
                address,
                name,
                now,
                row[1],
                row[2],
                row[3],
            ))
        schema = schema_name()
        execute_many_values(
            cursor,
            f"""
            INSERT INTO "{schema}"."leads_embeddings" (
                lead_id, combined_field, business_name, business_address,
                combined_embedding, address_embedding, name_embedding, updated_date,
                warehouse_number, fiscal_year, fiscal_period
            )
            """,
            insert_rows,
            11,
        )
        conn.commit()
        inserted += len(insert_rows)
        batch_duration = time.monotonic() - batch_started
        if batch_number == 1 or batch_number % 10 == 0 or batch_number == total_batches:
            print(
                f"Inserted lead embedding batches: {batch_number}/{total_batches}; "
                f"rows={inserted}; batch_duration_seconds={batch_duration:.2f}"
            )
    conn.close()
    print(
        f"Inserted lead embeddings: {inserted}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def generate_pos_embeddings():
    job_started = time.monotonic()
    client = vertex_client()
    conn = connect()
    cursor = conn.cursor()
    rows = pos_source_rows(cursor)
    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"POS rows needing embeddings: {len(rows)}")
    print(f"Embedding batch size: {DEFAULT_BATCH_SIZE}; max workers: {DEFAULT_MAX_WORKERS}")

    inserted = 0
    now = datetime.now(UTC)
    total_batches = (len(rows) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE
    for batch_number, batch in enumerate(chunks(rows, DEFAULT_BATCH_SIZE), start=1):
        batch_started = time.monotonic()
        records = [
            {
                "business_name": row[6],
                "address_line_one": row[7],
                "city": row[8],
                "state": row[9],
                "zip_code": row[10],
            }
            for row in batch
        ]
        combined_texts = [build_embedding_text(record, "combined_field") for record in records]
        address_texts = [build_embedding_text(record, "full_address") for record in records]
        name_texts = [build_embedding_text(record, "business_name") for record in records]
        vectors = embed_field_batches(
            client,
            {
                f"pos_batch_{batch_number}_combined": combined_texts,
                f"pos_batch_{batch_number}_address": address_texts,
                f"pos_batch_{batch_number}_name": name_texts,
            },
        )
        combined_vectors = vectors[f"pos_batch_{batch_number}_combined"]
        address_vectors = vectors[f"pos_batch_{batch_number}_address"]
        name_vectors = vectors[f"pos_batch_{batch_number}_name"]
        insert_rows = []
        for row, record, combined, address, name in zip(batch, records, combined_vectors, address_vectors, name_vectors):
            insert_rows.append((
                row[0],
                row[1],
                build_embedding_text(record, "combined_field"),
                build_embedding_text(record, "business_name"),
                build_embedding_text(record, "full_address"),
                combined,
                address,
                name,
                now,
                row[2],
                row[3],
                row[4],
                row[5],
            ))
        schema = schema_name()
        execute_many_values(
            cursor,
            f"""
            INSERT INTO "{schema}"."pos_embeddings" (
                pos_id, account_number, combined_field, business_name, business_address,
                combined_embedding, address_embedding, name_embedding, load_date,
                warehouse_number, fiscal_year, fiscal_period, week
            )
            """,
            insert_rows,
            13,
        )
        conn.commit()
        inserted += len(insert_rows)
        batch_duration = time.monotonic() - batch_started
        if batch_number == 1 or batch_number % 10 == 0 or batch_number == total_batches:
            print(
                f"Inserted POS embedding batches: {batch_number}/{total_batches}; "
                f"rows={inserted}; batch_duration_seconds={batch_duration:.2f}"
            )
    conn.close()
    print(
        f"Inserted POS embeddings: {inserted}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def run_fuzzy_match():
    job_started = time.monotonic()
    conn = connect()
    cursor = conn.cursor()
    schema = schema_name()
    run_id = os.environ.get("MATCH_RUN_ID") or f"workflow-{uuid.uuid4().hex[:12]}"
    limit = int(os.environ.get("MATCH_LEAD_LIMIT", "1000000"))
    rules = BUSINESS_RULES
    recall_gate = float(rules["candidate_retrieval"]["recall_gate_min_similarity"])
    qualify_min = float(rules["confidence_bands"]["qualify_min_score"])
    ambiguity_delta = float(rules["resolution"]["ambiguity_delta"])
    nearest_neighbor_limit = int(rules["candidate_retrieval"]["nearest_neighbor_limit"])

    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"Match run: {run_id}")
    print(
        f"Match lead limit: {limit}; match_batch_size: {MATCH_BATCH_SIZE}; "
        f"nearest_neighbor_limit: {nearest_neighbor_limit}; "
        f"statement_timeout_ms: {MATCH_STATEMENT_TIMEOUT_MS}"
    )

    processed_leads = 0
    inserted = 0
    last_lead_id = None
    batch_number = 0

    while processed_leads < limit:
        fetch_params = []
        warehouse_clause = warehouse_sql_filter("leads_embeddings", fetch_params).replace("leads_embeddings.", "")
        lead_cursor_clause = ""
        if last_lead_id is not None:
            lead_cursor_clause = "AND lead_id > %s"
            fetch_params.append(last_lead_id)
        fetch_limit = min(MATCH_BATCH_SIZE, limit - processed_leads)
        fetch_params.append(fetch_limit)
        cursor.execute(
            f"""
            SELECT lead_id
            FROM "{schema}"."leads_embeddings"
            WHERE combined_embedding IS NOT NULL
              {warehouse_clause}
              {lead_cursor_clause}
            ORDER BY lead_id
            LIMIT %s
            """,
            fetch_params,
        )
        lead_ids = [row[0] for row in cursor.fetchall()]
        if not lead_ids:
            break

        batch_number += 1
        last_lead_id = lead_ids[-1]
        processed_leads += len(lead_ids)
        lead_placeholders = ", ".join(["%s"] * len(lead_ids))
        params = [
            *lead_ids,
            recall_gate,
            nearest_neighbor_limit,
            qualify_min,
            run_id,
            ambiguity_delta,
            EMBEDDING_MODEL,
        ]
        query_started = time.monotonic()
        if MATCH_STATEMENT_TIMEOUT_MS > 0:
            cursor.execute("SET LOCAL statement_timeout = %s", [MATCH_STATEMENT_TIMEOUT_MS])
        cursor.execute(
            f"""
            WITH lead_batch AS (
                SELECT *
                FROM "{schema}"."leads_embeddings"
                WHERE combined_embedding IS NOT NULL
                  AND lead_id IN ({lead_placeholders})
            ),
            candidates AS (
                SELECT
                    l.lead_id,
                    s.pos_id,
                    l.warehouse_number,
                    l.fiscal_year AS lead_fiscal_year,
                    l.fiscal_period AS lead_fiscal_period,
                    s.fiscal_year AS pos_fiscal_year,
                    s.fiscal_period AS pos_fiscal_period,
                    s.week AS pos_week,
                    (1 - (s.combined_embedding <=> l.combined_embedding)) * 100 AS combined_field_score,
                    (1 - (s.address_embedding <=> l.address_embedding)) * 100 AS full_address_score,
                    (1 - COALESCE(NULLIF(s.name_embedding <=> l.name_embedding, 'NaN'::float), 1)) * 100 AS business_name_score
                FROM lead_batch l
                CROSS JOIN LATERAL (
                    SELECT *
                    FROM "{schema}"."pos_embeddings" s
                    WHERE s.combined_embedding IS NOT NULL
                      AND s.warehouse_number = l.warehouse_number
                      AND (1 - (s.combined_embedding <=> l.combined_embedding)) * 100 >= %s
                    ORDER BY s.combined_embedding <=> l.combined_embedding
                    LIMIT %s
                ) s
            ),
            scored AS (
                SELECT
                    lead_id,
                    pos_id,
                    warehouse_number,
                    pos_fiscal_year,
                    pos_fiscal_period,
                    pos_week,
                    combined_field_score,
                    full_address_score,
                    business_name_score,
                    (
                        4 * full_address_score
                        + 3 * business_name_score
                    ) / 7 AS final_score,
                    CASE
                        WHEN (
                            pos_fiscal_year > lead_fiscal_year
                            OR (pos_fiscal_year = lead_fiscal_year AND pos_fiscal_period >= lead_fiscal_period)
                        )
                        THEN 'Closed - Match'
                        ELSE 'Closed - Existing'
                    END AS lifecycle_state
                FROM candidates
            ),
            ranked AS (
                SELECT *
                FROM scored
                WHERE final_score >= %s
            ),
            best_unique_pos AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY pos_id
                           ORDER BY final_score DESC, lead_id
                       ) AS pos_rank,
                       LEAD(final_score) OVER (
                           PARTITION BY pos_id
                           ORDER BY final_score DESC, lead_id
                       ) AS next_pos_score
                FROM ranked
            )
            INSERT INTO "{schema}"."match_decision_detail" (
                match_run_id, lead_id, pos_id, warehouse_number, match_type, final_score,
                combined_field_score, full_address_score, business_name_score,
                weight_formula, embedding_model, created_date
            )
            SELECT
                %s,
                lead_id,
                pos_id,
                warehouse_number,
                CASE
                    WHEN next_pos_score IS NOT NULL
                     AND final_score - next_pos_score <= %s
                    THEN 'Manual Review'
                    ELSE 'Fuzzy'
                END,
                final_score,
                combined_field_score,
                full_address_score,
                business_name_score,
                '(4*address + 3*name)/7',
                %s,
                CURRENT_TIMESTAMP
            FROM best_unique_pos
            WHERE pos_rank = 1
            ON CONFLICT (match_run_id, lead_id, pos_id) DO NOTHING;
            """,
            params,
        )
        batch_inserted = cursor.rowcount
        inserted += batch_inserted
        conn.commit()
        query_duration = time.monotonic() - query_started
        print(
            f"Fuzzy match batch {batch_number}: leads={len(lead_ids)}; "
            f"processed_leads={processed_leads}; inserted_rows={batch_inserted}; "
            f"batch_duration_seconds={query_duration:.2f}"
        )

    conn.commit()
    conn.close()
    print(
        f"Inserted match decision rows: {inserted}; "
        f"processed_leads={processed_leads}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def print_summary():
    conn = connect()
    cursor = conn.cursor()
    schema = schema_name()
    for table in (
        "account",
        "lead",
        "contact",
        "pos_transactions",
        "transaction",
        "leads_embeddings",
        "pos_embeddings",
        "match_decision_detail",
    ):
        cursor.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
        print(f"{schema}.{table}: {cursor.fetchone()[0]}")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=("summary", "smoke", "lead-embeddings", "pos-embeddings", "fuzzy-match", "report"),
    )
    args, remaining = parser.parse_known_args()

    if args.task == "summary":
        print_summary()
    elif args.task == "smoke":
        from lead_match_runtime.smoke_test import main as smoke_main
        sys.argv = [sys.argv[0]] + remaining
        smoke_main()
    elif args.task == "lead-embeddings":
        generate_lead_embeddings()
    elif args.task == "pos-embeddings":
        generate_pos_embeddings()
    elif args.task == "fuzzy-match":
        run_fuzzy_match()
    elif args.task == "report":
        from lead_match_runtime.report import run_report
        run_report()
    else:
        raise AssertionError(args.task)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
