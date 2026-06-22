import argparse
import json
import os
import random
import re
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
EMBEDDING_BATCH_WORKERS = max(1, int(os.environ.get("EMBEDDING_BATCH_WORKERS", "3")))
EMBEDDING_MAX_RETRIES = max(1, int(os.environ.get("EMBEDDING_MAX_RETRIES", "6")))
EMBEDDING_RETRY_BASE_DELAY = float(os.environ.get("EMBEDDING_RETRY_BASE_DELAY", "1.5"))
EMBEDDING_RETRY_MAX_DELAY = float(os.environ.get("EMBEDDING_RETRY_MAX_DELAY", "90"))
EMBEDDING_MAX_TEXTS_PER_REQUEST = int(os.environ.get("EMBEDDING_MAX_TEXTS_PER_REQUEST", "0"))
EMBEDDING_REQUEST_LOG_EVERY = max(0, int(os.environ.get("EMBEDDING_REQUEST_LOG_EVERY", "100")))
MATCH_BATCH_SIZE = max(1, int(os.environ.get("MATCH_BATCH_SIZE", "100")))
MATCH_STATEMENT_TIMEOUT_MS = int(os.environ.get("MATCH_STATEMENT_TIMEOUT_MS", "900000"))
HNSW_EF_SEARCH = max(0, int(os.environ.get("HNSW_EF_SEARCH", "100")))

