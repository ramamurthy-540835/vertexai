import os
import concurrent.futures
import pandas as pd
from datetime import datetime
from pgvector.sqlalchemy import Vector
import sqlalchemy
from vertexai.preview.language_models import TextEmbeddingModel, TextEmbeddingInput
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

model = TextEmbeddingModel.from_pretrained("text-embedding-005")
Base = declarative_base()


def get_lead_class(insert_lead_table_name, schema_name):
    class Lead(Base):
        __tablename__ = insert_lead_table_name
        __table_args__ = {"schema": schema_name}  # Specify schema name
        lead_id = Column(Text, primary_key=True)
        combined_field = Column(Text)
        combined_embedding = Column(Vector(768))  # Adjust to your embedding size
        updated_date = Column(DateTime)
        warehouse_number = Column(Integer)
        load_date = Column(DateTime)
        business_address = Column(Text)
        business_name = Column(Text)
        address_embedding = Column(Vector(768))
        name_embedding = Column(Vector(768))

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


def batch_embedding(text_list):
    """Generate embeddings for a batch of texts"""
    try:

        embedding = []
        text_list = [TextEmbeddingInput(text, 'SEMANTIC_SIMILARITY') for text in text_list]
        embeddings = model.get_embeddings(text_list)
        for i in range(0, len(embeddings)):
            embedding.append(embeddings[i].values)
        # return [embedding.values for embedding in embeddings]
        return embedding
    except Exception as e:
        print(f"Batch Error occurred: {str(e)}")


def process_in_batch(df, embedding_column_name, column_name):
    if embedding_column_name not in df.columns:
        df[embedding_column_name] = None

    df[column_name] = df[column_name].fillna('')
    df_to_embed = df[df[column_name].str.strip() != ''].copy()

    batch_size = 200
    batches = [
        df_to_embed[column_name].iloc[i:i + batch_size].tolist()
        for i in range(0, len(df_to_embed), batch_size)
    ]

    all_embeddings = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_batch = {executor.submit(batch_embedding, batch): idx for idx, batch in enumerate(batches)}
        results = [None] * len(batches)

        for future in as_completed(future_to_batch):
            idx = future_to_batch[future]
            try:
                embeddings = future.result()
                results[idx] = embeddings
            except Exception as exc:
                print(f'Batch {idx} generated an exception: {exc}')
                results[idx] = [[0] * 768] * len(batches[idx])  # default fallback

    # Flatten and assign
    all_embeddings = [emb for batch in results if batch for emb in batch]
    df_to_embed[embedding_column_name] = all_embeddings

    df.update(df_to_embed)
    df[embedding_column_name] = df[embedding_column_name].apply(
        lambda x: x if isinstance(x, list) and len(x) == 768 else [0] * 768
    )

    return df[embedding_column_name].to_list()


def update_operation_leads(engine, dataframe, Lead):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    for _, row in dataframe.iterrows():
        lead_id = row['lead_id']
        updated_date = row['updated_date']
        combined_field = row['combined_field']
        combined_embedding = row['combined_embedding']
        address_embedding = row['address_embedding']
        name_embedding = row['name_embedding']
        business_address = row['business_address']
        business_name = row['business_name']

        session.query(Lead).filter(Lead.lead_id == lead_id).update(
            {"combined_embedding": combined_embedding, "updated_date": updated_date, "combined_field": combined_field,
             "address_embedding": address_embedding, "name_embedding": name_embedding,
             "business_address": business_address, "business_name": business_name})

    session.commit()
    session.close()


