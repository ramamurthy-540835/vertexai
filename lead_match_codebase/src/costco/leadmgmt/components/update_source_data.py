import pandas as pd
import sqlalchemy
from sqlalchemy import text
from datetime import datetime
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from google.cloud import storage
from sqlalchemy.types import TIMESTAMP

def get_gcs_file_path(uri: str) -> str:
    if not uri.startswith("gs://"):
        raise ValueError("Invalid GCS URI. Must start with 'gs://'.")

    # Extract bucket and folder from URI
    path = uri[5:]
    parts = path.split('/', 1)
    bucket_name = parts[0]
    folder_path = parts[1] if len(parts) > 1 else ""

    if folder_path and not folder_path.endswith('/'):
        folder_path += '/'

    # Connect to GCS
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # List blobs under the folder
    blobs = list(bucket.list_blobs(prefix=folder_path))
    files = [blob.name for blob in blobs if not blob.name.endswith('/')]

    if len(files) != 1:
        raise ValueError(f"Expected exactly one file in '{uri}', found {len(files)}")

    return f"gs://{bucket_name}/{files[0]}"


def lead_table_update(engine, leads_dataframe, create_temp_table_lead, insert_query_lead,schema_name):
    with engine.connect() as connection:
        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(create_temp_table_lead)
            )
        print("Temporary lead table created")

        leads_dataframe.to_sql("temp_lead", con=connection, schema=schema_name, if_exists="append", index=False,
                               method="multi", chunksize=5000, dtype={"updated_date": TIMESTAMP(timezone=False)})

        print("Data inserted into the temp_lead table")


        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(insert_query_lead)
            )

        print("Data updated to the lead table")


def transaction_table_update(engine, pos_dataframe, create_temp_table_transaction, insert_query_transaction,schema_name):
    with engine.connect() as connection:
        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(create_temp_table_transaction)
            )
        print("Temporary transaction table created")

        pos_dataframe.to_sql("temp_transaction", con=connection, schema=schema_name, if_exists="append", index=False,
                             method="multi", chunksize=5000, dtype={"updated_date": TIMESTAMP(timezone=False)})

        print("Data inserted into the temp_transaction table")


        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(insert_query_transaction)
            )

        print("Data updated to the transaction table")





def update_cloud_sql(config_file_path: str,file_path: str = ""):


    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query

    # query
    create_temp_table_transaction=query_config.create_temp_table_transaction
    create_temp_table_lead=query_config.create_temp_table_lead
    insert_query_transaction=query_config.insert_query_transaction
    insert_query_lead=query_config.insert_query_lead

    # engine
    engine = db_config.get_engine()
    schema_name = db_config.schema_name

    #in case of failure
    storage_config=job_config.storage_config
    standalone_file_path=storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    # Load the files from GCS into pandas DataFrames
    final_df = load_file_from_gcs(file_path)

    final_df.rename(columns={'similarity_score': 'match_score'}, inplace=True)
    # Ensure match_score is numeric (in case it's a string)
    final_df['match_score'] = pd.to_numeric(final_df['match_score'], errors='coerce')
    final_df['updated_by'] = 'GCP'

    # preprocessing pos_dataframe
    # pos_dataframe = final_df[final_df['pos_id'] != '']
    pos_dataframe = final_df[final_df['confidence_level'].isin(['High', 'Medium', 'Low'])]
    pos_dataframe = pos_dataframe[['pos_id', 'lead_id', 'match_type', 'match_score', 'updated_by', 'updated_date']]
    # Sort by match_score descending so the highest score comes first
    pos_dataframe = pos_dataframe.sort_values(by='match_score', ascending=False)
    # Drop duplicates, keeping the first (i.e., highest match_score)
    pos_dataframe = pos_dataframe.drop_duplicates(subset='pos_id', keep='first').reset_index(drop=True)
    print('pos confidence dataframe: ', len(pos_dataframe))

    # preprocessing leads_dataframe
    # Assign 'closed_fiscal_period' and 'closed_fiscal_year' for 'High' confidence level
    leads_dataframe = final_df[final_df['confidence_level'].isin(['High', 'Medium', 'Low'])]
    leads_dataframe.loc[:, 'closed_fiscal_period'] = None
    leads_dataframe.loc[:, 'closed_fiscal_year'] = None
    high_confidence = leads_dataframe[leads_dataframe['confidence_level'] == 'High']

    leads_dataframe.loc[high_confidence.index, 'closed_fiscal_period'] = 8
    leads_dataframe.loc[high_confidence.index, 'closed_fiscal_year'] = 2025

    # Sort by match_score descending so the highest score comes first
    leads_dataframe = leads_dataframe.sort_values(by='match_score', ascending=False)
    # Drop duplicates, keeping the first (i.e., highest match_score)
    leads_dataframe = leads_dataframe.drop_duplicates(subset='lead_id', keep='first').reset_index(drop=True)
    leads_dataframe = leads_dataframe[
        ['lead_id','account_number', 'lead_status', 'confidence_level', 'updated_date', 'updated_by', 'closed_fiscal_period',
         'closed_fiscal_year']]
    leads_dataframe['account_number'] = leads_dataframe['account_number'].astype(int)
    print('lead table dataframe: ', len(leads_dataframe))


    if not pos_dataframe.empty:
        transaction_table_update(engine, pos_dataframe, create_temp_table_transaction,
                                 insert_query_transaction,schema_name)  # transaction table update

    if not leads_dataframe.empty:
        lead_table_update(engine, leads_dataframe, create_temp_table_lead, insert_query_lead,schema_name)  # lead table update





