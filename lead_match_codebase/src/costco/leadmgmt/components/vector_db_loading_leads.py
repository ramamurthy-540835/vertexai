import os
import concurrent.futures
import json
import pandas as pd
from datetime import datetime
from pgvector.sqlalchemy import Vector
import sqlalchemy
import time
import random
from google import genai
from google.genai import types
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Column, Integer, Text, DateTime
import numpy as np
from sqlalchemy.orm import declarative_base, sessionmaker
from concurrent.futures import ThreadPoolExecutor, as_completed
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from costco.leadmgmt.database.DBUtil import load_data_from_cloudsql
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "5"))
PROJECT_ID = os.environ.get("PROJECT_ID")

def load_embedding_config():
    """Load embedding model and dimension from business rules JSON."""
    env_path = os.environ.get("LEAD_POS_RULES_PATH")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
    paths = [
        env_path,
        os.path.join(repo_root, "lead_match_runtime/lead_to_pos_match_rules.json"),
        os.path.join(os.getcwd(), "lead_match_runtime/lead_to_pos_match_rules.json"),
    ]
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                rules = json.load(f)
                return {
                    "model": rules.get("embeddings", {}).get("model", "gemini-embedding-001"),
                    "task_type": rules.get("embeddings", {}).get("task_type", "SEMANTIC_SIMILARITY"),
                    "output_dimensionality": rules.get("embeddings", {}).get("output_dimensionality", 768),
                }
        except Exception as e:
            pass
    return {
        "model": "gemini-embedding-001",
        "task_type": "SEMANTIC_SIMILARITY",
        "output_dimensionality": 768,
    }

embedding_config = load_embedding_config()
MODEL_NAME = embedding_config["model"]
TASK_TYPE = embedding_config["task_type"]
OUTPUT_DIMENSIONALITY = embedding_config["output_dimensionality"]

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
    http_options=types.HttpOptions(api_version='v1')
)

Base = declarative_base()