EXPECTED_PROJECT = os.environ.get("EXPECTED_PROJECT_ID", BUSINESS_RULES["environment"]["project_id"])
EXPECTED_CLOUDSQL_CONNECTION_NAME = os.environ.get(
    "EXPECTED_CLOUDSQL_CONNECTION_NAME",
    "ctoteam:us-central1:lead-mgmt-db",
)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    if conn and conn != EXPECTED_CLOUDSQL_CONNECTION_NAME:
        raise RuntimeError(
            f"Refusing to start: CLOUDSQL_CONNECTION_NAME={conn!r}, "
            f"expected {EXPECTED_CLOUDSQL_CONNECTION_NAME!r}"
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


def configure_hnsw_search(conn, cursor):
    if HNSW_EF_SEARCH <= 0:
        print("HNSW ef_search tuning disabled")
        return
    try:
        cursor.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")
        conn.commit()
        print(f"HNSW ef_search set to {HNSW_EF_SEARCH}")
    except Exception as exc:
        conn.rollback()
        print(
            f"Warning: failed to set hnsw.ef_search={HNSW_EF_SEARCH}: {exc}",
            file=sys.stderr,
        )


def schema_name():
    return get_schema(BUSINESS_RULES)


def quote_ident(identifier):
    if not IDENT_RE.match(identifier):
        raise RuntimeError(f"Unsafe SQL identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def qualified_name(schema, name):
    return f"{quote_ident(schema)}.{quote_ident(name)}"


def warehouse_scope():
    return get_warehouse_scope(BUSINESS_RULES)


def warehouse_sql_filter(alias, params):
    scope = warehouse_scope()
    if scope.is_all:
        return ""
    placeholders = ", ".join(["%s"] * len(scope.values))
    params.extend(scope.values)
    return f"AND {alias}.warehouse_number IN ({placeholders})"


def exact_match_types():
    raw_types = os.environ.get("EXACT_MATCH_TYPES", "Exact,Deterministic")
    return tuple(
        value.strip().lower()
        for value in raw_types.split(",")
        if value.strip()
    )


def _exact_type_placeholders(types):
    return ", ".join(["%s"] * len(types))


def _append_exact_guard_params(params, run_id, types):
    params.extend([run_id, *types, *types])


def exact_lead_exclusion_clause(schema, lead_expr, params, run_id):
    types = exact_match_types()
    if not types:
        return ""
    placeholders = _exact_type_placeholders(types)
    _append_exact_guard_params(params, run_id, types)
    return f"""
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."match_decision_detail" exact_m
          WHERE exact_m.match_run_id = %s
            AND exact_m.lead_id = {lead_expr}
            AND lower(exact_m.match_type) IN ({placeholders})
      )
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."transaction" exact_t
          WHERE exact_t.lead_id = {lead_expr}
            AND lower(exact_t.match_type) IN ({placeholders})
      )
    """


def exact_pos_exclusion_clause(schema, pos_expr, params, run_id):
    types = exact_match_types()
    if not types:
        return ""
    placeholders = _exact_type_placeholders(types)
    _append_exact_guard_params(params, run_id, types)
    return f"""
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."match_decision_detail" exact_m
          WHERE exact_m.match_run_id = %s
            AND exact_m.pos_id = {pos_expr}
            AND lower(exact_m.match_type) IN ({placeholders})
      )
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."transaction" exact_t
          WHERE exact_t.pos_id = {pos_expr}
            AND lower(exact_t.match_type) IN ({placeholders})
      )
    """


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


def embedding_request_size():
    if EMBEDDING_MAX_TEXTS_PER_REQUEST > 0:
        return EMBEDDING_MAX_TEXTS_PER_REQUEST
    return min(DEFAULT_BATCH_SIZE, 250)


def embed_text_request(client, texts, label, log_success=True):
    started = time.monotonic()
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY",
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            duration = time.monotonic() - started
            if log_success:
                print(
                    f"Embedded {label}: rows={len(texts)} attempt={attempt} "
                    f"duration_seconds={duration:.2f}"
                )
            embeddings = response.embeddings or []
            if len(embeddings) != len(texts):
                raise RuntimeError(
                    f"API returned {len(embeddings)} embeddings for {len(texts)} texts ({label})"
                )
            return [vector_literal(embedding.values) for embedding in embeddings]
        except Exception as exc:
            if attempt >= EMBEDDING_MAX_RETRIES or not is_retryable_embedding_error(exc):
                print(
                    f"Embedding failed for {label}: rows={len(texts)} "
                    f"attempt={attempt} error={exc}",
                    file=sys.stderr,
                )
                raise
            delay = min(
                EMBEDDING_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1.5),
                EMBEDDING_RETRY_MAX_DELAY,
            )
            print(
                f"Retrying {label}: rows={len(texts)} attempt={attempt} "
                f"delay_seconds={delay:.2f} error={exc}"
            )
            time.sleep(delay)

    raise RuntimeError(f"Embedding failed for {label}")


def embed_texts(client, texts, label="embedding"):
    normalized = [(text or "").strip() for text in texts]
    results = [None] * len(normalized)
    pending = [(idx, text) for idx, text in enumerate(normalized) if text]
    if not pending:
        return results

    request_size = embedding_request_size()
    total_requests = (len(pending) + request_size - 1) // request_size
    for request_number, request_items in enumerate(chunks(pending, request_size), start=1):
        request_texts = [text for _, text in request_items]
        log_success = (
            request_size > 1
            or request_number == 1
            or request_number == total_requests
            or (
                EMBEDDING_REQUEST_LOG_EVERY > 0
                and request_number % EMBEDDING_REQUEST_LOG_EVERY == 0
            )
        )
        request_vectors = embed_text_request(
            client,
            request_texts,
            f"{label}_request_{request_number}",
            log_success=log_success,
        )
        for (idx, _), vector in zip(request_items, request_vectors):
            results[idx] = vector
    return results


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


def execute_many_values(
    cursor,
    insert_prefix,
    rows,
    fields_per_row,
    chunk_size=250,
    conflict_clause="",
):
    if not rows:
        return
    row_placeholder = "(" + ", ".join(["%s"] * fields_per_row) + ")"
    for chunk in chunks(rows, chunk_size):
        placeholders = ", ".join([row_placeholder] * len(chunk))
        params = [value for row in chunk for value in row]
        cursor.execute(f"{insert_prefix} VALUES {placeholders} {conflict_clause}", params)


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


def build_lead_embedding_insert_rows(client, batch_number, batch, now):
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
    for row, record, combined, address, name in zip(
        batch, records, combined_vectors, address_vectors, name_vectors
    ):
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
    return batch_number, insert_rows, time.monotonic() - batch_started


def build_pos_embedding_insert_rows(client, batch_number, batch, now):
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
    for row, record, combined, address, name in zip(
        batch, records, combined_vectors, address_vectors, name_vectors
    ):
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
    return batch_number, insert_rows, time.monotonic() - batch_started


def process_embedding_batches(rows, build_insert_rows, insert_insert_rows, conn, label):
    total_batches = (len(rows) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE
    batches = list(enumerate(chunks(rows, DEFAULT_BATCH_SIZE), start=1))
    inserted = 0
    completed = 0

    def write_result(batch_number, insert_rows, batch_duration):
        nonlocal inserted, completed
        insert_insert_rows(insert_rows)
        conn.commit()
        inserted += len(insert_rows)
        completed += 1
        if completed == 1 or completed % 10 == 0 or completed == total_batches:
            print(
                f"Inserted {label} embedding batches: {completed}/{total_batches}; "
                f"last_batch={batch_number}; rows={inserted}; "
                f"batch_duration_seconds={batch_duration:.2f}"
            )

    if EMBEDDING_BATCH_WORKERS == 1 or len(batches) <= 1:
        for batch_number, batch in batches:
            write_result(*build_insert_rows(batch_number, batch))
        return inserted

    max_workers = min(EMBEDDING_BATCH_WORKERS, len(batches))
    print(f"Embedding batch-level workers enabled: {max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(build_insert_rows, batch_number, batch)
            for batch_number, batch in batches
        ]
        for future in as_completed(futures):
            write_result(*future.result())
    return inserted


def generate_lead_embeddings():
    job_started = time.monotonic()
    client = vertex_client()
    conn = connect()
    try:
        _generate_lead_embeddings(conn, job_started, client)
    finally:
        conn.close()


def _generate_lead_embeddings(conn, job_started, client):
    cursor = conn.cursor()
    rows = lead_source_rows(cursor)
    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"Lead rows needing embeddings: {len(rows)}")
    print(
        f"Embedding batch size: {DEFAULT_BATCH_SIZE}; field workers: {DEFAULT_MAX_WORKERS}; "
        f"batch workers: {EMBEDDING_BATCH_WORKERS}; request size: {embedding_request_size()}; "
        f"request_log_every: {EMBEDDING_REQUEST_LOG_EVERY}"
    )

    now = datetime.now(UTC)
    schema = schema_name()

    def insert_lead_rows(insert_rows):
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
            conflict_clause="ON CONFLICT (lead_id) DO NOTHING",
        )

    inserted = process_embedding_batches(
        rows,
        lambda batch_number, batch: build_lead_embedding_insert_rows(
            client, batch_number, batch, now
        ),
        insert_lead_rows,
        conn,
        "lead",
    )
    print(
        f"Inserted lead embeddings: {inserted}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def generate_pos_embeddings():
    job_started = time.monotonic()
    client = vertex_client()
    conn = connect()
    try:
        _generate_pos_embeddings(conn, job_started, client)
    finally:
        conn.close()


def _generate_pos_embeddings(conn, job_started, client):
    cursor = conn.cursor()
    rows = pos_source_rows(cursor)
    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"POS rows needing embeddings: {len(rows)}")
    print(
        f"Embedding batch size: {DEFAULT_BATCH_SIZE}; field workers: {DEFAULT_MAX_WORKERS}; "
        f"batch workers: {EMBEDDING_BATCH_WORKERS}; request size: {embedding_request_size()}; "
        f"request_log_every: {EMBEDDING_REQUEST_LOG_EVERY}"
    )

    now = datetime.now(UTC)
    schema = schema_name()

    def insert_pos_rows(insert_rows):
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
            conflict_clause="ON CONFLICT (pos_id) DO NOTHING",
        )

    inserted = process_embedding_batches(
        rows,
        lambda batch_number, batch: build_pos_embedding_insert_rows(
            client, batch_number, batch, now
        ),
        insert_pos_rows,
        conn,
        "POS",
    )
    print(
        f"Inserted POS embeddings: {inserted}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def run_fuzzy_match():
    job_started = time.monotonic()
    conn = connect()
    try:
        _run_fuzzy_match(conn, job_started)
    finally:
        conn.close()


def _run_fuzzy_match(conn, job_started):
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
        f"statement_timeout_ms: {MATCH_STATEMENT_TIMEOUT_MS}; "
        f"hnsw_ef_search: {HNSW_EF_SEARCH}"
    )
    configure_hnsw_search(conn, cursor)
    if MATCH_STATEMENT_TIMEOUT_MS > 0:
        cursor.execute(f"SET statement_timeout = {MATCH_STATEMENT_TIMEOUT_MS}")
        conn.commit()
        print(f"statement_timeout set to {MATCH_STATEMENT_TIMEOUT_MS}ms")

    processed_leads = 0
    inserted = 0
    last_lead_id = None
    batch_number = 0

    while processed_leads < limit:
        fetch_params = []
        warehouse_clause = warehouse_sql_filter("leads_embeddings", fetch_params).replace("leads_embeddings.", "")
        exact_lead_clause = exact_lead_exclusion_clause(
            schema,
            "leads_embeddings.lead_id",
            fetch_params,
            run_id,
        )
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
              {exact_lead_clause}
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
        params = [*lead_ids]
        exact_pos_clause = exact_pos_exclusion_clause(
            schema,
            "s.pos_id",
            params,
            run_id,
        )
        params.extend([
            recall_gate,
            nearest_neighbor_limit,
            qualify_min,
            run_id,
            ambiguity_delta,
            EMBEDDING_MODEL,
        ])
        query_started = time.monotonic()
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
                      {exact_pos_clause}
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
    if processed_leads == 0:
        print(
            "Warning: fuzzy match processed 0 leads after warehouse and exact-match filters",
            file=sys.stderr,
        )
    print(
        f"Inserted match decision rows: {inserted}; "
        f"processed_leads={processed_leads}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )


def ensure_indexes():
    job_started = time.monotonic()
    conn = connect()
    try:
        _ensure_indexes(conn, job_started)
    finally:
        conn.close()


def _ensure_indexes(conn, job_started):
    conn.autocommit = True
    cursor = conn.cursor()
    schema = schema_name()
    dimension = EMBEDDING_DIMENSION
    print(f"Schema: {schema}")

    def execute(label, sql, params=None):
        print(f"start: {label}")
        started = time.monotonic()
        cursor.execute(sql, params or ())
        duration = time.monotonic() - started
        print(f"done: {label}; seconds={duration:.2f}")

    def fetchall(sql, params=None):
        cursor.execute(sql, params or ())
        return cursor.fetchall()

    execute("create pgvector extension", "CREATE EXTENSION IF NOT EXISTS vector")

    required_tables = ("leads_embeddings", "pos_embeddings")
    vector_columns = ("combined_embedding", "address_embedding", "name_embedding")
    missing_tables = [
        table
        for table in required_tables
        if not fetchall(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
    ]
    if missing_tables:
        raise RuntimeError(f"Missing required embedding tables: {missing_tables}")

    for table in required_tables:
        for column in vector_columns:
            bad_dims = [
                row
                for row in fetchall(
                    f"""
                    SELECT vector_dims({quote_ident(column)}), count(*)
                    FROM {qualified_name(schema, table)}
                    WHERE {quote_ident(column)} IS NOT NULL
                    GROUP BY vector_dims({quote_ident(column)})
                    """,
                )
                if int(row[0]) != dimension
            ]
            if bad_dims:
                raise RuntimeError(
                    f"{schema}.{table}.{column} has non-{dimension} vectors: {bad_dims}"
                )

    invalid_indexes = fetchall(
        """
        SELECT c.relname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname IN (
            'idx_leads_embeddings_combined_hnsw',
            'idx_pos_embeddings_combined_hnsw',
            'idx_leads_embeddings_lead_id_unique',
            'idx_pos_embeddings_pos_id_unique'
          )
          AND (NOT i.indisvalid OR NOT i.indisready)
        """,
        (schema,),
    )
    for (index_name,) in invalid_indexes:
        execute(
            f"drop invalid index {index_name}",
            f"DROP INDEX CONCURRENTLY IF EXISTS {qualified_name(schema, index_name)}",
        )

    for table in required_tables:
        current_types = {
            row[0]: row[1]
            for row in fetchall(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s
                  AND a.attname IN ('combined_embedding', 'address_embedding', 'name_embedding')
                """,
                (schema, table),
            )
        }
        if any(current_types.get(column) != f"vector({dimension})" for column in vector_columns):
            execute(
                f"dimension {table} vector columns",
                f"""
                ALTER TABLE {qualified_name(schema, table)}
                    ALTER COLUMN combined_embedding TYPE vector({dimension}) USING combined_embedding::vector({dimension}),
                    ALTER COLUMN address_embedding TYPE vector({dimension}) USING address_embedding::vector({dimension}),
                    ALTER COLUMN name_embedding TYPE vector({dimension}) USING name_embedding::vector({dimension})
                """,
            )

    duplicate_checks = (
        ("leads_embeddings", "lead_id"),
        ("pos_embeddings", "pos_id"),
    )
    for table, key_column in duplicate_checks:
        duplicates = fetchall(
            f"""
            SELECT {quote_ident(key_column)}, count(*)
            FROM {qualified_name(schema, table)}
            WHERE {quote_ident(key_column)} IS NOT NULL
            GROUP BY {quote_ident(key_column)}
            HAVING count(*) > 1
            LIMIT 10
            """,
        )
        if duplicates:
            raise RuntimeError(
                f"Cannot create unique index on {schema}.{table}.{key_column}; "
                f"duplicates={duplicates}"
            )

    execute(
        "create lead embedding unique index",
        f"""
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_lead_id_unique
        ON {qualified_name(schema, "leads_embeddings")} (lead_id)
        """,
    )
    execute(
        "create POS embedding unique index",
        f"""
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_pos_id_unique
        ON {qualified_name(schema, "pos_embeddings")} (pos_id)
        """,
    )
    execute(
        "create lead combined HNSW index",
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_embeddings_combined_hnsw
        ON {qualified_name(schema, "leads_embeddings")}
        USING hnsw (combined_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 128)
        """,
    )
    execute(
        "create POS combined HNSW index",
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_combined_hnsw
        ON {qualified_name(schema, "pos_embeddings")}
        USING hnsw (combined_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 128)
        """,
    )

    for table in required_tables:
        execute(f"analyze {table}", f"ANALYZE {qualified_name(schema, table)}")

    index_states = [
        {
            "index_name": row[0],
            "unique": bool(row[1]),
            "valid": bool(row[2]),
            "ready": bool(row[3]),
        }
        for row in fetchall(
            """
            SELECT c.relname, i.indisunique, i.indisvalid, i.indisready
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname IN (
                'idx_leads_embeddings_combined_hnsw',
                'idx_pos_embeddings_combined_hnsw',
                'idx_leads_embeddings_lead_id_unique',
                'idx_pos_embeddings_pos_id_unique'
              )
            ORDER BY c.relname
            """,
            (schema,),
        )
    ]
    if len(index_states) != 4 or not all(row["valid"] and row["ready"] for row in index_states):
        raise RuntimeError(f"Index verification failed: {index_states}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "schema": schema,
                "embedding_dimension": dimension,
                "index_states": index_states,
                "duration_seconds": round(time.monotonic() - job_started, 2),
            },
            indent=2,
        )
    )


def print_summary():
    conn = connect()
    try:
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
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=("summary", "smoke", "lead-embeddings", "pos-embeddings", "fuzzy-match", "report", "ensure-indexes"),
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
    elif args.task == "ensure-indexes":
        ensure_indexes()
    else:
        raise AssertionError(args.task)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
