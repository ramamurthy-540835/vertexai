import argparse
import os
import sys
import uuid
from datetime import UTC, datetime

import numpy as np
import pg8000.dbapi
from google import genai
from google.genai import types


EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "768"))
DEFAULT_FISCAL_YEAR = int(os.environ.get("DEFAULT_FISCAL_YEAR", "2026"))
DEFAULT_FISCAL_PERIOD = int(os.environ.get("DEFAULT_FISCAL_PERIOD", "10"))
DEFAULT_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "25"))


def db_config():
    missing = [
        name
        for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required DB env vars: {', '.join(missing)}")

    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "database": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def connect():
    return pg8000.dbapi.connect(**db_config())


def schema_name():
    return os.environ.get("DB_SCHEMA", "leadmgmt")


def vertex_client():
    project = os.environ.get("VERTEX_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
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


def embed_texts(client, texts):
    normalized = [(text or "").strip() for text in texts]
    if not any(normalized):
        return [None] * len(normalized)

    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=normalized,
        config=types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=EMBEDDING_DIMENSION,
        ),
    )
    return [vector_literal(embedding.values) for embedding in response.embeddings]


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
    cursor.execute(
        f"""
        SELECT
            l.lead_id,
            l.warehouse_number,
            COALESCE(l.fiscal_year, %s) AS fiscal_year,
            COALESCE(l.fiscal_period, %s) AS fiscal_period,
            COALESCE(a.business_name, '') AS business_name,
            CONCAT_WS(' ', a.address_line_one, a.address_line_two, a.city, a.state, a.zip_code) AS full_address,
            CONCAT_WS(' ', a.business_name, a.address_line_one, a.address_line_two, a.city, a.state, a.zip_code) AS combined_field
        FROM "{schema}"."lead" l
        JOIN "{schema}"."account" a ON a.account_id = l.account_id
        WHERE NOT EXISTS (
            SELECT 1 FROM "{schema}"."leads_embeddings" e WHERE e.lead_id = l.lead_id
        )
        ORDER BY l.lead_id;
        """,
        (DEFAULT_FISCAL_YEAR, DEFAULT_FISCAL_PERIOD),
    )
    return cursor.fetchall()


def pos_source_rows(cursor):
    schema = schema_name()
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
            CONCAT_WS(' ', t.address_line_one, t.address_line_two, t.city, t.state, t.zip_code) AS full_address,
            CONCAT_WS(' ', t.business_name, t.address_line_one, t.address_line_two, t.city, t.state, t.zip_code) AS combined_field
        FROM "{schema}"."transaction" t
        WHERE NOT EXISTS (
            SELECT 1 FROM "{schema}"."pos_embeddings" e WHERE e.pos_id = t.pos_id
        )
        ORDER BY t.pos_id;
        """
    )
    return cursor.fetchall()


def generate_lead_embeddings():
    client = vertex_client()
    conn = connect()
    cursor = conn.cursor()
    rows = lead_source_rows(cursor)
    print(f"Lead rows needing embeddings: {len(rows)}")

    insert_rows = []
    now = datetime.now(UTC)
    for batch in chunks(rows, DEFAULT_BATCH_SIZE):
        combined_vectors = embed_texts(client, [row[6] for row in batch])
        address_vectors = embed_texts(client, [row[5] for row in batch])
        name_vectors = embed_texts(client, [row[4] for row in batch])
        for row, combined, address, name in zip(batch, combined_vectors, address_vectors, name_vectors):
            insert_rows.append((
                row[0],
                row[6],
                row[4],
                row[5],
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
    conn.close()
    print(f"Inserted lead embeddings: {len(insert_rows)}")


def generate_pos_embeddings():
    client = vertex_client()
    conn = connect()
    cursor = conn.cursor()
    rows = pos_source_rows(cursor)
    print(f"POS rows needing embeddings: {len(rows)}")

    insert_rows = []
    now = datetime.now(UTC)
    for batch in chunks(rows, DEFAULT_BATCH_SIZE):
        combined_vectors = embed_texts(client, [row[8] for row in batch])
        address_vectors = embed_texts(client, [row[7] for row in batch])
        name_vectors = embed_texts(client, [row[6] for row in batch])
        for row, combined, address, name in zip(batch, combined_vectors, address_vectors, name_vectors):
            insert_rows.append((
                row[0],
                row[1],
                row[8],
                row[6],
                row[7],
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
    conn.close()
    print(f"Inserted POS embeddings: {len(insert_rows)}")


def run_fuzzy_match():
    conn = connect()
    cursor = conn.cursor()
    schema = schema_name()
    run_id = os.environ.get("MATCH_RUN_ID") or f"workflow-{uuid.uuid4().hex[:12]}"
    limit = int(os.environ.get("MATCH_LEAD_LIMIT", "1000000"))

    cursor.execute(
        f"""
        WITH lead_batch AS (
            SELECT *
            FROM "{schema}"."leads_embeddings"
            WHERE combined_embedding IS NOT NULL
            ORDER BY lead_id
            LIMIT %s
        ),
        candidates AS (
            SELECT
                l.lead_id,
                s.pos_id,
                l.warehouse_number,
                (1 - (s.combined_embedding <=> l.combined_embedding)) * 100 AS combined_field_score,
                (1 - (s.address_embedding <=> l.address_embedding)) * 100 AS full_address_score,
                (1 - COALESCE(NULLIF(s.name_embedding <=> l.name_embedding, 'NaN'::float), 1)) * 100 AS business_name_score
            FROM lead_batch l
            CROSS JOIN LATERAL (
                SELECT *
                FROM "{schema}"."pos_embeddings" s
                WHERE s.combined_embedding IS NOT NULL
                  AND (l.warehouse_number IS NULL OR s.warehouse_number = l.warehouse_number)
                  AND (
                      s.fiscal_year > l.fiscal_year
                      OR (s.fiscal_year = l.fiscal_year AND s.fiscal_period >= l.fiscal_period)
                  )
                ORDER BY s.combined_embedding <=> l.combined_embedding
                LIMIT 20
            ) s
        ),
        scored AS (
            SELECT
                lead_id,
                pos_id,
                warehouse_number,
                combined_field_score,
                full_address_score,
                business_name_score,
                (
                    combined_field_score
                    + 4 * full_address_score
                    + 3 * business_name_score
                ) / 8 AS final_score
            FROM candidates
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY final_score DESC) AS rank
            FROM scored
            WHERE final_score >= %s
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
            'Fuzzy',
            final_score,
            combined_field_score,
            full_address_score,
            business_name_score,
            '(combined + 4*address + 3*name)/8',
            %s,
            CURRENT_TIMESTAMP
        FROM ranked
        WHERE rank = 1
        ON CONFLICT (match_run_id, lead_id, pos_id) DO NOTHING;
        """,
        (limit, float(os.environ.get("MATCH_MIN_SCORE", "80")), run_id, EMBEDDING_MODEL),
    )
    inserted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Match run: {run_id}")
    print(f"Inserted match decision rows: {inserted}")


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
        choices=("summary", "lead-embeddings", "pos-embeddings", "fuzzy-match"),
    )
    args = parser.parse_args()

    if args.task == "summary":
        print_summary()
    elif args.task == "lead-embeddings":
        generate_lead_embeddings()
    elif args.task == "pos-embeddings":
        generate_pos_embeddings()
    elif args.task == "fuzzy-match":
        run_fuzzy_match()
    else:
        raise AssertionError(args.task)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
