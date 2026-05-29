import os
import concurrent.futures
import pandas as pd
from datetime import datetime
import time
import random
from pgvector.sqlalchemy import Vector
import sqlalchemy
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

# Get the base image from environment variables
BASE_IMAGE = os.environ.get("KFP_CUSTOM_IMAGE")
MAX_WORKERS = os.environ.get("MAX_WORKERS")
PROJECT_ID = os.environ.get("PROJECT_ID")


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1"
)

def data_extraction(transaction_df, pos_insert_id):
    # type confirmation
    pos_insert_id['pos_id'] = pos_insert_id['pos_id'].astype(str)
    transaction_df['pos_id'] = transaction_df['pos_id'].astype(str)
    transaction_df['account_number'] = transaction_df['account_number'].astype(int)

    # transaction inserts
    transaction_insert_df = transaction_df[transaction_df['pos_id'].isin(pos_insert_id['pos_id'])]
    print(len(transaction_insert_df))

    return transaction_insert_df


def batch_embedding(text_list, max_retries=5, base_delay=1.0, max_delay=60.0):
    """Generate embeddings for a batch of texts"""
    for attempt in range(max_retries):
        try:
            response = client.models.embed_content(
                model="text-embedding-005",
                contents=text_list,
                config=types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY"
                )
            )
            return [e.values for e in response.embeddings]
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
                return None
    return None

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

    all_embeddings = []
    overall_start = time.time()

    results = [None] * len(batches)

    RAMP_STAGES = [
        (5,  0.5),   #  First 5 batches  → 0.5s gap between submits
        (10, 0.2),   # Next 10 batches  → 0.2s gap
        (999, 0.05), # Rest             → 0.05s gap (near full speed)
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
            #  Stagger submission 
            time.sleep(get_delay(idx))
            future = executor.submit(batch_embedding, batch)
            future_to_batch[future] = (idx, time.time())

        for future in as_completed(future_to_batch):
            idx, start_time = future_to_batch[future]
            
            try:
                embeddings = future.result()
                end_time = time.time()  # ⏱ batch end
                duration = end_time - start_time

                print(f"Batch {idx} took {duration:.2f} seconds")
                if embeddings is None:
                    print(f"Batch {idx} exhausted retries — using zero fallback")
                    results[idx] = [[0] * 768] * len(batches[idx])
                else:      
                    results[idx] = embeddings
            except Exception as exc:
                print(f'Batch {idx} generated an exception: {exc}')
                results[idx] = [[0] * 768] * len(batches[idx])  # default fallback
    overall_end = time.time()
    print(f"\nTotal processing time: {overall_end - overall_start:.2f} seconds")

    # Flatten and assign
    all_embeddings = [emb for batch in results if batch for emb in batch]
    df_to_embed[embedding_column_name] = all_embeddings

    df.update(df_to_embed)

    df[embedding_column_name] = df[embedding_column_name].apply(
        lambda x: x if isinstance(x, list) and len(x) == 768 else [0] * 768
    )

    return df[embedding_column_name].to_list()


def insert_operation_transaction(engine, table_name, schema_name, data_frame):
    try:
        data_frame.to_sql(
            table_name, con=engine, if_exists='append', index=False,
            schema=schema_name, method='multi', chunksize=1000,
            dtype={"combined_embedding": Vector(768),
                   "address_embedding": Vector(768),
                   "name_embedding": Vector(768),
                   "load_date": TIMESTAMP}
        )
    except Exception as e:
        print(f"DB insert failed for table {schema_name}.{table_name}: {e}")
        raise   # re-raise so the caller knows it failed

def embed_chunk(chunk_df):

    chunk_df['combined_embedding'] = process_in_batch(chunk_df, 'combined_embedding',
                                                                        'combined_field')  # column name to  be changed
    chunk_df['address_embedding'] = process_in_batch(chunk_df, 'address_embedding',
                                                                        'full_address')  # column name to  be changed
    chunk_df['name_embedding'] = process_in_batch(chunk_df, 'name_embedding',
                                                                    'business_name')  # column name to  be changed

    return chunk_df

def embedding_generation(file_pos: str, config_file_path: str):
    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query

    # fiscal information
    fiscal_info = get_costco_fiscal_info()

    # query

    query_pos_inserts_ids = f'''{query_config.query_pos_inserts_ids} = {fiscal_info["fiscal_year"]}'''

    # database detail
    schema_name = db_config.schema_name
    insert_lead_table_name = db_config.insert_lead_table_name
    insert_pos_table_name = db_config.insert_pos_table_name

    # engine
    engine = db_config.get_engine()

    pos_insert_id = load_data_from_cloudsql(  # pos records in database
        query_input=query_pos_inserts_ids,
        engine=engine)

    transaction_df = load_file_from_gcs(file_pos)

    transaction_df.rename(columns={"COMBINED_FIELD": "combined_field", "FULL_ADDRESS": "full_address", }, inplace=True)

    transaction_df = transaction_df[
        ~(
                transaction_df['address_line_one'].isna() &
                transaction_df['business_name'].isna()
        )
    ]

    transaction_df.drop(
        columns=['membership_number', 'first_name', 'last_name', 'city', 'state', 'zip_code', 'phone', 'email',
                 'CUSTOMER_NAME', 'address_line_one', 'address_line_two', 'updated_date'], inplace=True)

    transaction_insert_df = data_extraction(transaction_df, pos_insert_id)

    if not transaction_insert_df.empty:
        chunk_size = 20000
        # Assign embeddings to respective columns in the dataframe
        for i in range(0, len(transaction_insert_df), chunk_size):

            chunk_df = transaction_insert_df.iloc[i:i+chunk_size].copy()
            chunk_df = embed_chunk(chunk_df)
            chunk_df['load_date'] = pd.to_datetime(datetime.now())

            chunk_df = chunk_df.rename(
                columns={"full_address": "business_address", "fiscal_year_transaction": "fiscal_year",
                        "fiscal_period_transaction": "fiscal_period"})

            insert_operation_transaction(engine, insert_pos_table_name, schema_name, chunk_df)




