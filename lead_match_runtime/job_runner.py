import argparse
import json
import logging
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
    build_pos_variant_texts,
    closed_existing_lifecycle_state_from_fiscal_rules,
    confidence_subtier,
    exact_authoritative_score,
    exact_lifecycle_state,
    exact_match_type,
    exact_match_types as configured_exact_match_types,
    exact_score,
    fiscal_ce_period_window,
    fiscal_periods_per_year,
    fuzzy_artifact_score,
    fuzzy_lifecycle_state,
    fuzzy_lifecycle_state_label,
    fuzzy_match_type,
    fuzzy_max_score,
    fuzzy_qualify_min_score,
    fuzzy_score_bands,
    manual_review_match_type,
    matching_set_address_fields,
    matching_set_by_id,
    matching_set_definitions,
    matching_set_email_fields,
    matching_set_name_fields,
    matching_set_phone_fields,
    fuzzy_boost_rule,
    pos_embedding_variant_field_aliases,
    pos_transaction_field_aliases,
    matching_sets as configured_matching_sets,
    no_match_lifecycle_state,
    normalize_email,
    normalize_phone,
    get_project_id,
    get_schema,
    get_warehouse_scope,
    load_business_rules,
    precision_score_formula,
    semantic_precision_weights,
)


logger = logging.getLogger(__name__)
BUSINESS_RULES = load_business_rules()
EMBEDDING_MODEL = BUSINESS_RULES["embeddings"]["model"]
EMBEDDING_DIMENSION = int(BUSINESS_RULES["embeddings"]["output_dimensionality"])
EMBEDDING_TASK_TYPE = BUSINESS_RULES["embeddings"].get("task_type", "SEMANTIC_SIMILARITY")
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
HNSW_M = max(2, int(os.environ.get("HNSW_M", "32")))
HNSW_EF_CONSTRUCTION = max(2 * HNSW_M, int(os.environ.get("HNSW_EF_CONSTRUCTION", "128")))
HNSW_MAINTENANCE_WORK_MEM = os.environ.get("HNSW_MAINTENANCE_WORK_MEM", "512MB")
EXPLAIN_FUZZY_PLAN = os.environ.get("EXPLAIN_FUZZY_PLAN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "y"}
DRY_RUN_MATCH_ROW_LIMIT = min(
    10,
    max(1, int(os.environ.get("DRY_RUN_MATCH_ROW_LIMIT", "10"))),
)
DRY_RUN_WRITEBACK_BUSINESS_TABLES = (
    os.environ.get("DRY_RUN_WRITEBACK_BUSINESS_TABLES", "false").strip().lower()
    in {"1", "true", "yes", "y"}
)


def optional_positive_int_env(name):
    raw = os.environ.get(name, "").strip()
    if not raw or raw.lower() == "all":
        return None
    value = int(raw)
    if value < 1:
        raise RuntimeError(f"{name} must be blank, 'all', or a positive integer")
    return value


LEAD_EMBEDDING_LIMIT = optional_positive_int_env("LEAD_EMBEDDING_LIMIT")
POS_EMBEDDING_LIMIT = optional_positive_int_env("POS_EMBEDDING_LIMIT")

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


def configure_hnsw_search(conn, cursor, local=False):
    if HNSW_EF_SEARCH <= 0:
        print("HNSW ef_search tuning disabled")
        return
    try:
        scope = "LOCAL " if local else ""
        cursor.execute(f"SET {scope}hnsw.ef_search = {HNSW_EF_SEARCH}")
        print(f"HNSW ef_search set {'locally ' if local else ''}to {HNSW_EF_SEARCH}")
    except Exception as exc:
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


def warehouse_sql_filter(alias):
    scope = warehouse_scope()
    if scope.is_all:
        return "", []
    placeholders = ", ".join(["%s"] * len(scope.values))
    return f"AND {alias}.warehouse_number IN ({placeholders})", list(scope.values)


def exact_match_types():
    configured_types = ",".join(configured_exact_match_types(BUSINESS_RULES, lower=False))
    raw_types = os.environ.get("EXACT_MATCH_TYPES", configured_types)
    return tuple(
        value.strip().lower()
        for value in raw_types.split(",")
        if value.strip()
    )


def exact_qualified_min_score():
    configured = exact_authoritative_score(BUSINESS_RULES)
    return float(os.environ.get("EXACT_MATCH_MIN_SCORE", configured))


def _fuzzy_lifecycle_case(score_expr, params):
    lifecycle = fuzzy_lifecycle_state_label(BUSINESS_RULES)
    floor = fuzzy_qualify_min_score(BUSINESS_RULES)
    no_match = no_match_lifecycle_state(BUSINESS_RULES)
    params.extend([floor, lifecycle, no_match])
    return f"CASE WHEN {score_expr} >= %s THEN %s ELSE %s END"


def _exact_type_placeholders(types):
    return ", ".join(["%s"] * len(types))


def _append_exact_guard_params(params, types):
    min_score = exact_qualified_min_score()
    params.extend([*types, min_score, *types, min_score])


def exact_lead_exclusion_clause(schema, lead_expr, params):
    types = exact_match_types()
    if not types:
        return ""
    placeholders = _exact_type_placeholders(types)
    _append_exact_guard_params(params, types)
    return f"""
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."match_decision_detail" exact_m
          WHERE exact_m.lead_id = {lead_expr}
            AND lower(exact_m.match_type) IN ({placeholders})
            AND (exact_m.final_score IS NULL OR exact_m.final_score >= %s)
      )
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."transaction" exact_t
          WHERE exact_t.lead_id = {lead_expr}
            AND lower(exact_t.match_type) IN ({placeholders})
            AND (exact_t.match_score IS NULL OR exact_t.match_score >= %s)
      )
    """


def exact_pos_exclusion_clause(schema, pos_expr, params):
    types = exact_match_types()
    if not types:
        return ""
    placeholders = _exact_type_placeholders(types)
    _append_exact_guard_params(params, types)
    return f"""
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."match_decision_detail" exact_m
          WHERE exact_m.pos_id = {pos_expr}
            AND lower(exact_m.match_type) IN ({placeholders})
            AND (exact_m.final_score IS NULL OR exact_m.final_score >= %s)
      )
      AND NOT EXISTS (
          SELECT 1
          FROM "{schema}"."transaction" exact_t
          WHERE exact_t.pos_id = {pos_expr}
            AND lower(exact_t.match_type) IN ({placeholders})
            AND (exact_t.match_score IS NULL OR exact_t.match_score >= %s)
      )
    """


REQUIRED_HNSW_COMBINED_INDEXES = {
    "leads_embeddings": "idx_leads_embeddings_combined_hnsw",
    "pos_embeddings": "idx_pos_embeddings_combined_hnsw",
}


def hnsw_combined_index_rows(cursor, schema):
    cursor.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s
          AND tablename IN ('leads_embeddings', 'pos_embeddings')
          AND lower(indexdef) LIKE '%%using hnsw%%'
          AND lower(indexdef) LIKE '%%combined_embedding%%'
          AND lower(indexdef) LIKE '%%vector_cosine_ops%%'
        """,
        (schema,),
    )
    return cursor.fetchall()


def verify_hnsw_combined_indexes(cursor, schema, *, fail_fast=True):
    rows = hnsw_combined_index_rows(cursor, schema)
    found_tables = {row[0] for row in rows}
    missing_tables = sorted(set(REQUIRED_HNSW_COMBINED_INDEXES) - found_tables)
    if missing_tables and fail_fast:
        missing = ", ".join(f"{table}.combined_embedding" for table in missing_tables)
        raise RuntimeError(
            f"HNSW index missing on {missing} - refusing to run fuzzy match, "
            "would trigger full scan"
        )
    return rows, missing_tables


def warehouse_scope_label():
    scope = warehouse_scope()
    return "ALL" if scope.is_all else ",".join(str(value) for value in scope.values)


def vertex_client():
    project = os.environ.get("VERTEX_PROJECT_ID") or get_project_id(BUSINESS_RULES)
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    if not project:
        raise RuntimeError("Missing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT")
    timeout = float(os.environ.get("VERTEX_TIMEOUT_SECONDS", "120.0"))
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(api_version="v1", timeout=timeout),
    )


def vector_literal(values):
    if values is None:
        return None
    arr = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if not norm:
        return None
    if np.isnan(norm) or not np.isfinite(norm):
        return None
    arr = arr / norm
    if not np.all(np.isfinite(arr)):
        return None
    if not np.any(arr):
        return None
    return "[" + ",".join(f"{float(value):.8f}" for value in arr.tolist()) + "]"


def is_retryable_embedding_error(exc):
    error = f"{exc.__class__.__module__}.{exc.__class__.__name__}: {exc}".lower()
    retryable_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "deadline",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "read operation timed out",
        "readtimeout",
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
                    task_type=EMBEDDING_TASK_TYPE,
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


def _build_row_variant_embeddings(texts_by_row: list[dict[str, str | None]], sources: list[str]):
    """Build per-source vectors for one semantic group (name/address) with per-row dedupe."""
    unique_texts = []
    row_source_index: list[dict[str, int | None]] = []
    for row in texts_by_row:
        row_mapping: dict[str, int | None] = {}
        seen = {}
        for source in sources:
            text = row.get(source)
            if not text:
                row_mapping[source] = None
                continue
            idx = seen.get(text)
            if idx is None:
                idx = len(unique_texts)
                unique_texts.append(text)
                seen[text] = idx
            row_mapping[source] = idx
        row_source_index.append(row_mapping)
    return unique_texts, row_source_index


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
    warehouse_clause, warehouse_params = warehouse_sql_filter("l")
    params.extend(warehouse_params)
    exact_lead_clause = exact_lead_exclusion_clause(schema, "l.lead_id", params)
    limit_clause = ""
    if LEAD_EMBEDDING_LIMIT is not None:
        limit_clause = "LIMIT %s"
        params.append(LEAD_EMBEDDING_LIMIT)

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
          {exact_lead_clause}
        ORDER BY l.lead_id
        {limit_clause};
        """,
        params,
    )
    return cursor.fetchall()