def insert_operation_leads(engine, table_name, schema_name, data_frame):
    data_frame.to_sql(table_name, con=engine, if_exists='append', index=False, schema=schema_name, method='multi',
                      chunksize=1000, dtype={"combined_embedding": Vector(768), "address_embedding": Vector(768),
                                             "name_embedding": Vector(768), "updated_date": TIMESTAMP})


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

    leads_insert_id = load_data_from_cloudsql(  # leads records in database
        query_input=query_leads_insert_ids,
        engine=engine)

    leads_df = load_file_from_gcs(file_leads)

    leads_update_id = load_data_from_cloudsql(  # leads records in database
        query_input=query_leads_update_ids,
        engine=engine)

    leads_df.rename(columns={"COMBINED_FIELD": "combined_field", "FULL_ADDRESS": "full_address", }, inplace=True)

    leads_df = leads_df[
        ~(
                leads_df['address_line_one'].isna() &
                leads_df['business_name'].isna()
        )
    ]

    leads_df.drop(columns=['membership_number', 'first_name', 'last_name', 'city', 'state', 'zip_code',
                           'phone', 'email', 'CUSTOMER_NAME', 'address_line_one', 'address_line_two'], inplace=True)

    leads_insert_df, leads_update_df = data_extraction(leads_df, leads_insert_id, leads_update_id)

    # Check if dataframes are not empty before proceeding
    if not leads_insert_df.empty:
        # Assign embeddings to respective columns in the dataframe
        print("Rows in leads_insert_df:", len(leads_insert_df))
        problem_rows = leads_insert_df[
        leads_insert_df['combined_field'].isna() |
        (leads_insert_df['combined_field'].astype(str).str.strip() == '')
    ]

        print("Rows with empty combined_field:", len(problem_rows))
        print(problem_rows[['lead_id', 'combined_field']].head(20))
        embeddings = process_in_batch(leads_insert_df, 'combined_embedding',
                                                                 'combined_field')  # column name to  be changed
        print("Embeddings generated:", len(embeddings))
        leads_insert_df['combined_embedding'] = embeddings                                                         
        leads_insert_df['address_embedding'] = process_in_batch(leads_insert_df, 'address_embedding',
                                                                'full_address')  # column name to  be changed
        leads_insert_df['name_embedding'] = process_in_batch(leads_insert_df, 'name_embedding',
                                                             'business_name')  # column name to  be changed

        # Select columns to be inserted into the database, including embeddings for each field
        leads_insert_df = leads_insert_df.rename(
            columns={"full_address": "business_address", "fiscal_year_lead": "fiscal_year",
                     "fiscal_period_lead": "fiscal_period"})

        leads_insert_df['updated_date'] = pd.to_datetime(datetime.now())

        leads_insert_df = leads_insert_df[['warehouse_number', 'lead_id', 'updated_date',
                                           'combined_field', 'business_address', 'business_name',
                                           'combined_embedding', 'address_embedding',
                                           'name_embedding', 'fiscal_year', 'fiscal_period']]

        # Insert the data into the target table
        insert_operation_leads(engine, insert_lead_table_name, schema_name, leads_insert_df)

    if not leads_update_df.empty:
        # Assign embeddings to respective columns in the dataframe
        leads_update_df['combined_embedding'] = process_in_batch(leads_update_df, 'combined_embedding',
                                                                 'combined_field')  # column name to  be changed
        leads_update_df['address_embedding'] = process_in_batch(leads_update_df, 'address_embedding',
                                                                'full_address')  # column name to  be changed
        leads_update_df['name_embedding'] = process_in_batch(leads_update_df, 'name_embedding',
                                                             'business_name')  # column name to  be changed

        # Select columns to be inserted into the database, including embeddings for each field
        leads_update_df = leads_update_df.rename(
            columns={"full_address": "business_address", "fiscal_year_lead": "fiscal_year",
                     "fiscal_period_lead": "fiscal_period"})

        leads_update_df['updated_date'] = pd.to_datetime(datetime.now())

        leads_update_df = leads_update_df[['warehouse_number', 'lead_id', 'updated_date',
                                           'combined_field', 'business_address', 'business_name',
                                           'combined_embedding', 'address_embedding',
                                           'name_embedding', 'fiscal_year', 'fiscal_period']]

        Lead = get_lead_class(insert_lead_table_name, schema_name)

        update_operation_leads(engine, leads_update_df, Lead)



