import pandas as pd
from datetime import datetime
import time
import random
import uuid
import numpy as np
from pgvector.sqlalchemy import Vector
from google import genai
from google.genai.types import EmbedContentConfig, HttpOptions
from sqlalchemy.dialects.postgresql import TIMESTAMP
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text, bindparam

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from costco.leadmgmt.database.DBUtil import load_data_from_cloudsql
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


# ============================================================
# CONFIGURATION
# ============================================================
MODEL_NAME = "gemini-embedding-001"
TASK_TYPE = "SEMANTIC_SIMILARITY"
OUTPUT_DIMENSIONALITY = 768
CHUNK_SIZE = 2000
MAX_WORKERS = 5
INTERNAL_BATCH_SIZE = 25


def l2_normalize(vector):
    arr = np.array(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return None
    return (arr / norm).tolist()


def create_checkpoint_table(engine, schema_name):
    create_sql = text(f"""
        CREATE TABLE IF NOT EXISTS {schema_name}.embedding_job_checkpoint (
            run_id TEXT NOT NULL,
            job_name TEXT NOT NULL,
            source_file TEXT,
            model_name TEXT,
            task_type TEXT,
            output_dimensionality INTEGER,
            chunk_index INTEGER NOT NULL,
            chunk_start_pos_id TEXT,
            chunk_end_pos_id TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            rows_attempted INTEGER DEFAULT 0,
            rows_inserted INTEGER DEFAULT 0,
            rows_failed INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            error_message TEXT,
            PRIMARY KEY (run_id, chunk_index)
        );
    """)
    with engine.connect() as conn:
        conn.execute(create_sql)
        conn.commit()


def generate_run_id(job_name: str) -> str:
    return f"{job_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def get_checkpoint_status(engine, schema_name, run_id: str, chunk_index: int):
    query = text(f"""
        SELECT status FROM {schema_name}.embedding_job_checkpoint 
        WHERE run_id = :run_id AND chunk_index = :chunk_index
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"run_id": run_id, "chunk_index": chunk_index}).fetchone()
        return result[0] if result else None


def mark_chunk_status(engine, schema_name, run_data: dict):
    defaults = {
        "run_id": None, "job_name": None, "source_file": None,
        "model_name": MODEL_NAME, "task_type": TASK_TYPE,
        "output_dimensionality": OUTPUT_DIMENSIONALITY,
        "chunk_index": None, "chunk_start_pos_id": None, "chunk_end_pos_id": None,
        "status": "PENDING", "rows_attempted": 0, "rows_inserted": 0,
        "rows_failed": 0, "started_at": None, "completed_at": None,
        "error_message": None,
    }
    payload = {**defaults, **run_data}

    upsert_sql = text(f"""
        INSERT INTO {schema_name}.embedding_job_checkpoint 
        (run_id, job_name, source_file, model_name, task_type, output_dimensionality,
         chunk_index, chunk_start_pos_id, chunk_end_pos_id, status,
         rows_attempted, rows_inserted, rows_failed, started_at, completed_at, error_message)
        VALUES 
        (:run_id, :job_name, :source_file, :model_name, :task_type, :output_dimensionality,
         :chunk_index, :chunk_start_pos_id, :chunk_end_pos_id, :status,
         :rows_attempted, :rows_inserted, :rows_failed, :started_at, :completed_at, :error_message)
        ON CONFLICT (run_id, chunk_index) 
        DO UPDATE SET 
            status = EXCLUDED.status,
            rows_attempted = EXCLUDED.rows_attempted,
            rows_inserted = EXCLUDED.rows_inserted,
            rows_failed = EXCLUDED.rows_failed,
            completed_at = EXCLUDED.completed_at,
            error_message = EXCLUDED.error_message;
    """)
    with engine.connect() as conn:
        conn.execute(upsert_sql, payload)
        conn.commit()


def batch_embedding(client, text_list, max_retries=6, base_delay=1.5, max_delay=90.0):
    text_list = [str(text).strip() for text in text_list]
    if not any(text_list):
        return [None] * len(text_list)

    for attempt in range(max_retries):
        try:
            response = client.models.embed_content(
                model=MODEL_NAME,
                contents=text_list,
                config=EmbedContentConfig(
                    task_type=TASK_TYPE,
                    output_dimensionality=OUTPUT_DIMENSIONALITY
                )
            )
            return [l2_normalize(embedding.values) for embedding in response.embeddings]
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = any(x in error_str for x in ["429", "resource exhausted", "quota", "rate limit"])
            if is_rate_limit and attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1.5), max_delay)
                print(f"[Rate Limit] Retrying after {delay:.1f}s (Attempt {attempt + 1})")
                time.sleep(delay)
            else:
                print(f"[ERROR] Embedding batch failed after {attempt + 1} attempts: {str(e)}")
                return [None] * len(text_list)
    return [None] * len(text_list)


def process_in_batch(client, df, embedding_column_name, column_name, max_workers):
    if embedding_column_name not in df.columns:
        df[embedding_column_name] = None

    df[column_name] = df[column_name].fillna('').astype(str)
    df_to_embed = df[df[column_name].str.strip() != ''].copy()

    if df_to_embed.empty:
        return df

    batches = [
        df_to_embed[column_name].iloc[i:i + INTERNAL_BATCH_SIZE].tolist()
        for i in range(0, len(df_to_embed), INTERNAL_BATCH_SIZE)
    ]

    results = [None] * len(batches)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_batch = {
            executor.submit(batch_embedding, client, batch): idx
            for idx, batch in enumerate(batches)
        }

        for future in as_completed(future_to_batch):
            idx = future_to_batch[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                print(f"Batch {idx} failed: {exc}")
                results[idx] = [None] * len(batches[idx])

    all_embeddings = [emb for batch in results if batch for emb in batch]

    if len(all_embeddings) != len(df_to_embed):
        raise ValueError(
            f"Embedding count mismatch for {embedding_column_name}: "
            f"expected {len(df_to_embed)}, got {len(all_embeddings)}"
        )

    df.loc[df_to_embed.index, embedding_column_name] = pd.Series(
        all_embeddings, index=df_to_embed.index, dtype=object
    )
    return df


def embed_chunk(client, chunk_df, max_workers):
    chunk_df = process_in_batch(client, chunk_df, 'combined_embedding', 'combined_field', max_workers)
    chunk_df = process_in_batch(client, chunk_df, 'address_embedding', 'full_address', max_workers)
    chunk_df = process_in_batch(client, chunk_df, 'name_embedding', 'business_name', max_workers)
    return chunk_df


def delete_existing_embeddings(engine, schema_name, table_name, pos_ids):
    if not pos_ids:
        return
    delete_sql = text(f"""
        DELETE FROM {schema_name}.{table_name}
        WHERE pos_id IN :pos_ids
    """).bindparams(bindparam("pos_ids", expanding=True))

    with engine.connect() as conn:
        conn.execute(delete_sql, {"pos_ids": list(pos_ids)})
        conn.commit()


def insert_operation_transaction(engine, schema_name, table_name, data_frame):
    data_frame.to_sql(
        table_name, con=engine, if_exists='append', index=False,
        schema=schema_name, method='multi', chunksize=1000,
        dtype={
            "combined_embedding": Vector(OUTPUT_DIMENSIONALITY),
            "address_embedding": Vector(OUTPUT_DIMENSIONALITY),
            "name_embedding": Vector(OUTPUT_DIMENSIONALITY),
            "load_date": TIMESTAMP
        }
    )


def embedding_generation(file_pos: str, config_file_path: str, project_id: str,
                         job_name: str = "pos_embedding_job", run_id: str = None):

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="us-central1",
        http_options=HttpOptions(api_version='v1')
    )

    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    engine = db_config.get_engine()
    schema_name = db_config.schema_name

    create_checkpoint_table(engine, schema_name)

    if run_id is None:
        run_id = generate_run_id(job_name)

    print(f"\n=== Starting Embedding Job ===")
    print(f"Run ID: {run_id} | Model: {MODEL_NAME}")

    fiscal_info = get_costco_fiscal_info()
    query_pos_inserts_ids = f"{job_config.match_query.query_pos_inserts_ids} = {fiscal_info['fiscal_year']}"
    pos_insert_id = load_data_from_cloudsql(query_input=query_pos_inserts_ids, engine=engine)
    transaction_df = load_file_from_gcs(file_pos)

    # Preprocessing
    transaction_df.rename(columns={"COMBINED_FIELD": "combined_field", "FULL_ADDRESS": "full_address"}, inplace=True)
    transaction_df = transaction_df[~(transaction_df['address_line_one'].isna() & transaction_df['business_name'].isna())]
    pos_insert_id['pos_id'] = pos_insert_id['pos_id'].astype(str)
    transaction_df['pos_id'] = transaction_df['pos_id'].astype(str)
    transaction_insert_df = transaction_df[transaction_df['pos_id'].isin(pos_insert_id['pos_id'])]

    if transaction_insert_df.empty:
        print("No records to process.")
        return

    total_chunks = (len(transaction_insert_df) + CHUNK_SIZE - 1) // CHUNK_SIZE

    for chunk_index in range(total_chunks):
        status = get_checkpoint_status(engine, schema_name, run_id, chunk_index)
        if status == "SUCCESS":
            print(f"Skipping chunk {chunk_index + 1}/{total_chunks} (already SUCCESS)")
            continue

        start_idx = chunk_index * CHUNK_SIZE
        end_idx = min(start_idx + CHUNK_SIZE, len(transaction_insert_df))
        chunk_df = transaction_insert_df.iloc[start_idx:end_idx].copy()

        print(f"\nProcessing chunk {chunk_index + 1}/{total_chunks} | Records: {len(chunk_df)}")

        run_data = {
            "run_id": run_id,
            "job_name": job_name,
            "source_file": file_pos,
            "chunk_index": chunk_index,
            "chunk_start_pos_id": str(chunk_df['pos_id'].iloc[0]),
            "chunk_end_pos_id": str(chunk_df['pos_id'].iloc[-1]),
            "rows_attempted": len(chunk_df),
            "started_at": datetime.now()
        }

        mark_chunk_status(engine, schema_name, {**run_data, "status": "RUNNING"})

        try:
            chunk_df = embed_chunk(client, chunk_df, MAX_WORKERS)
            chunk_df_final = chunk_df.dropna(
                subset=["combined_embedding", "address_embedding", "name_embedding"]
            )

            rows_inserted = len(chunk_df_final)
            rows_failed = len(chunk_df) - rows_inserted
            final_status = "SUCCESS" if rows_failed == 0 else "PARTIAL_SUCCESS"

            if rows_inserted > 0:
                # Rename + load_date BEFORE filtering
                chunk_df_final = chunk_df_final.copy()
                chunk_df_final["load_date"] = pd.to_datetime(datetime.now())

                chunk_df_final = chunk_df_final.rename(columns={
                    "full_address": "business_address",
                    "fiscal_year_transaction": "fiscal_year",
                    "fiscal_period_transaction": "fiscal_period"
                })

                # Filter only target columns
                target_cols = [
                    "pos_id", "account_number", "business_name", "business_address",
                    "combined_field", "warehouse_number", "fiscal_year", "fiscal_period", "week",
                    "combined_embedding", "address_embedding", "name_embedding", "load_date"
                ]
                chunk_df_final = chunk_df_final[
                    [col for col in target_cols if col in chunk_df_final.columns]
                ]

                # Delete existing rows for these pos_ids (idempotency)
                delete_existing_embeddings(
                    engine, schema_name, db_config.insert_pos_table_name,
                    chunk_df_final["pos_id"].tolist()
                )

                insert_operation_transaction(
                    engine, schema_name, db_config.insert_pos_table_name, chunk_df_final
                )

            mark_chunk_status(engine, schema_name, {
                **run_data,
                "status": final_status,
                "rows_inserted": rows_inserted,
                "rows_failed": rows_failed,
                "completed_at": datetime.now()
            })

            print(f"Chunk {chunk_index + 1} completed | Status: {final_status} | Inserted: {rows_inserted}, Failed: {rows_failed}")

        except Exception as e:
            print(f"Chunk {chunk_index + 1} FAILED: {e}")
            mark_chunk_status(engine, schema_name, {
                **run_data,
                "status": "FAILED",
                "error_message": str(e),
                "completed_at": datetime.now()
            })
            raise

    print(f"\n=== Job Completed ===")
    print(f"Run ID: {run_id}")