def l2_normalize(vector):
    arr = np.array(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return None
    return (arr / norm).tolist()


def get_lead_class(insert_lead_table_name, schema_name):
    class Lead(Base):
        __tablename__ = insert_lead_table_name
        __table_args__ = {"schema": schema_name}
        lead_id = Column(Text, primary_key=True)
        combined_field = Column(Text)
        combined_embedding = Column(Vector(OUTPUT_DIMENSIONALITY))
        updated_date = Column(DateTime)
        warehouse_number = Column(Integer)
        load_date = Column(DateTime)
        business_address = Column(Text)
        business_name = Column(Text)
        address_embedding = Column(Vector(OUTPUT_DIMENSIONALITY))
        name_embedding = Column(Vector(OUTPUT_DIMENSIONALITY))

    return Lead


def data_extraction(leads_df, leads_insert_id, leads_update_id):
    # type confirmation
    leads_df['lead_id'] = leads_df['lead_id'].astype(str)
    leads_insert_id['lead_id'] = leads_insert_id['lead_id'].astype(str)
    leads_update_id['lead_id'] = leads_update_id['lead_id'].astype(str)

    # leads inserts
    leads_insert_df = leads_df[leads_df['lead_id'].isin(leads_insert_id['lead_id'])]
    print(len(leads_insert_df))

    # leads updates
    leads_update_df = leads_df[leads_df['lead_id'].isin(leads_update_id['lead_id'])]
    print(len(leads_update_df))

    return leads_insert_df, leads_update_df


def batch_embedding(text_list, max_retries=5, base_delay=1.0, max_delay=60.0):
    """Generate embeddings for a batch of texts"""
    text_list = [str(text).strip() for text in text_list]
    if not any(text_list):
        return [None] * len(text_list)

    for attempt in range(max_retries):
        try:
            response = client.models.embed_content(
                model=MODEL_NAME,
                contents=text_list,
                config=types.EmbedContentConfig(
                    task_type=TASK_TYPE,
                    output_dimensionality=OUTPUT_DIMENSIONALITY,
                )
            )
            return [l2_normalize(e.values) for e in response.embeddings]
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = (
                "429" in error_str or
                "resource exhausted" in error_str or
                "quota" in error_str or
                "rate limit" in error_str
            )
            if is_rate_limit and attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                print(f"Rate limit hit (attempt {attempt + 1}/{max_retries}). Retrying in {delay:.2f}s...")
                time.sleep(delay)
            else:
                print(f"Batch failed after {attempt + 1} attempt(s): {str(e)}")
                return [None] * len(text_list)
    return [None] * len(text_list)

# ── FIX 2: added ramp-up stagger logic to match POS script ───────────────────
# prevents all batches submitting simultaneously and hitting rate limits
def process_in_batch(df, embedding_column_name, column_name):
    if embedding_column_name not in df.columns:
        df[embedding_column_name] = None

    df[column_name] = df[column_name].fillna('')
    df_to_embed = df[df[column_name].str.strip() != ''].copy()

    batch_size = 250
    batches = [
        df_to_embed[column_name].iloc[i:i + batch_size].tolist()
        for i in range(0, len(df_to_embed), batch_size)
    ]

    overall_start = time.time()
    results = [None] * len(batches)

    RAMP_STAGES = [
        (5,   0.5),   # first 5 batches  → 0.5s gap between submits
        (10,  0.2),   # next 10 batches  → 0.2s gap
        (999, 0.05),  # rest             → 0.05s gap (near full speed)
    ]

    def get_delay(batch_idx):
        count = 0
        for stage_size, delay in RAMP_STAGES:
            count += stage_size
            if batch_idx < count:
                return delay
        return 0.05

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_batch = {}

        for idx, batch in enumerate(batches):
            time.sleep(get_delay(idx))
            future = executor.submit(batch_embedding, batch)
            future_to_batch[future] = (idx, time.time())

        for future in as_completed(future_to_batch):
            idx, start_time = future_to_batch[future]
            try:
                embeddings = future.result()
                duration = time.time() - start_time
                print(f"Batch {idx} took {duration:.2f} seconds")
                if embeddings is None:
                    print(f"Batch {idx} exhausted retries")
                    results[idx] = [None] * len(batches[idx])
                else:
                    results[idx] = embeddings
            except Exception as exc:
                print(f'Batch {idx} generated an exception: {exc}')
                results[idx] = [None] * len(batches[idx])

    overall_end = time.time()
    print(f"\nTotal processing time: {overall_end - overall_start:.2f} seconds")

    # Flatten and assign
    all_embeddings = [emb for batch in results if batch for emb in batch]
    df_to_embed[embedding_column_name] = all_embeddings

    df.update(df_to_embed)

    df[embedding_column_name] = df[embedding_column_name].apply(
        lambda x: x if isinstance(x, list) and len(x) == OUTPUT_DIMENSIONALITY else None
    )

    return df[embedding_column_name].to_list()


# ── FIX 3: added try/except + rollback + guaranteed session close ─────────────
def update_operation_leads(engine, dataframe, Lead):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        for _, row in dataframe.iterrows():
            session.query(Lead).filter(Lead.lead_id == row['lead_id']).update(
                {
                    "combined_embedding": row['combined_embedding'],
                    "updated_date":       row['updated_date'],
                    "combined_field":     row['combined_field'],
                    "address_embedding":  row['address_embedding'],
                    "name_embedding":     row['name_embedding'],
                    "business_address":   row['business_address'],
                    "business_name":      row['business_name'],
                }
            )
        session.commit()
    except Exception as e:
        session.rollback()    # ✅ rollback on any failure
        print(f"DB update failed: {e}")
        raise
    finally:
        session.close()       # ✅ always closes regardless of success or failure


# ── FIX 4: added try/except — DB errors now logged before re-raising ──────────
def insert_operation_leads(engine, table_name, schema_name, data_frame):
    try:
        data_frame.to_sql(
            table_name, con=engine, if_exists='append', index=False,
            schema=schema_name, method='multi', chunksize=1000,
            dtype={
                "combined_embedding": Vector(OUTPUT_DIMENSIONALITY),
                "address_embedding":  Vector(OUTPUT_DIMENSIONALITY),
                "name_embedding":     Vector(OUTPUT_DIMENSIONALITY),
                "updated_date":       TIMESTAMP,
            }
        )
    except Exception as e:
        print(f"DB insert failed for table {schema_name}.{table_name}: {e}")
        raise


def embedding_generation(file_leads: str, config_file_path: str):
    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query

    # fiscal information
    fiscal_info = get_costco_fiscal_info()

    # query
    query_leads_insert_ids = f'''{query_config.query_leads_insert_ids} >= {fiscal_info["fiscal_year"] - 1}'''
    query_leads_update_ids = f'''{query_config.query_leads_update_ids} >= {fiscal_info["fiscal_year"] - 1}'''

    # database detail
    schema_name = db_config.schema_name
    insert_lead_table_name = db_config.insert_lead_table_name
    insert_pos_table_name = db_config.insert_pos_table_name

    # engine
    engine = db_config.get_engine()

    leads_insert_id = load_data_from_cloudsql(
        query_input=query_leads_insert_ids,
        engine=engine)

    leads_df = load_file_from_gcs(file_leads)

    leads_update_id = load_data_from_cloudsql(
        query_input=query_leads_update_ids,
        engine=engine)

    leads_df.rename(columns={"COMBINED_FIELD": "combined_field", "FULL_ADDRESS": "full_address"}, inplace=True)

    leads_df = leads_df[
        ~(
            leads_df['address_line_one'].isna() &
            leads_df['business_name'].isna()
        )
    ]

    leads_df.drop(
        columns=['membership_number', 'first_name', 'last_name', 'city', 'state', 'zip_code',
                 'phone', 'email', 'CUSTOMER_NAME', 'address_line_one', 'address_line_two'],
        inplace=True,
    )

    leads_insert_df, leads_update_df = data_extraction(leads_df, leads_insert_id, leads_update_id)

    if not leads_insert_df.empty:
        chunk_size = 20000
        for i in range(0, len(leads_insert_df), chunk_size):
            chunk_df = leads_insert_df.iloc[i:i + chunk_size].copy()

            chunk_df['combined_embedding'] = process_in_batch(chunk_df, 'combined_embedding', 'combined_field')
            chunk_df['address_embedding']  = process_in_batch(chunk_df, 'address_embedding',  'full_address')
            chunk_df['name_embedding']     = process_in_batch(chunk_df, 'name_embedding',     'business_name')

            chunk_df = chunk_df.rename(columns={
                "full_address":            "business_address",
                "fiscal_year_lead":        "fiscal_year",
                "fiscal_period_lead":      "fiscal_period",
            })

            chunk_df['updated_date'] = pd.to_datetime(datetime.now())

            chunk_df = chunk_df[[
                'warehouse_number', 'lead_id', 'updated_date',
                'combined_field', 'business_address', 'business_name',
                'combined_embedding', 'address_embedding', 'name_embedding',
                'fiscal_year', 'fiscal_period',
            ]]

            insert_operation_leads(engine, insert_lead_table_name, schema_name, chunk_df)

    if not leads_update_df.empty:
        chunk_size = 20000

        # ── FIX 5: Lead class created once outside the loop ──────────────────
        # previously created on every iteration causing SAWarning: Table already defined
        Lead = get_lead_class(insert_lead_table_name, schema_name)

        for i in range(0, len(leads_update_df), chunk_size):
            chunk_df = leads_update_df.iloc[i:i + chunk_size].copy()

            chunk_df['combined_embedding'] = process_in_batch(chunk_df, 'combined_embedding', 'combined_field')
            chunk_df['address_embedding']  = process_in_batch(chunk_df, 'address_embedding',  'full_address')
            chunk_df['name_embedding']     = process_in_batch(chunk_df, 'name_embedding',     'business_name')

            chunk_df = chunk_df.rename(columns={
                "full_address":       "business_address",
                "fiscal_year_lead":   "fiscal_year",
                "fiscal_period_lead": "fiscal_period",
            })

            chunk_df['updated_date'] = pd.to_datetime(datetime.now())

            chunk_df = chunk_df[[
                'warehouse_number', 'lead_id', 'updated_date',
                'combined_field', 'business_address', 'business_name',
                'combined_embedding', 'address_embedding', 'name_embedding',
                'fiscal_year', 'fiscal_period',
            ]]

            update_operation_leads(engine, chunk_df, Lead)