def pos_source_rows(cursor):
    schema = schema_name()
    params = []
    warehouse_clause, warehouse_params = warehouse_sql_filter("t")
    params.extend(warehouse_params)
    exact_pos_clause = exact_pos_exclusion_clause(schema, "t.pos_id", params)
    limit_clause = ""
    if POS_EMBEDDING_LIMIT is not None:
        limit_clause = "LIMIT %s"
        params.append(POS_EMBEDDING_LIMIT)

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
            COALESCE(t.zip_code, '') AS zip_code,
            COALESCE(t.oms_company, '') AS oms_company_name,
            COALESCE(t.oms_company_2, '') AS oms2_company_name,
            COALESCE(t.oms_address_line_1, '') AS oms_address_line_1,
            COALESCE(t.oms_city, '') AS oms_city,
            COALESCE(t.oms_state, '') AS oms_state,
            COALESCE(t.oms_zip, '') AS oms_zip,
            COALESCE(t.oms_address_line_1_v2, '') AS oms_address_line_1_v2,
            COALESCE(t.oms_city_2, '') AS oms_city_2,
            COALESCE(t.oms_state_2, '') AS oms_state_2,
            COALESCE(t.oms_zip_2, '') AS oms_zip_2
        FROM "{schema}"."transaction" t
        WHERE NOT EXISTS (
            SELECT 1 FROM "{schema}"."pos_embeddings" e WHERE e.pos_id = t.pos_id
        )
          AND t.is_processed = false
          {warehouse_clause}
          {exact_pos_clause}
        ORDER BY t.pos_id
        {limit_clause};
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
            "oms_company_name": row[11],
            "oms2_company_name": row[12],
            "oms_address_line_1": row[13],
            "oms_city": row[14],
            "oms_state": row[15],
            "oms_zip": row[16],
            "oms_address_line_1_v2": row[17],
            "oms_city_2": row[18],
            "oms_state_2": row[19],
            "oms_zip_2": row[20],
        }
        for row in batch
    ]
    variant_texts = [build_pos_variant_texts(r) for r in records]
    combined_texts = [v["combined_field"] for v in variant_texts]
    name_sources = matching_set_name_fields(BUSINESS_RULES)
    address_sources = matching_set_address_fields(BUSINESS_RULES)

    name_unique_texts, name_row_source_idx = _build_row_variant_embeddings(
        variant_texts,
        name_sources,
    )
    addr_unique_texts, addr_row_source_idx = _build_row_variant_embeddings(
        variant_texts,
        address_sources,
    )

    vectors: dict[str, list[str | None]] = {}
    if name_unique_texts:
        vectors[f"pos_batch_{batch_number}_names"] = embed_texts(
            client,
            name_unique_texts,
            label=f"pos_batch_{batch_number}_names",
        )
    if addr_unique_texts:
        vectors[f"pos_batch_{batch_number}_addresses"] = embed_texts(
            client,
            addr_unique_texts,
            label=f"pos_batch_{batch_number}_addresses",
        )

    if any(combined_texts):
        vectors[f"pos_batch_{batch_number}_combined"] = embed_texts(
            client,
            combined_texts,
            label=f"pos_batch_{batch_number}_combined",
        )
    else:
        vectors[f"pos_batch_{batch_number}_combined"] = [None] * len(batch)

    all_name_sources = {name: [] for name in name_sources}
    all_address_sources = {name: [] for name in address_sources}
    for row_idx in range(len(batch)):
        for source in name_sources:
            source_index = name_row_source_idx[row_idx].get(source)
            all_name_sources[source].append(
                vectors.get(f"pos_batch_{batch_number}_names", [None] * len(batch))[source_index]
                if source_index is not None and vectors.get(f"pos_batch_{batch_number}_names")
                else None
            )
        for source in address_sources:
            source_index = addr_row_source_idx[row_idx].get(source)
            all_address_sources[source].append(
                vectors.get(f"pos_batch_{batch_number}_addresses", [None] * len(batch))[source_index]
                if source_index is not None and vectors.get(f"pos_batch_{batch_number}_addresses")
                else None
            )

    # Ensure all expected variant vectors exist for deterministic indexing.
    for source in ["full_address", "full_oms_address", "full_oms2_address"]:
        all_address_sources.setdefault(source, [None] * len(batch))
    for source in ["business_name", "oms_company_name", "oms2_company_name"]:
        all_name_sources.setdefault(source, [None] * len(batch))

    combined_vectors = vectors[f"pos_batch_{batch_number}_combined"]
    name_vectors = {
        source: all_name_sources[source] for source in name_sources
    }
    address_vectors = {
        source: all_address_sources[source] for source in address_sources
    }

    insert_rows = []
    for i, row in enumerate(batch):
        vt = variant_texts[i]
        insert_rows.append((
            row[0],                         # pos_id
            row[1],                         # account_number
            vt["combined_field"],           # combined_field text
            vt["business_name"],            # business_name text
            vt["full_address"],             # business_address text
            vt["oms_company_name"],         # oms_company_name text
            vt["oms2_company_name"],        # oms2_company_name text
            vt["full_oms_address"],         # full_oms_address text
            vt["full_oms2_address"],        # full_oms2_address text
            combined_vectors[i],            # combined_embedding
            address_vectors.get("full_address", [None] * len(batch))[i],
            name_vectors.get("business_name", [None] * len(batch))[i],
            name_vectors.get("oms_company_name", [None] * len(batch))[i],
            name_vectors.get("oms2_company_name", [None] * len(batch))[i],
            address_vectors.get("full_oms_address", [None] * len(batch))[i],
            address_vectors.get("full_oms2_address", [None] * len(batch))[i],
            now,                            # load_date
            row[2],                         # warehouse_number
            row[3],                         # fiscal_year
            row[4],                         # fiscal_period
            row[5],                         # week
        ))
    return batch_number, insert_rows, time.monotonic() - batch_started


def process_embedding_batches(rows, build_insert_rows, insert_insert_rows, conn, label):
    total_batches = (len(rows) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE
    batches = list(enumerate(chunks(rows, DEFAULT_BATCH_SIZE), start=1))
    inserted = 0
    completed = 0

    def write_result(batch_number, insert_rows, batch_duration):
        nonlocal inserted, completed
        try:
            insert_insert_rows(insert_rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
        futures = {
            executor.submit(build_insert_rows, batch_number, batch): batch_number
            for batch_number, batch in batches
        }
        for future in as_completed(futures):
            batch_number = futures[future]
            try:
                batch_num, insert_rows, duration = future.result()
                write_result(batch_num, insert_rows, duration)
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Database write failed during parallel batch {batch_number}: {e}") from e
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
                oms_company_name, oms2_company_name, oms_address, oms2_address,
                combined_embedding, address_embedding, name_embedding,
                oms_company_name_embedding, oms2_company_name_embedding,
                oms_address_embedding, oms2_address_embedding,
                load_date, warehouse_number, fiscal_year, fiscal_period, week
            )
            """,
            insert_rows,
            21,
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


def _fuzzy_set_sql_fragments(config: dict):
    sets = matching_set_definitions(config)
    if not sets:
        raise RuntimeError("No matching sets configured in business rules")

    alias_map = pos_embedding_variant_field_aliases(config)
    address_weight, name_weight = semantic_precision_weights(config)
    denominator = address_weight + name_weight

    score_exprs = []
    name_score_exprs = []
    address_score_exprs = []
    best_set_cases = []
    boost_email_cases = []
    boost_phone_cases = []
    set_source_mapping = []
    best_name_cases = []
    best_address_cases = []
    pos_field_select_exprs = []

    email_sources = set()
    phone_sources = set()
    set_score_columns = []
    set_aliases = []

    for item in sets:
        set_id = int(item["set"])
        set_alias = f"set{set_id}"
        name_source = str(item.get("name_field", "")).strip()
        address_source = str(item.get("address_field", "")).strip()
        email_source = str(item.get("email_field", "")).strip()
        phone_source = str(item.get("phone_field", "")).strip()
        if not name_source or not address_source:
            raise RuntimeError(f"Invalid matching set definition: {item}")
        if name_source not in alias_map:
            raise RuntimeError(f"Missing embedding alias for name field '{name_source}'")
        if address_source not in alias_map:
            raise RuntimeError(f"Missing embedding alias for address field '{address_source}'")

        name_col = alias_map[name_source]
        address_col = alias_map[address_source]
        name_score_exprs.append(
            f"CASE WHEN s.{name_col} IS NOT NULL AND l_name_emb IS NOT NULL "
            f"THEN (1 - (s.{name_col} <=> l_name_emb)) * 100 END AS {set_alias}_name_score"
        )
        address_score_exprs.append(
            f"CASE WHEN s.{address_col} IS NOT NULL AND l_addr_emb IS NOT NULL "
            f"THEN (1 - (s.{address_col} <=> l_addr_emb)) * 100 END AS {set_alias}_address_score"
        )
        score_exprs.append(
            f"CASE WHEN s.{name_col} IS NOT NULL AND s.{address_col} IS NOT NULL "
            f"THEN ({address_weight} * (1 - (s.{address_col} <=> l_addr_emb)) * 100 "
            f"+ {name_weight} * (1 - COALESCE(NULLIF(s.{name_col} <=> l_name_emb, 'NaN'::float), 1)) * 100) / {denominator} "
            f"END AS {set_alias}_score"
        )
        set_aliases.append(set_alias)
        set_score_columns.append(f"{set_alias}_score")
        best_set_cases.append(f"WHEN {set_alias}_score IS NOT NULL THEN {set_id}")
        boost_email_cases.append(
            f"WHEN winning_set = {set_id} AND lower(trim(COALESCE(lead_email, ''))) <> '' "
            f"AND lower(trim(COALESCE(set{set_id}_pos_email, ''))) = lower(trim(COALESCE(lead_email, ''))) "
            f"THEN %s"
        )
        boost_phone_cases.append(
            f"WHEN winning_set = {set_id} AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g') <> '' "
            f"AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g') "
            f"= regexp_replace(COALESCE(set{set_id}_pos_phone, ''), '\\D', '', 'g') "
            f"THEN %s"
        )
        best_name_cases.append(f"WHEN winning_set = {set_id} THEN {set_alias}_name_score")
        best_address_cases.append(
            f"WHEN winning_set = {set_id} THEN {set_alias}_address_score"
        )
        set_source_mapping.append((set_id, str(item.get("source", "")).strip()))

        email_field = str(item.get("email_field", "")).strip()
        phone_field = str(item.get("phone_field", "")).strip()
        if email_field:
            email_sources.add(email_field)
            pos_field_select_exprs.append(f"\n            t.{email_field} AS set{set_id}_pos_email")
        if phone_field:
            phone_sources.add(phone_field)
            pos_field_select_exprs.append(f"\n            t.{phone_field} AS set{set_id}_pos_phone")

    if any(name_source not in alias_map for name_source in matching_set_name_fields(config)):
        logger.warning(
            "Missing name source alias mapping for one or more fuzzy sets;"
            " fallback to configured set mappings only"
        )
    if any(address_source not in alias_map for address_source in matching_set_address_fields(config)):
        logger.warning(
            "Missing address source alias mapping for one or more fuzzy sets;"
            " fallback to configured set mappings only"
        )

    return {
        "sets": sets,
        "score_exprs": score_exprs,
        "name_score_exprs": name_score_exprs,
        "address_score_exprs": address_score_exprs,
        "winner_case": best_set_cases,
        "best_name_case": best_name_cases,
        "best_address_case": best_address_cases,
        "boost_email_cases": boost_email_cases,
        "boost_phone_cases": boost_phone_cases,
        "set_source_mapping": set_source_mapping,
        "pos_field_selects": pos_field_select_exprs,
        "set_score_columns": set_score_columns,
        "set_aliases": set_aliases,
        "email_sources": sorted(email_sources),
        "phone_sources": sorted(phone_sources),
        "denominator": denominator,
        "address_weight": address_weight,
        "name_weight": name_weight,
    }


def write_back_match_results(conn, cursor, schema, run_id):
    types = exact_match_types()
    min_score = exact_qualified_min_score()
    exact_state = exact_lifecycle_state(BUSINESS_RULES)
    fuzzy_type_lower = fuzzy_match_type(BUSINESS_RULES).lower()
    manual_review_type_lower = manual_review_match_type(BUSINESS_RULES).lower()
    placeholders = _exact_type_placeholders(types)
    fuzzy_lifecycle_params = []
    fuzzy_lifecycle_case = _fuzzy_lifecycle_case("final_score", fuzzy_lifecycle_params)
    cursor.execute(
        f"""
        WITH base AS (
            SELECT
                m.match_run_id,
                m.lead_id,
                m.pos_id,
                m.match_type,
                m.final_score,
                m.created_date,
                l.fiscal_year AS lead_fiscal_year,
                l.fiscal_period AS lead_fiscal_period,
                COALESCE(l.week, 0) AS lead_week,
                t.fiscal_year AS pos_fiscal_year,
                t.fiscal_period AS pos_fiscal_period,
                COALESCE(t.week, 0) AS pos_week
            FROM "{schema}"."match_decision_detail" m
            JOIN "{schema}"."lead" l ON l.lead_id = m.lead_id
            JOIN "{schema}"."transaction" t ON t.pos_id = m.pos_id
            WHERE m.match_run_id = %s
        ),
        prioritized AS (
            SELECT
                *,
                CASE
                    WHEN lower(match_type) IN ({placeholders}) THEN 3
                    WHEN lower(match_type) = %s THEN 2
                    WHEN lower(match_type) = %s THEN 1
                    ELSE 0
                END AS match_priority
            FROM base
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY pos_id
                    ORDER BY match_priority DESC, final_score DESC NULLS LAST, created_date DESC, lead_id
                ) AS decision_rank
            FROM prioritized
        ),
        classified AS (
            SELECT
                *,
                CASE
                    WHEN match_priority = 3 THEN %s
                    ELSE {fuzzy_lifecycle_case}
                END AS lifecycle_state
            FROM ranked
            WHERE decision_rank = 1
        ),
        primary_rows AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY lead_id
                    ORDER BY pos_fiscal_year, pos_fiscal_period, pos_week, pos_id
                ) AS primary_rank
            FROM classified
            WHERE lifecycle_state = %s
        ),
        tx_updates AS (
            SELECT
                c.pos_id,
                c.lead_id,
                c.final_score,
                c.match_type,
                c.match_run_id,
                c.match_priority,
                c.lifecycle_state,
                COALESCE(p.primary_rank = 1, false) AS primary_transaction
            FROM classified c
            LEFT JOIN primary_rows p
              ON p.match_run_id = c.match_run_id
             AND p.lead_id = c.lead_id
             AND p.pos_id = c.pos_id
        )
        UPDATE "{schema}"."transaction" t
        SET
            lead_id = tx.lead_id,
            match_score = tx.final_score,
            match_type = tx.match_type,
            primary_transaction = tx.primary_transaction,
            is_processed = true,
            process_datetime = CURRENT_TIMESTAMP,
            updated_by = 'lead_match_runtime',
            updated_date = CURRENT_TIMESTAMP,
            matching_comments = CONCAT(
                'match_run_id=', CAST(tx.match_run_id AS text),
                '; lifecycle_state=', tx.lifecycle_state
            )
        FROM tx_updates tx
        WHERE t.pos_id = tx.pos_id
          AND tx.match_priority >= CASE
              WHEN lower(COALESCE(t.match_type, '')) IN ({placeholders})
               AND (t.match_score IS NULL OR t.match_score >= %s)
              THEN 3
              WHEN lower(COALESCE(t.match_type, '')) = %s THEN 2
              WHEN lower(COALESCE(t.match_type, '')) = %s THEN 1
              ELSE 0
          END
        """,
        (
            run_id,
            *types,
            fuzzy_type_lower,
            manual_review_type_lower,
            exact_state,
            *fuzzy_lifecycle_params,
            exact_state,
            *types,
            min_score,
            fuzzy_type_lower,
            manual_review_type_lower,
        ),
    )
    transaction_updates = cursor.rowcount

    lead_lifecycle_params = []
    lead_lifecycle_case = _fuzzy_lifecycle_case("m.final_score", lead_lifecycle_params)
    fuzzy_states = tuple(
        dict.fromkeys(str(band["lifecycle_state"]) for band in fuzzy_score_bands(BUSINESS_RULES))
    )
    potential_states = tuple(state for state in fuzzy_states if state != exact_state)
    potential_state = potential_states[0] if potential_states else exact_state
    no_match_state = no_match_lifecycle_state(BUSINESS_RULES)
    potential_condition = "false"
    if potential_states:
        potential_placeholders = ", ".join(["%s"] * len(potential_states))
        potential_condition = f"match_result IN ({potential_placeholders})"
    cursor.execute(
        f"""
        WITH classified AS (
            SELECT
                m.lead_id,
                CASE
                    WHEN lower(m.match_type) IN ({placeholders}) THEN %s
                    ELSE {lead_lifecycle_case}
                END AS match_result
            FROM "{schema}"."match_decision_detail" m
            JOIN "{schema}"."lead" l ON l.lead_id = m.lead_id
            JOIN "{schema}"."transaction" t ON t.pos_id = m.pos_id
            WHERE m.match_run_id = %s
        ),
        lead_states AS (
            SELECT
                lead_id,
                CASE
                    WHEN bool_or(match_result = %s) THEN %s
                    WHEN bool_or({potential_condition}) THEN %s
                    ELSE %s
                END AS match_result
            FROM classified
            GROUP BY lead_id
        )
        UPDATE "{schema}"."lead" l
        SET
            match_result = lead_states.match_result,
            updated_by = 'lead_match_runtime',
            updated_date = CURRENT_TIMESTAMP
        FROM lead_states
        WHERE l.lead_id = lead_states.lead_id
          AND (
              EXISTS (
                  SELECT 1
                  FROM "{schema}"."match_decision_detail" cur
                  WHERE cur.lead_id = l.lead_id
                    AND cur.match_run_id = %s
                    AND lower(cur.match_type) IN ({placeholders})
              )
              OR (
                  NOT EXISTS (
                      SELECT 1
                      FROM "{schema}"."match_decision_detail" exact_m
                      WHERE exact_m.lead_id = l.lead_id
                        AND exact_m.match_run_id <> %s
                        AND lower(exact_m.match_type) IN ({placeholders})
                        AND (exact_m.final_score IS NULL OR exact_m.final_score >= %s)
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM "{schema}"."transaction" exact_t
                      WHERE exact_t.lead_id = l.lead_id
                        AND lower(exact_t.match_type) IN ({placeholders})
                        AND (exact_t.match_score IS NULL OR exact_t.match_score >= %s)
                  )
              )
          )
        """,
        (
            *types,
            exact_state,
            *lead_lifecycle_params,
            run_id,
            exact_state,
            exact_state,
            *potential_states,
            potential_state,
            no_match_state,
            run_id,
            *types,
            run_id,
            *types,
            min_score,
            *types,
            min_score,
        ),
    )
    lead_updates = cursor.rowcount
    conn.commit()
    print(
        f"Business table writeback complete: "
        f"transaction_updates={transaction_updates}; lead_updates={lead_updates}"
    )
    return transaction_updates, lead_updates


def write_match_audit(cursor, schema, run_id, lead_count, match_count, status, comments):
    cursor.execute(
        f"""
        SELECT COUNT(DISTINCT pos_id)
        FROM "{schema}"."match_decision_detail"
        WHERE match_run_id = %s
        """,
        (run_id,),
    )
    pos_count = int(cursor.fetchone()[0])
    stats = json.dumps(
        {
            "match_run_id": run_id,
            "dry_run": DRY_RUN,
            "processed_leads": lead_count,
            "matched_pos": pos_count,
            "match_rows": match_count,
        },
        sort_keys=True,
    )
    cursor.execute(
        f"""
        INSERT INTO "{schema}"."match_audit" (
            match_id,
            lead_count,
            pos_count,
            match_count,
            stats,
            status,
            start_date,
            end_date,
            update_date,
            comments
        )
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s)
        """,
        (
            str(uuid.uuid4()),
            lead_count,
            pos_count,
            match_count,
            stats,
            status,
            comments,
        ),
    )
    print(
        f"Match audit inserted: status={status}; lead_count={lead_count}; "
        f"pos_count={pos_count}; match_count={match_count}"
    )


def run_exact_match():
    job_started = time.monotonic()
    conn = connect()
    try:
        _run_exact_match(conn, job_started)
    finally:
        conn.close()


def _run_exact_match(conn, job_started):
    cursor = conn.cursor()
    schema = schema_name()
    run_id = os.environ.get("MATCH_RUN_ID") or f"workflow-{uuid.uuid4().hex[:12]}"
    warehouse_clause, warehouse_params = warehouse_sql_filter("t")
    limit = optional_positive_int_env("MATCH_LEAD_LIMIT")
    limit_clause = ""
    params = []
    if limit is not None:
        limit_clause = "LIMIT %s"
        params.append(limit)
    params.extend(warehouse_params)
    exact_type = exact_match_type(BUSINESS_RULES)
    exact_match_score = exact_score(BUSINESS_RULES)
    params.extend([
        run_id,
        exact_type,
        exact_match_score,
        exact_match_score,
        exact_match_score,
        exact_match_score,
    ])

    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(
        f"Exact deterministic match run: {run_id}; "
        f"match_lead_limit={limit or 'all'}; dry_run={DRY_RUN}"
    )

    cursor.execute(
        f"""
        WITH lead_candidates AS (
            SELECT
                l.lead_id,
                l.warehouse_number,
                upper(regexp_replace(trim(COALESCE(a.business_name, '')), '\\s+', ' ', 'g')) AS business_name_key,
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                regexp_replace(
                                    regexp_replace(
                                        upper(regexp_replace(trim(COALESCE(a.address_line_one, '')), '\\s+', ' ', 'g')),
                                        '\\mSTREET\\M', 'ST', 'g'
                                    ),
                                    '\\mAVENUE\\M', 'AVE', 'g'
                                ),
                                '\\mROAD\\M', 'RD', 'g'
                            ),
                            '\\mDRIVE\\M', 'DR', 'g'
                        ),
                        '\\mLANE\\M', 'LN', 'g'
                    ),
                    '\\mBOULEVARD\\M', 'BLVD', 'g'
                ) AS address_key,
                upper(regexp_replace(trim(COALESCE(a.city, '')), '\\s+', ' ', 'g')) AS city_key,
                left(upper(trim(COALESCE(a.state, ''))), 2) AS state_key,
                left(regexp_replace(COALESCE(a.zip_code, ''), '\\D', '', 'g'), 5) AS zip_key
            FROM "{schema}"."lead" l
            JOIN "{schema}"."account" a ON a.account_id = l.account_id
            WHERE l.lead_id IS NOT NULL
              AND l.warehouse_number IS NOT NULL
            ORDER BY l.lead_id
            {limit_clause}
        ),
        pos_candidates AS (
            SELECT
                t.pos_id,
                t.warehouse_number,
                t.fiscal_year,
                t.fiscal_period,
                COALESCE(t.week, 0) AS week,
                upper(regexp_replace(trim(COALESCE(t.business_name, '')), '\\s+', ' ', 'g')) AS business_name_key,
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                regexp_replace(
                                    regexp_replace(
                                        upper(regexp_replace(trim(COALESCE(t.address_line_one, '')), '\\s+', ' ', 'g')),
                                        '\\mSTREET\\M', 'ST', 'g'
                                    ),
                                    '\\mAVENUE\\M', 'AVE', 'g'
                                ),
                                '\\mROAD\\M', 'RD', 'g'
                            ),
                            '\\mDRIVE\\M', 'DR', 'g'
                        ),
                        '\\mLANE\\M', 'LN', 'g'
                    ),
                    '\\mBOULEVARD\\M', 'BLVD', 'g'
                ) AS address_key,
                upper(regexp_replace(trim(COALESCE(t.city, '')), '\\s+', ' ', 'g')) AS city_key,
                left(upper(trim(COALESCE(t.state, ''))), 2) AS state_key,
                left(regexp_replace(COALESCE(t.zip_code, ''), '\\D', '', 'g'), 5) AS zip_key
            FROM "{schema}"."transaction" t
            WHERE t.pos_id IS NOT NULL
              AND t.warehouse_number IS NOT NULL
              {warehouse_clause}
        ),
        exact_pairs AS (
            SELECT
                l.lead_id,
                p.pos_id,
                p.warehouse_number,
                p.fiscal_year,
                p.fiscal_period,
                p.week
            FROM lead_candidates l
            JOIN pos_candidates p
              ON p.warehouse_number = l.warehouse_number
             AND p.business_name_key = l.business_name_key
             AND p.address_key = l.address_key
             AND p.city_key = l.city_key
             AND p.state_key = l.state_key
             AND p.zip_key = l.zip_key
            WHERE l.business_name_key <> ''
              AND l.address_key <> ''
              AND l.city_key <> ''
              AND l.state_key <> ''
              AND l.zip_key <> ''
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
            %s,
            %s,
            %s,
            %s,
            %s,
            'deterministic 5-field exact identity',
            'exact-sql',
            CURRENT_TIMESTAMP
        FROM exact_pairs
        ORDER BY lead_id, fiscal_year, fiscal_period, week, pos_id
        ON CONFLICT (match_run_id, lead_id, pos_id) DO NOTHING
        """,
        params,
    )
    inserted = cursor.rowcount
    conn.commit()

    cursor.execute(
        f"""
        SELECT COUNT(DISTINCT lead_id), COUNT(DISTINCT pos_id)
        FROM "{schema}"."match_decision_detail"
        WHERE match_run_id = %s
          AND lower(match_type) IN ({_exact_type_placeholders(exact_match_types())})
        """,
        (run_id, *exact_match_types()),
    )
    exact_leads, exact_pos = cursor.fetchone()
    print(
        f"Inserted deterministic exact match decision rows: {inserted}; "
        f"exact_leads={exact_leads}; exact_pos={exact_pos}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )
    if inserted == 0:
        print(
            "No deterministic exact rows were found for this warehouse scope."
        )
    if exact_pos:
        write_back_match_results(conn, cursor, schema, run_id)
    conn.commit()


def _run_fuzzy_match(conn, job_started):
    cursor = conn.cursor()
    schema = schema_name()
    run_id = os.environ.get("MATCH_RUN_ID") or f"workflow-{uuid.uuid4().hex[:12]}"
    limit = optional_positive_int_env("MATCH_LEAD_LIMIT") or 1000000
    rules = BUSINESS_RULES
    recall_gate = float(rules["candidate_retrieval"]["recall_gate_min_similarity"])
    qualify_min = fuzzy_qualify_min_score(rules)
    fuzzy_ceiling = fuzzy_max_score(rules)
    artifact_threshold = fuzzy_artifact_score(rules)
    fuzzy_type = fuzzy_match_type(rules)
    manual_review_type = manual_review_match_type(rules)
    ambiguity_delta = float(rules["resolution"]["ambiguity_delta"])
    nearest_neighbor_limit = int(rules["candidate_retrieval"]["nearest_neighbor_limit"])
    address_weight, business_weight = semantic_precision_weights(rules)
    precision_weight_total = address_weight + business_weight
    if precision_weight_total <= 0:
        raise ValueError("Semantic precision score weights must sum to a positive value")
    weight_formula = precision_score_formula(rules)

    print(f"Warehouse scope: {warehouse_scope_label()}")
    print(f"Match run: {run_id}")
    print(
        f"Match lead limit: {limit}; match_batch_size: {MATCH_BATCH_SIZE}; "
        f"nearest_neighbor_limit: {nearest_neighbor_limit}; "
        f"statement_timeout_ms: {MATCH_STATEMENT_TIMEOUT_MS}; "
        f"hnsw_ef_search: {HNSW_EF_SEARCH}; dry_run: {DRY_RUN}; "
        f"dry_run_match_row_limit: {DRY_RUN_MATCH_ROW_LIMIT}"
    )
    configure_hnsw_search(conn, cursor)
    hnsw_indexes, _ = verify_hnsw_combined_indexes(cursor, schema)
    print(
        "Verified combined_embedding HNSW indexes: "
        + ", ".join(f"{row[0]}.{row[1]}" for row in hnsw_indexes)
    )
    if MATCH_STATEMENT_TIMEOUT_MS > 0:
        cursor.execute(f"SET statement_timeout = {MATCH_STATEMENT_TIMEOUT_MS}")
        print(f"statement_timeout set to {MATCH_STATEMENT_TIMEOUT_MS}ms")

    processed_leads = 0
    inserted = 0
    last_lead_id = None
    batch_number = 0

    while processed_leads < limit:
        fetch_params = []
        warehouse_clause, warehouse_params = warehouse_sql_filter("leads_embeddings")
        warehouse_clause = warehouse_clause.replace("leads_embeddings.", "")
        fetch_params.extend(warehouse_params)
        exact_lead_clause = exact_lead_exclusion_clause(
            schema,
            "leads_embeddings.lead_id",
            fetch_params,
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
        )
        periods_per_year = fiscal_periods_per_year(rules)
        ce_window = fiscal_ce_period_window(rules)
        fuzzy_lifecycle = fuzzy_lifecycle_state_label(rules)
        ce_lifecycle = str(rules["fiscal_rules"]["classification"][0]["lifecycle_state"])
        email_boost_val = float(rules["scoring"]["deterministic_boosts"]["email_exact_match"])
        phone_boost_val = float(rules["scoring"]["deterministic_boosts"]["phone_exact_match"])
        boost_cap = float(rules["scoring"]["deterministic_boosts"]["cap"])

        params.extend([
            recall_gate,
            nearest_neighbor_limit,
            periods_per_year,
            ce_window,
            address_weight,
            business_weight,
            precision_weight_total,
            address_weight,
            business_weight,
            precision_weight_total,
            address_weight,
            business_weight,
            precision_weight_total,
            address_weight,
            business_weight,
            precision_weight_total,
            address_weight,
            business_weight,
            precision_weight_total,
            address_weight,
            business_weight,
            precision_weight_total,
            email_boost_val,
            phone_boost_val,
            boost_cap,
            fuzzy_ceiling,
            qualify_min,
        ])
        query_started = time.monotonic()
        match_cte = f"""
            WITH lead_batch AS (
                SELECT l.*, le.email AS lead_email, le.phone AS lead_phone
                FROM "{schema}"."leads_embeddings" l
                LEFT JOIN "{schema}"."lead" ld ON ld.lead_id = l.lead_id
                LEFT JOIN "{schema}"."account" le ON le.account_id = ld.account_id
                WHERE l.combined_embedding IS NOT NULL
                  AND l.lead_id IN ({lead_placeholders})
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
                    l.address_embedding AS l_addr_emb,
                    l.name_embedding AS l_name_emb,
                    s.address_embedding AS s_addr_emb,
                    s.name_embedding AS s_name_emb,
                    s.oms_company_name_embedding AS s_oms_co_emb,
                    s.oms2_company_name_embedding AS s_oms2_co_emb,
                    s.oms_address_embedding AS s_oms_addr_emb,
                    s.oms2_address_embedding AS s_oms2_addr_emb,
                    l.lead_email,
                    l.lead_phone,
                    t.email AS pos_email,
                    t.phone AS pos_phone,
                    t.oms_email_1,
                    t.oms_phone_1,
                    t.oms_email_2,
                    t.oms_phone_2
                FROM lead_batch l
                CROSS JOIN LATERAL (
                    SELECT s_inner.*
                    FROM "{schema}"."pos_embeddings" s_inner
                    WHERE s_inner.combined_embedding IS NOT NULL
                      AND s_inner.warehouse_number = l.warehouse_number
                      {exact_pos_clause}
                      AND (1 - (s_inner.combined_embedding <=> l.combined_embedding)) * 100 >= %s
                    ORDER BY s_inner.combined_embedding <=> l.combined_embedding
                    LIMIT %s
                ) s
                JOIN "{schema}"."transaction" t ON t.pos_id = s.pos_id
            ),
            fiscal_classified AS (
                SELECT *,
                    (%s * (lead_fiscal_year - pos_fiscal_year)
                     + (lead_fiscal_period - pos_fiscal_period)) AS period_gap,
                    CASE
                        WHEN (pos_fiscal_year < lead_fiscal_year)
                          OR (pos_fiscal_year = lead_fiscal_year AND pos_fiscal_period < lead_fiscal_period)
                        THEN true ELSE false
                    END AS pos_before_lead
                FROM candidates
            ),
            classified AS (
                SELECT *,
                    CASE
                        WHEN pos_before_lead AND period_gap <= %s THEN 'CE'
                        WHEN pos_before_lead AND period_gap > %s THEN 'OAF'
                        ELSE 'NORMAL'
                    END AS fiscal_class
                FROM fiscal_classified
            ),
            normal_candidates AS (
                SELECT * FROM classified WHERE fiscal_class = 'NORMAL'
            ),
            six_set_scores AS (
                SELECT
                    lead_id, pos_id, warehouse_number,
                    pos_fiscal_year, pos_fiscal_period, pos_week,
                    combined_field_score,
                    lead_email, lead_phone,
                    pos_email, pos_phone,
                    oms_email_1, oms_phone_1,
                    oms_email_2, oms_phone_2,
                    -- Set 1: business_name × full_address
                    CASE WHEN s_name_emb IS NOT NULL AND s_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_name_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set1_score,
                    -- Set 2: oms_company_name × full_address
                    CASE WHEN s_oms_co_emb IS NOT NULL AND s_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_oms_co_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set2_score,
                    -- Set 3: business_name × full_oms_address
                    CASE WHEN s_name_emb IS NOT NULL AND s_oms_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_oms_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_name_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set3_score,
                    -- Set 4: oms_company_name × full_oms_address
                    CASE WHEN s_oms_co_emb IS NOT NULL AND s_oms_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_oms_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_oms_co_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set4_score,
                    -- Set 5: business_name × full_oms2_address
                    CASE WHEN s_name_emb IS NOT NULL AND s_oms2_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_oms2_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_name_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set5_score,
                    -- Set 6: oms2_company_name × full_oms2_address
                    CASE WHEN s_oms2_co_emb IS NOT NULL AND s_oms2_addr_emb IS NOT NULL THEN
                        (%s * (1 - (s_oms2_addr_emb <=> l_addr_emb)) * 100
                         + %s * (1 - COALESCE(NULLIF(s_oms2_co_emb <=> l_name_emb, 'NaN'::float), 1)) * 100
                        ) / %s
                    END AS set6_score
                FROM normal_candidates
            ),
            best_set AS (
                SELECT *,
                    GREATEST(
                        COALESCE(set1_score, -1), COALESCE(set2_score, -1),
                        COALESCE(set3_score, -1), COALESCE(set4_score, -1),
                        COALESCE(set5_score, -1), COALESCE(set6_score, -1)
                    ) AS best_set_score,
                    CASE GREATEST(
                        COALESCE(set1_score, -1), COALESCE(set2_score, -1),
                        COALESCE(set3_score, -1), COALESCE(set4_score, -1),
                        COALESCE(set5_score, -1), COALESCE(set6_score, -1)
                    )
                        WHEN set1_score THEN 1
                        WHEN set2_score THEN 2
                        WHEN set3_score THEN 3
                        WHEN set4_score THEN 4
                        WHEN set5_score THEN 5
                        WHEN set6_score THEN 6
                        ELSE 1
                    END AS winning_set
                FROM six_set_scores
            ),
            boosted AS (
                SELECT *,
                    CASE WHEN winning_set IN (1, 2) AND lower(trim(COALESCE(lead_email, ''))) <> ''
                         AND lower(trim(COALESCE(lead_email, ''))) = lower(trim(COALESCE(pos_email, '')))
                         THEN %s
                         WHEN winning_set IN (3, 4) AND lower(trim(COALESCE(lead_email, ''))) <> ''
                         AND lower(trim(COALESCE(lead_email, ''))) = lower(trim(COALESCE(oms_email_1, '')))
                         THEN %s
                         WHEN winning_set IN (5, 6) AND lower(trim(COALESCE(lead_email, ''))) <> ''
                         AND lower(trim(COALESCE(lead_email, ''))) = lower(trim(COALESCE(oms_email_2, '')))
                         THEN %s
                         ELSE 0
                    END AS email_boost,
                    CASE WHEN winning_set IN (1, 2)
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g') <> ''
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g')
                           = regexp_replace(COALESCE(pos_phone, ''), '\\D', '', 'g')
                         THEN %s
                         WHEN winning_set IN (3, 4)
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g') <> ''
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g')
                           = regexp_replace(COALESCE(oms_phone_1, ''), '\\D', '', 'g')
                         THEN %s
                         WHEN winning_set IN (5, 6)
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g') <> ''
                         AND regexp_replace(COALESCE(lead_phone, ''), '\\D', '', 'g')
                           = regexp_replace(COALESCE(oms_phone_2, ''), '\\D', '', 'g')
                         THEN %s
                         ELSE 0
                    END AS phone_boost
                FROM best_set
                WHERE best_set_score >= 0
            ),
            scored AS (
                SELECT *,
                    LEAST(
                        %s,
                        ROUND((best_set_score + email_boost + phone_boost)::numeric, 2)::double precision
                    ) AS final_score,
                    best_set_score AS raw_final_score
                FROM boosted
            ),
            ranked AS (
                SELECT * FROM scored WHERE final_score >= %s
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
        """
        if EXPLAIN_FUZZY_PLAN and batch_number == 1:
            configure_hnsw_search(conn, cursor, local=True)
            cursor.execute(
                f"""
                EXPLAIN (FORMAT TEXT)
                {match_cte}
                SELECT pos_id
                FROM best_unique_pos
                WHERE pos_rank = 1
                ORDER BY final_score DESC, lead_id, pos_id
                LIMIT 1
                """,
                params,
            )
            plan_text = "\n".join(row[0] for row in cursor.fetchall())
            if "Seq Scan on pos_embeddings" in plan_text or "hnsw" not in plan_text.lower():
                logger.warning(
                    "Fuzzy top-k EXPLAIN did not show HNSW index usage; plan follows:\n%s",
                    plan_text,
                )
            else:
                print("Fuzzy top-k EXPLAIN shows HNSW index usage")

        insert_params = [*params]
        insert_params.extend([
            run_id,
            ambiguity_delta,
            manual_review_type,
            fuzzy_type,
            fuzzy_lifecycle,
            weight_formula,
            EMBEDDING_MODEL,
        ])
        if DRY_RUN:
            remaining_preview_rows = DRY_RUN_MATCH_ROW_LIMIT - inserted
            if remaining_preview_rows <= 0:
                print(
                    f"Dry-run preview row limit reached: {DRY_RUN_MATCH_ROW_LIMIT}; "
                    "skipping remaining candidate inserts"
                )
                break
            insert_params.append(remaining_preview_rows)
        insert_params.append(artifact_threshold)
        configure_hnsw_search(conn, cursor, local=True)
        cursor.execute(
            f"""
            {match_cte},
            inserted AS (
                INSERT INTO "{schema}"."match_decision_detail" (
                    match_run_id, lead_id, pos_id, warehouse_number, match_type, final_score,
                    combined_field_score, full_address_score, business_name_score,
                    winning_set, name_score, address_score, email_boost, phone_boost,
                    lifecycle_state,
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
                        THEN %s
                        ELSE %s
                    END,
                    final_score,
                    combined_field_score,
                    COALESCE(set1_score, 0),
                    COALESCE(set1_score, 0),
                    winning_set,
                    best_set_score,
                    best_set_score,
                    email_boost,
                    phone_boost,
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                FROM best_unique_pos
                WHERE pos_rank = 1
                ORDER BY final_score DESC, lead_id, pos_id
                {"LIMIT %s" if DRY_RUN else ""}
                ON CONFLICT (match_run_id, lead_id, pos_id) DO NOTHING
                RETURNING lead_id, pos_id
            ),
            score_artifacts AS (
                SELECT lead_id, pos_id, raw_final_score
                FROM best_unique_pos
                WHERE pos_rank = 1
                  AND raw_final_score >= %s
                ORDER BY raw_final_score DESC, lead_id, pos_id
                LIMIT 20
            )
            SELECT
                (SELECT COUNT(*) FROM inserted) AS inserted_count,
                COALESCE(
                    (
                        SELECT json_agg(
                            json_build_object(
                                'lead_id', lead_id,
                                'pos_id', pos_id,
                                'raw_final_score', raw_final_score
                            )
                        )
                        FROM score_artifacts
                    ),
                    '[]'::json
                ) AS score_artifacts;
            """,
            insert_params,
        )
        insert_summary = cursor.fetchone()
        batch_inserted = int(insert_summary[0]) if insert_summary else 0
        score_artifacts = insert_summary[1] if insert_summary else []
        if isinstance(score_artifacts, str):
            score_artifacts = json.loads(score_artifacts)
        for artifact in score_artifacts:
            logger.warning(
                "Raw fuzzy score %.3f exceeded fuzzy ceiling for lead_id=%s pos_id=%s; clamping to %.2f",
                float(artifact["raw_final_score"]),
                artifact["lead_id"],
                artifact["pos_id"],
                fuzzy_ceiling,
            )
        conn.commit()
        inserted += batch_inserted
        query_duration = time.monotonic() - query_started
        print(
            f"Fuzzy match batch {batch_number}: leads={len(lead_ids)}; "
            f"processed_leads={processed_leads}; "
            f"{'dry_run_preview_rows' if DRY_RUN else 'inserted_rows'}={batch_inserted}; "
            f"batch_duration_seconds={query_duration:.2f}"
        )
        if DRY_RUN and inserted >= DRY_RUN_MATCH_ROW_LIMIT:
            print(f"Dry-run preview row limit reached: {DRY_RUN_MATCH_ROW_LIMIT}")
            break

    conn.commit()
    if processed_leads == 0:
        print(
            "Warning: fuzzy match processed 0 leads after warehouse and exact-match filters",
            file=sys.stderr,
        )
    print(
        f"{'Inserted dry-run preview' if DRY_RUN else 'Inserted'} match decision rows: {inserted}; "
        f"processed_leads={processed_leads}; "
        f"duration_seconds={time.monotonic() - job_started:.2f}"
    )
    if inserted > 0 and (not DRY_RUN or DRY_RUN_WRITEBACK_BUSINESS_TABLES):
        write_back_match_results(conn, cursor, schema, run_id)
    elif DRY_RUN and inserted > 0:
        print(
            "Dry-run business table writeback skipped; "
            "set DRY_RUN_WRITEBACK_BUSINESS_TABLES=true to test lead/transaction updates"
        )
    audit_status = "DRY_RUN" if DRY_RUN else "COMPLETED"
    audit_comments = (
        f"match_run_id={run_id}; "
        f"warehouse_scope={warehouse_scope_label()}; "
        f"dry_run={DRY_RUN}; "
        f"business_writeback={'enabled' if (not DRY_RUN or DRY_RUN_WRITEBACK_BUSINESS_TABLES) else 'skipped'}"
    )
    write_match_audit(
        cursor,
        schema,
        run_id,
        processed_leads,
        inserted,
        audit_status,
        audit_comments,
    )
    conn.commit()


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
    print(
        f"HNSW build params: m={HNSW_M}; ef_construction={HNSW_EF_CONSTRUCTION}; "
        f"maintenance_work_mem={HNSW_MAINTENANCE_WORK_MEM}"
    )

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
    if not re.match(r"^\d+(B|kB|MB|GB|TB)$", HNSW_MAINTENANCE_WORK_MEM):
        raise RuntimeError(
            "HNSW_MAINTENANCE_WORK_MEM must be a PostgreSQL memory value like 512MB"
        )
    execute(
        "set HNSW maintenance_work_mem",
        f"SET maintenance_work_mem = '{HNSW_MAINTENANCE_WORK_MEM}'",
    )

    required_tables = ("leads_embeddings", "pos_embeddings")
    lead_vector_columns = ("combined_embedding", "address_embedding", "name_embedding")
    pos_vector_columns = (
        "combined_embedding", "address_embedding", "name_embedding",
        "oms_company_name_embedding", "oms2_company_name_embedding",
        "oms_address_embedding", "oms2_address_embedding",
    )
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
    existing_hnsw_indexes = fetchall(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s
          AND indexname IN (
            'idx_leads_embeddings_combined_hnsw',
            'idx_pos_embeddings_combined_hnsw'
          )
        """,
        (schema,),
    )
    for index_name, indexdef in existing_hnsw_indexes:
        indexdef_lower = indexdef.lower()
        expected_m = f"m='{HNSW_M}'"
        expected_ef = f"ef_construction='{HNSW_EF_CONSTRUCTION}'"
        if expected_m not in indexdef_lower or expected_ef not in indexdef_lower:
            execute(
                f"drop HNSW index with stale params {index_name}",
                f"DROP INDEX CONCURRENTLY IF EXISTS {qualified_name(schema, index_name)}",
            )

    for table in required_tables:
        vector_columns = pos_vector_columns if table == "pos_embeddings" else lead_vector_columns
        attname_list = ", ".join(f"'{c}'" for c in vector_columns)
        current_types = {
            row[0]: row[1]
            for row in fetchall(
                f"""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s
                  AND a.attname IN ({attname_list})
                """,
                (schema, table),
            )
        }
        required_vec_cols = lead_vector_columns if table == "leads_embeddings" else lead_vector_columns
        missing_columns = [
            column
            for column in required_vec_cols
            if column not in current_types
        ]
        if missing_columns:
            raise RuntimeError(
                f"{schema}.{table} is missing vector columns: {missing_columns}"
            )
        cols_needing_alter = [
            c for c in vector_columns
            if c in current_types and current_types[c] != f"vector({dimension})"
        ]
        if cols_needing_alter:
            alter_parts = ", ".join(
                f"ALTER COLUMN {c} TYPE vector({dimension}) USING {c}::vector({dimension})"
                for c in cols_needing_alter
            )
            execute(
                f"dimension {table} vector columns",
                f"ALTER TABLE {qualified_name(schema, table)} {alter_parts}",
            )
        if table == "pos_embeddings":
            for col in pos_vector_columns:
                if col not in current_types and col not in lead_vector_columns:
                    execute(
                        f"add {col} to {table}",
                        f"ALTER TABLE {qualified_name(schema, table)} ADD COLUMN IF NOT EXISTS {col} vector({dimension})",
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
        WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})
        """,
    )
    execute(
        "create POS combined HNSW index",
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pos_embeddings_combined_hnsw
        ON {qualified_name(schema, "pos_embeddings")}
        USING hnsw (combined_embedding vector_cosine_ops)
        WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})
        """,
    )

    execute(
        "create transaction unprocessed partial index",
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transaction_unprocessed
        ON {qualified_name(schema, "transaction")} (warehouse_number)
        WHERE is_processed = false
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
        choices=(
            "summary",
            "smoke",
            "exact-match",
            "lead-embeddings",
            "pos-embeddings",
            "fuzzy-match",
            "report",
            "ensure-indexes",
        ),
    )
    args, remaining = parser.parse_known_args()

    if args.task == "summary":
        print_summary()
    elif args.task == "smoke":
        from lead_match_runtime.smoke_test import main as smoke_main
        sys.argv = [sys.argv[0]] + remaining
        smoke_main()
    elif args.task == "exact-match":
        run_exact_match()
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
