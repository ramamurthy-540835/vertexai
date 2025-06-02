import io
import sys
import time
import uuid

import sqlalchemy
from datetime import datetime, timedelta
import pandas as pd
from google.cloud import storage
from google.cloud import secretmanager
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, text
import json
import os
from dataclasses import dataclass
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import text

# Define default arguments
from sqlalchemy.dialects.postgresql import UUID,TIMESTAMP
from sqlalchemy.exc import SQLAlchemyError
import configparser

google_project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
db_details = None


def access_secret_version(project_id, secret_id, version_id="latest"):
    """
    Accesses a secret version from Secret Manager.

    Args:
        project_id: The GCP project ID.
        secret_id: The ID of the secret.
        version_id: The version ID of the secret. Defaults to "latest".

    Returns:
        The secret payload as a string, or None if an error occurs.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"

    try:
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        return payload
    except Exception as e:
        print(f"Error accessing secret: {e}")
        return None


@dataclass
class DatabaseDetail:
    schema_name: str = os.environ.get("DB_SCHEMA")
    instance_connection_name: str = os.environ.get("DB_CONNECTION_NAME")
    db_user: str = os.environ.get("POSTGRES_DB_USER")
    db_name: str = os.environ.get("POSTGRES_DB_NAME")
    db_password_id: str = os.environ.get("POSTGRES_DB_PASSWORD_ID")
    project_id: str = os.environ.get("GCP_PROJECT_ID")

    if os.environ.get("CLOUD_SQL_IP_TYPE") == "PRIVATE":
        ip_type = IPTypes.PRIVATE
    elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PUBLIC":
        ip_type = IPTypes.PUBLIC
    elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PSC":
        ip_type = IPTypes.PSC
    else:
        ip_type = os.environ.get("CLOUD_SQL_IP_TYPE")

    db_password: str = access_secret_version(project_id, db_password_id, version_id="latest")


def getconn():
    global db_details
    if db_details is None:
        db_details = DatabaseDetail()
    # initialize Connector object
    connector = Connector()
    INSTANCE_CONNECTION_NAME = db_details.instance_connection_name
    DB_USER = db_details.db_user
    DB_PASS = db_details.db_password
    DB_NAME = db_details.db_name
    print("inside get connection")
    conn = connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        password=DB_PASS,
        db=DB_NAME,
        ip_type=db_details.ip_type,
    )
    print("returning from get connection")
    return conn


def get_costco_fiscal_info(input_date=None):
    # Default to today if no input date is provided
    if input_date is None:
        input_date = datetime.today()
    else:
        input_date = datetime.strptime(input_date, '%Y-%m-%d')

    # Extract year and determine fiscal year
    year = input_date.year
    fiscal_year = year + 1 if input_date.month >= 9 else year

    if input_date.month < 9:
        year = year - 1

        # Find first Monday closest to September 1st of the current fiscal year
    fiscal_start = datetime(year, 9, 1)
    while fiscal_start.weekday() != 0:  # Monday is 0
        fiscal_start += timedelta(days=1)

    # Determine weeks since fiscal start
    days_since_start = (input_date - fiscal_start).days

    # print(days_since_start)
    weeks_since_start = days_since_start // 7
    # Fiscal periods are 4 weeks long (except the last one)
    fiscal_period = min(12, (weeks_since_start // 4) + 1)

    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period
    }


def check_file_exist_in_gcs(bucket_name, file_path):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        return blob.exists()
    except Exception as e:
        print(f"An error occurred: {e}")
        return False


def read_data_from_source(bucket_name, blob_name, job_config, encoding='utf-8'):
    df = None
    try:
        print("inside read data from source")
        gcs_path = f"gs://{bucket_name}/{blob_name}"
        print(f"gcp path :{gcs_path}")
        df = pd.read_csv(gcs_path, encoding=encoding, low_memory=False)


    except Exception as ex:
        print("Error happened while reading file")
        raise ex
    return df


def write_pandas_dataframe_to_gcs(dataframe, bucket_name, blob_name, file_format='csv', **kwargs):
    """
    Writes a Pandas DataFrame to Google Cloud Storage.

    Args:
        dataframe (pd.DataFrame): The DataFrame to write.
        bucket_name (str): The name of the GCS bucket.
        blob_name (str): The name of the GCS blob (file).
        file_format (str, optional): The file format ('csv', 'json', 'parquet', etc.). Defaults to 'csv'.
        **kwargs: Additional keyword arguments to pass to the Pandas to_* function.
    """

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if file_format == 'csv':
            csv_buffer = io.StringIO()
            dataframe.to_csv(csv_buffer, index=False, **kwargs)
            blob.upload_from_string(csv_buffer.getvalue(), 'text/csv')

        elif file_format == 'json':
            json_buffer = dataframe.to_json(orient='records', **kwargs)
            blob.upload_from_string(json_buffer, 'application/json')

        elif file_format == 'parquet':
            parquet_buffer = io.BytesIO()
            dataframe.to_parquet(parquet_buffer, index=False, **kwargs)
            blob.upload_from_string(parquet_buffer.getvalue(), 'application/octet-stream')

        else:
            raise ValueError(f"Unsupported file format: {file_format}")

        print(f"DataFrame written to gs://{bucket_name}/{blob_name}")

    except Exception as e:
        print(f"Error writing DataFrame to GCS: {e}")


# Step 2: Cleanse and Transform Data
def cleanse_and_transform_lead(input_bucket, input_blob, staging_bucket, staging_blob, archive_bucket, archive_blob,
                               job_config):
    print("inside cleanse_and_transform")
    is_file_loaded = False
    try:
        df = read_data_from_source(input_bucket, input_blob,job_config, encoding='windows-1252')
        initial_record_count = len(df)
        cleansed_df = df.drop_duplicates().reset_index(drop=True)

        rename_dict = job_config.lead_rename_dict
        ls_columns = job_config.lead_file_columns

        cleansed_df = cleansed_df[ls_columns].rename(columns=rename_dict)
        fiscal_info = get_costco_fiscal_info()
        print(fiscal_info)
        cleansed_df['fiscal_year'] = fiscal_info['fiscal_year']
        cleansed_df['fiscal_period'] = fiscal_info['fiscal_period']
        after_duplicate_remove_count = len(cleansed_df)
        print("Delete if file already exist in the Staging folder")
        # delete_blob(staging_bucket,staging_blob)
        archive_gcs_files(source_bucket_name=staging_bucket, source_folder=staging_blob,
                          destination_bucket_name=archive_bucket, destination_folder=archive_blob,
                          service_account_path=None)
        print(f"records count before and after dedup - {initial_record_count} , {after_duplicate_remove_count}")
        staging_blob_name = f"{staging_blob}/{input_blob.split('/')[-1]}"
        write_pandas_dataframe_to_gcs(cleansed_df, staging_bucket, staging_blob_name, file_format='csv')
        archive_gcs_files(source_bucket_name=input_bucket, source_folder=input_blob,
                          destination_bucket_name=archive_bucket, destination_folder=archive_blob,
                          service_account_path=None)
        batch_id = uuid.uuid4()
        add_batch_id(batch_id, "lead", len(df), len(cleansed_df), "pre-processing", "Completed")
        is_file_loaded = True
        print("Archive input file completed ")
    except Exception as ex:
        print("Error : error happened while cleansing data ")
        print(ex)
        batch_id = uuid.uuid4()
        add_batch_id(batch_id, "lead", len(df), len(cleansed_df), "pre-processing", "Failed")
        raise ex
    return is_file_loaded


def cleanse_and_transform_pos(input_bucket, input_blob, pre_processing_bucket, pre_processing_blob, archive_bucket,
                              archive_blob, job_config, encoding='utf-8'):
    print("inside cleanse_and_transform")
    is_file_loaded = False
    input_count = 0
    output_count = 0
    try:
        df = read_data_from_source(input_bucket, input_blob,job_config, encoding=encoding)
        input_count = len(df)
        cleansed_df = df.drop_duplicates().reset_index(drop=True)
        ls_columns = job_config.pos_file_columns
        rename_dict = job_config.pos_rename_dict
        cleansed_df = cleansed_df[ls_columns].rename(columns=rename_dict)

        cleansed_df[['fiscal_year', 'fiscal_period']] = cleansed_df.apply(
            lambda row: get_costco_fiscal_info(row['transaction_date']), axis=1).apply(pd.Series)

        output_count = len(cleansed_df)
        print("Delete if file already exist in the Staging folder")
        # delete_blob(staging_bucket,staging_blob)
        archive_gcs_files(source_bucket_name=pre_processing_bucket, source_folder=pre_processing_blob,
                          destination_bucket_name=archive_bucket, destination_folder=archive_blob,
                          service_account_path=None)
        print(f"records count before and after dedup - {input_count} , {output_count}")
        pre_process_blob_name = f"{pre_processing_blob}/{input_blob.split('/')[-1]}"
        write_pandas_dataframe_to_gcs(cleansed_df, pre_processing_bucket, pre_process_blob_name, file_format='csv')
        archive_gcs_files(source_bucket_name=input_bucket, source_folder=input_blob,
                          destination_bucket_name=archive_bucket, destination_folder=archive_blob,
                          service_account_path=None)
        batch_id = uuid.uuid4()
        add_batch_id(batch_id, "pos", input_count, output_count, "pre-processing", "Completed")
        is_file_loaded = True
        print("Archive input file completed ")
    except Exception as ex:
        print("Error : error happened while cleansing data ")
        print(ex)
        batch_id = uuid.uuid4()
        add_batch_id(batch_id, "lead", input_count, output_count, "pre-processing", "Failed")
        raise ex
    return is_file_loaded


def dedup_account(cleansed_df):
    ls_account_columns = ["account_id_gcp", 'contact_id_gcp', 'lead_id_gcp', "bd_industry", "street", "city", "name",
                          "phone", "state",
                          "zip_code", "sic4_code", "sic4_description"]
    dedup_columns = ['street', 'city', 'state', 'zip_code', 'name']
    account_df = cleansed_df[ls_account_columns]
    df_clone = account_df.copy()

    df_clone = df_clone.fillna('-1')
    # Step 1: Identify groups based on the deduplication columns
    df_clone['group_id'] = df_clone.groupby(dedup_columns).ngroup()

    # Step 2: Find the "master" record for each group (smallest id1 within each group)
    group_master_ids = (
        df_clone.groupby('group_id')['account_id_gcp']
            .transform('min')
    )

    # Step 3: Assign the master id1 to all records in the same duplicate group
    df_clone['account_id_gcp'] = group_master_ids

    df_clone = df_clone.drop(columns=["group_id", "bd_industry", "street", "city", "name", "phone", "state",
                                      "zip_code", "sic4_code", "sic4_description"])

    merged = cleansed_df.merge(df_clone, on=['contact_id_gcp', 'lead_id_gcp'], how='left', suffixes=('', '_new'))

    # Update status where there is a match
    merged['account_id_gcp'] = merged['account_id_gcp_new'].combine_first(merged['account_id_gcp'])

    deduped_df = merged.drop(columns=['account_id_gcp_new'])
    # print(deduped_df)
    return deduped_df


def add_batch_id(batch_id: str, data_type: str, total_count: int, success_count: int, stage: str, status: str):
    try:
        engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=getconn, )
        params = {"batch_id": batch_id, "data_type": data_type, "total_count": total_count,
                  "success_count": success_count, "stage": stage, "status": status}
        insert_query = f"insert into {db_details.schema_name}.batch_audit (batch_id,data_type,total_volume,success_count,stage,status) values( :batch_id,:data_type" \
                       f",:total_count,:success_count,:stage,:status);"
        with engine.begin() as conn:
            conn.execute(text(insert_query), params)
            print(f"batch id added successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e


def update_batch_id(batch_id: str, data_type: str, success_count: int):
    try:
        engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=getconn, )
        schema = db_details.schema_name
        params = {"batch_id": batch_id, "data_type": data_type, "success_count": success_count}
        update_query = f"update {schema}.batch_audit set success_count =:success_count where batch_id = :batch_id and data_type = :data_type";
        with engine.begin() as conn:
            conn.execute(text(update_query), params)
            print(f"batch id update successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e


def get_latest_batch_id(data_type, stage):
    batch_id = None
    status = None
    try:
        engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=getconn, )
        schema = db_details.schema_name
        params = {"data_type": data_type, "stage": stage}
        select_query = f"select batch_id,status from {schema}.batch_audit where  data_type = :data_type and stage =:stage order by load_date desc limit 1 ;"
        print(select_query)
        with engine.begin() as conn:
            result = conn.execute(text(select_query), params)
            first_result = result.first()
            if first_result:
                batch_id = first_result[0]
                status = first_result[1]
            return batch_id, status
    except Exception as ex:
        print("Error happened reading  batch audit data")
        print(ex)
        raise ex


def split_bo_from_df(cleansed_df, batch_id, job_config):
    acct_dedup_columns = job_config.account_dedup_columns
    ls_account_columns = job_config.account_columns
    ls_lead_columns = job_config.lead_columns
    ls_contact_columns = job_config.contact_columns

    cleansed_df["lead_id_gcp"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID
    cleansed_df["account_id_gcp"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID
    cleansed_df["contact_id_gcp"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID
    # batch_id = uuid.uuid4()
    print(batch_id)
    cleansed_df["batch_id"] = batch_id
    cleansed_df["lead_status"] = "New"
    cleansed_df["lead_source"] = "Firefly"
    cleansed_df = dedup_account(cleansed_df)
    lead_df = cleansed_df[ls_lead_columns]
    account_df = cleansed_df[ls_account_columns].drop_duplicates(subset=acct_dedup_columns, keep='first')
    concact_df = cleansed_df[ls_contact_columns]

    return account_df, concact_df, lead_df


def upsert_account_data(account_df, batch_id):
    schema = db_details.schema_name
    table = 'account'
    temp_table = 'account_temp'

    merge_query = f""" INSERT INTO {schema}.{table} ( account_id_gcp, batch_id, bd_industry,street,city,name, phone, state,
             zip_code, sic4_code, sic4_description,temp_account_id)  
             SELECT  account_id_gcp, batch_id, bd_industry,street,city,name, phone, state,
             zip_code, sic4_code, sic4_description,account_id_gcp FROM {schema}.{temp_table}
    ON CONFLICT (COALESCE(name, '__NULL__'),COALESCE(street, '__NULL__'),COALESCE(city, '__NULL__'),COALESCE(state, '__NULL__'),COALESCE(zip_code, '__NULL__'))
    DO UPDATE SET
    name = EXCLUDED.name,
    street = EXCLUDED.street,
     city = EXCLUDED.city,
      state = EXCLUDED.state,
        phone = EXCLUDED.phone ,
        batch_id = EXCLUDED.batch_id,
        temp_account_id = EXCLUDED.account_id_gcp; """

    updates_cnt_sql = f"select count(*) from {schema}.{temp_table} a left join  {schema}.{table}  b on a.account_id_gcp = b.account_id_gcp where b.account_id_gcp is null;"
    insert_cnt_sql = f"select count(*) from {schema}.{temp_table} a join  {schema}.{table}  b on a.account_id_gcp = b.account_id_gcp;"
    select_account_query = f"select account_id_gcp, temp_account_id  from {schema}.{table} where batch_id = '{batch_id}';"
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn, )
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {schema}.{temp_table}"))
        account_df.to_sql("account_temp", con=conn, schema=schema, if_exists="append", index=False,
                          method="multi", chunksize=1000,
                          dtype={"account_id_gcp": UUID(as_uuid=True), "batch_id": UUID(as_uuid=True)})
        conn.execute(text(merge_query))
        result1 = conn.execute(text(updates_cnt_sql))
        updates_count = result1.fetchone()[0]
        print(f"updated rows: {updates_count}")
        result2 = conn.execute(text(insert_cnt_sql))
        insert_count = result2.fetchone()[0]
        print(f"insert rows: {insert_count}")
        print(select_account_query)
        df_account = pd.read_sql(select_account_query, conn)
        return df_account


def update_account_id_in_contact(concact_df, df_account):
    df_merged = concact_df.merge(df_account, left_on='account_id_gcp', right_on='temp_account_id', how='right')
    df_merged = df_merged.drop(columns=['account_id_gcp_x', 'temp_account_id'])
    contact = df_merged.rename(columns={'account_id_gcp_y': 'account_id_gcp'})

    return contact


def update_account_id_in_lead(lead_df, df_account):
    df_merged = lead_df.merge(df_account, left_on='account_id_gcp', right_on='temp_account_id', how='right')
    df_merged = df_merged.drop(columns=['account_id_gcp_x', 'temp_account_id'])
    lead = df_merged.rename(columns={'account_id_gcp_y': 'account_id_gcp'})

    return lead


def write_to_sql(account_df, concact_df, lead_df, batch_id):
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn, )
    schema = db_details.schema_name
    # Insert DataFrames into PostgreSQL
    try:
        with engine.connect() as connection:
            with connection.begin():
                df_account = upsert_account_data(account_df, batch_id)
                concact_df = update_account_id_in_contact(concact_df, df_account)
                lead_df = update_account_id_in_lead(lead_df, df_account)
                # account_df.to_sql("account", con=connection, schema=schema, if_exists="append", index=False,
                #                      method="multi", chunksize=1000, dtype={ "account_id_gcp": UUID(as_uuid=True)})
                concact_df.to_sql("contact", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=1000,
                                  dtype={"account_id_gcp": UUID(as_uuid=True), "contact_id_gcp": UUID(as_uuid=True)})
                lead_df.to_sql("lead", con=connection, schema=schema, if_exists="append", index=False,
                               method="multi", chunksize=1000,
                               dtype={"lead_id_gcp": UUID(as_uuid=True), "contact_id_gcp": UUID(as_uuid=True),
                                      "batch_id": UUID(as_uuid=True), "account_id_gcp": UUID(as_uuid=True)})
    except SQLAlchemyError as e:
        print(f"Database error: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e

    print("Data successfully inserted!")


def pos_write_to_database(pos_df, batch_id):
    print("inside pos_write_to_database ")
    engine = sqlalchemy.create_engine("postgresql+pg8000://",creator=getconn, )
    schema = db_details.schema_name
    try:
        with engine.connect() as connection:
            with connection.begin():
                pos_df['batch_id'] = batch_id
                pos_df.to_sql("transaction", con=connection, schema=schema, if_exists="append", index=False,
                              method="multi", chunksize=1000,
                              dtype={"batch_id": UUID(as_uuid=True),'transaction_date':TIMESTAMP})

    except SQLAlchemyError as e:
        print(f"Database error: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e

    print("Data successfully inserted!")


# Step 3: Load Data into PostgreSQL
def lead_load_to_postgres(staging_bucket, staging_blob, archive_bucket, archive_blob, batch_id, job_config, **kwargs):
    print("inside load_to_postgres")
    input_count = 0
    output_count = 0
    try:
        cleansed_df = read_data_from_source(staging_bucket, staging_blob, job_config)
        input_count = len(cleansed_df)
        account_df, concact_df, lead_df = split_bo_from_df(cleansed_df, batch_id, job_config)
        write_to_sql(account_df, concact_df, lead_df, batch_id)
        add_batch_id(batch_id, "lead", len(cleansed_df), len(lead_df), "staging", "Completed")
        print(f'{"batch_id":"{batch_id}" }')
    except Exception as ex:
        print("ERROR: load to database failed")
        print(ex)
        add_batch_id(batch_id, "lead", input_count, len(lead_df), "staging", "Completed")


def pos_load_to_postgres(staging_bucket, staging_blob, archive_bucket, archive_blob, batch_id, **kwargs):
    print("inside pos_load_to_postgres")
    input_count = 0
    output_count = 0
    try:
        cleansed_df = read_data_from_source(staging_bucket, staging_blob,job_config)
        input_count = len(cleansed_df)
        pos_write_to_database(cleansed_df, batch_id)
        add_batch_id(batch_id, "pos", len(cleansed_df), len(cleansed_df), "staging", "Completed")
        output_count = len(cleansed_df)
        print(f'batch_id:{batch_id} ')
    except Exception as ex:
        print("ERROR: load to database failed")
        print(ex)
        add_batch_id(batch_id, "lead", input_count, output_count, "staging", "Failed")
        raise ex



def delete_blob(bucket_name, blob_path, service_account_path=None):
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials
    bucket = client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=blob_path)

    for blob in blobs:
        blob.delete()
        print(f"Blob {blob.name} deleted.")

    print(f"Folder '{blob_path}' in bucket '{bucket_name}' deleted.")


def archive_gcs_files(
        source_bucket_name,
        source_folder,
        destination_bucket_name,
        destination_folder,
        service_account_path=None
):
    # Initialize client
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials

    source_bucket = client.bucket(source_bucket_name)
    destination_bucket = client.bucket(destination_bucket_name)

    # List files in the source folder
    blobs = list(client.list_blobs(source_bucket_name, prefix=source_folder))

    if not blobs:
        print(f"No files found in {source_bucket_name}/{source_folder}")
        return
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    for blob in blobs:
        source_path = blob.name  # Full path in source bucket

        destination_path = f"{destination_folder}/{timestamp}/{source_path.split('/')[-1]}"

        # Copy file to archive bucket
        source_blob = source_bucket.blob(source_path)
        destination_blob = destination_bucket.blob(destination_path)

        destination_blob.rewrite(source_blob)

        # Optionally delete from source after successful copy (move instead of copy)
        source_blob.delete()

        print(f"Moved {source_path} to {destination_path}")


def write_to_csv_local(df, csv_file_path):
    df.fillna('')
    df.to_csv(csv_file_path, index=False, encoding='utf-8', na_rep='')


def db_to_csv_gcs(query, folder_path, file_name, engine, bucket_name, chunksize=50000, service_account_path=None):
    # Initialize client
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials

    bucket = client.bucket(bucket_name)
    chunk_number = 0
    ls_output_files = []
    for chunk in pd.read_sql(query, con=engine, chunksize=chunksize):
        csv_buffer = io.StringIO()
        chunk.to_csv(csv_buffer, index=False)
        # Define GCS path
        blob_name = f"{folder_path}/{file_name}_{chunk_number}.csv"
        ls_output_files.append(blob_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(csv_buffer.getvalue(), content_type='text/csv')
        print(f"Uploaded {blob_name} with {len(chunk)} records")

        chunk_number += 1

    return ls_output_files


def archive_files(source_bucket_name, destination_bucket_name, stg_folder, archive_folder, object_list):
    for entity in object_list:
        src_folder = f"{stg_folder}/{entity}"
        tgt_folder = f"{archive_folder}/{entity}"
        archive_gcs_files(source_bucket_name=source_bucket_name, source_folder=src_folder,
                          destination_bucket_name=destination_bucket_name, destination_folder=tgt_folder,
                          service_account_path=None)


def export_db_to_gcs(query_dict, folder_path, ls_entities, engine, bucket, chunksize, service_account_path=None):
    for entity in ls_entities:
        query = query_dict[entity]
        folder = f"{folder_path}/{entity}"
        file_name = entity
        ls_output_files = db_to_csv_gcs(query, folder, file_name, engine, bucket, chunksize)
        print(f"output files : {ls_output_files}")


def export_data_from_db_to_gcs(**kwargs):
    ti = kwargs["ti"]
    batch_id = ti.xcom_pull(task_ids="load_to_postgres", key="batch_id")
    start_time = time.time()
    schema = db_details.schema_name

    engine = create_engine("postgresql+pg8000://", creator=getconn)

    select_lead_query = f"select lead_id_gcp,account_id_gcp,contact_id_gcp,firefly_id,batch_id,lead_snow_id,lead_source,lead_status,confidence_level,assigned_to,\
    lead_notes,membership_number,warehouse_number \
      from {schema}.lead where batch_id = '{batch_id}' "
    select_account_query = f"select account_id_gcp,account_id_snow,batch_id,name,street,city,state,zip_code,phone,sic4_code,sic6_code,bd_industry,duplicate from {schema}.account a where a.batch_id = '{batch_id}' "
    select_contact_query = f"select a.contact_id_gcp,a.account_id_gcp,a.contact_id_snow,a.first_name,a.last_name,a.email,a.phone,a.job_title from {schema}.contact a join {schema}.account b on a.account_id_gcp = b.account_id_gcp where b.batch_id = '{batch_id}' "

    dict_select_query = {'lead': select_lead_query, 'account': select_account_query, 'contact': select_contact_query}

    bucket_name = os.environ.get("LANDING_BUCKET")
    chunksize = os.environ.get("FILE_CHUNK_SIZE", 5000)
    ls_entities = ['lead', 'account', 'contact']
    stg_folder = "stagging"
    archive_folder = "archive/stagging"

    archive_files(bucket_name, bucket_name, stg_folder, archive_folder, ls_entities)
    export_db_to_gcs(dict_select_query, stg_folder, ls_entities, engine, bucket_name, chunksize=chunksize)

    end_time = time.time()  # Record the end time
    elapsed_time = end_time - start_time
    print(f"Elapsed time: {elapsed_time:.4f} seconds")


def dataframe_to_json_rows(df, output_file):
    try:

        with open(output_file, 'w') as f:
            for index, row in df.iterrows():
                json_row = row.to_json()  # Convert row to JSON string
                f.write(json_row + '\n')  # write each row as a new line.

        print(f"DataFrame converted to JSON rows and written to {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

    print("extracting data and converting it to json")


def db_to_json(query, uuid_columns, folder_path, file_name, bucket_name, chunksize=50000):
    chunk_number = 0
    ls_output_files = []
    client = storage.Client()
    total_rec_count = 0
    bucket = client.bucket(bucket_name)
    engine = create_engine("postgresql+pg8000://", creator=getconn)
    for chunk in pd.read_sql(query, con=engine, chunksize=chunksize):
        # Step 3: Convert chunk to JSON (list of dicts)
        for col in uuid_columns:
            chunk[col] = chunk[col].astype(str)
            if pd.api.types.is_datetime64_any_dtype(chunk[col]):
                print("inside timestamp data ")
                #chunk[col] = chunk[col].dt.isoformat()
                chunk[col] = chunk[col].apply(lambda x: x.isoformat() if pd.notnull(x) else None)
        for col in chunk.select_dtypes(include=['datetime64[ns]', 'timedelta64[ns]']).columns:
            print("inside timstamp secnd time")
            chunk[col] = chunk[col].dt.isoformat()
        json_data = chunk.to_dict(orient='records')
        total_rec_count = total_rec_count + len(chunk)
        # Step 4: Upload JSON to GCS
        json_string = json.dumps(json_data,  indent=2)

        # Define GCS path
        blob_name = f"{folder_path}/{file_name}_{chunk_number}.json"
        ls_output_files.append(blob_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json_string, content_type='application/json')

        print(f"Uploaded {blob_name} with {len(chunk)} records")

        chunk_number += 1
    return ls_output_files, total_rec_count


def lead_export_all_entity(extract_folder, extract_bucket_name,archive_bucket_name,archive_blob, batch_id,job_config, chunk_size=50000):
    schema = db_details.schema_name
    # SQL Query
    select_lead_query = f"select lead_id_gcp,account_id_gcp,contact_id_gcp,firefly_id,batch_id,lead_snow_id,lead_source,lead_status,confidence_level,assigned_to,\
    lead_notes,membership_number,warehouse_number \
      from {schema}.lead where batch_id = '{batch_id}' "
    select_account_query = f"select account_id_gcp,account_id_snow,batch_id,name,street,city,state,zip_code,phone,sic4_code,sic6_code,bd_industry,duplicate from {schema}.account a where a.batch_id = '{batch_id}' "
    select_contact_query = f"select a.contact_id_gcp,a.account_id_gcp,a.contact_id_snow,a.first_name,a.last_name,a.email,a.phone,a.job_title from {schema}.contact a join {schema}.account b on a.account_id_gcp = b.account_id_gcp where b.batch_id = '{batch_id}' "

    uuid_columns_lead = ['lead_id_gcp', 'account_id_gcp', 'contact_id_gcp', 'batch_id']
    uuid_columns_account = ['account_id_gcp', 'batch_id']
    uuid_columns_contact = ['account_id_gcp', 'contact_id_gcp']

    ls_entities = ['lead', 'account', 'contact']
    archive_folder = f"{archive_blob}/{extract_folder}"

    archive_files(extract_bucket_name, archive_bucket_name, extract_folder, archive_folder, ls_entities)
    ls_output_files, lead_total_rec_count = db_to_json(select_lead_query, uuid_columns_lead, extract_folder,
                                                       'lead', extract_bucket_name, chunk_size)
    print(f" total chunk files - {ls_output_files}, total records count = {lead_total_rec_count}")
    ls_output_files, total_rec_count = db_to_json(select_account_query, uuid_columns_account, extract_folder,
                                                  'account', extract_bucket_name, chunk_size)
    print(f" total chunk files - {ls_output_files}, total records count = {total_rec_count}")
    ls_output_files, total_rec_count = db_to_json(select_contact_query, uuid_columns_contact, extract_folder,
                                                  'contact', extract_bucket_name, chunk_size)
    print(f" total chunk files - {ls_output_files}, total records count = {total_rec_count}")

    return lead_total_rec_count


def pos_export_data(extract_folder, extract_bucket_name, archive_bucket_name, archive_folder,batch_id , job_config, chunk_size=50000):
    schema = db_details.schema_name
    # SQL Query
    select_pos_query = f"select {job_config.pos_extract_columns} from {schema}.transaction where batch_id = '{batch_id}'"
    uuid_columns_pos = ['lead_id_gcp', 'batch_id','transaction_date']
    archive_folder_1 = f"{archive_folder}/{extract_folder}"
    # archive existing data
    entity = "pos"
    src_folder = f"{extract_folder}/{entity}"
    tgt_folder = f"{archive_folder_1}/{entity}"
    archive_gcs_files(source_bucket_name=extract_bucket_name, source_folder=src_folder,
                      destination_bucket_name=archive_bucket_name, destination_folder=tgt_folder,
                      service_account_path=None)
    ls_output_files, lead_total_rec_count = db_to_json(select_pos_query, uuid_columns_pos, extract_folder,
                                                       'pos', extract_bucket_name, chunk_size)
    print(f"Total chunk files - {ls_output_files}, total records count = {lead_total_rec_count}")

    return lead_total_rec_count


def send_data_to_snow(**kwargs):
    schema = db_details.schema_name
    ti = kwargs["ti"]
    batch_id = ti.xcom_pull(task_ids="load_to_postgres", key="batch_id")
    select_lead_query = f"select * from {schema}.lead where batch_id = '{batch_id}' "
    select_account_query = f"select a.* from {schema}.account a join {schema}.lead b on a.lead_id_gcp = b.lead_id_gcp where b.batch_id = '{batch_id}' "
    select_contact_query = f"select a.* from {schema}.contact a join {schema}.account b on a.account_id_gcp = b.account_id_gcp join {schema}.leads c on c.lead_id_gcp = b.lead_id_gcp where c.batch_id = '{batch_id}' "
    engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn)
    df_lead = pd.read_sql(select_lead_query, engine)
    df_account = pd.read_sql(select_account_query, engine)
    df_contact = pd.read_sql(select_contact_query, engine)

    df_lead = df_lead.map(lambda x: x.encode('utf-8', 'ignore').decode('utf-8') if isinstance(x, str) else str(x))
    df_account = df_account.map(lambda x: x.encode('utf-8', 'ignore').decode('utf-8') if isinstance(x, str) else str(x))
    df_contact = df_contact.map(lambda x: x.encode('utf-8', 'ignore').decode('utf-8') if isinstance(x, str) else str(x))

    dataframe_to_json_rows(df_lead, )
    print("extract data from database and send it servicenow")


def parse_config(filepath):
    """Parses a config.ini file and returns a dictionary."""
    config = configparser.ConfigParser()
    config.read(filepath)

    config_dict = {}
    for section in config.sections():
        config_dict[section] = {}
        for key, value in config.items(section):
            config_dict[section][key] = value

    return config_dict


@dataclass
class JobConfig:
    lead_file_columns: list
    lead_rename_dict: dict
    pos_file_columns: list
    pos_rename_dict: dict
    account_dedup_columns: list
    account_columns: list
    lead_columns: list
    contact_columns: list
    pos_extract_columns :str
    lead_extract_columns :str
    account_extract_columns :str
    contact_extract_columns :str

    def __init__(self, config_path='config.ini'):
        config = configparser.ConfigParser()
        config.read(config_path)
        # lead_config_data = parse_config("config.ini")
        print(config)
        lead_rename_columns = config.get('pre_processing', 'lead_file_columns_mapping')
        lead_ls_columns = config.get('pre_processing', 'lead_file_columns')
        pos_rename_columns = config.get('pre_processing', 'pos_file_columns_mapping')
        pos_ls_columns = config.get('pre_processing', 'pos_file_columns')
        acct_dedup_columns = config.get('staging', 'account_dedup_columns')
        account_columns_str = config.get('staging', 'account_columns')
        lead_columns_str = config.get('staging', 'lead_columns')
        contact_columns_str = config.get('staging', 'contact_columns')
        # print(rename_dict)
        self.lead_rename_dict = json.loads(lead_rename_columns)
        self.lead_file_columns = json.loads(lead_ls_columns)
        self.pos_rename_dict = json.loads(pos_rename_columns)
        self.pos_file_columns = json.loads(pos_ls_columns)
        self.account_dedup_columns = json.loads(acct_dedup_columns)
        self.account_columns = json.loads(account_columns_str)
        self.lead_columns = json.loads(lead_columns_str)
        self.contact_columns = json.loads(contact_columns_str)
        self.pos_extract_columns = config.get('extract', 'pos_extract_columns')
        self.lead_extract_columns = config.get('extract', 'lead_extract_columns')
        self.account_extract_columns = config.get('extract', 'account_extract_columns')
        self.contact_extract_columns = config.get('extract', 'contact_extract_columns')

    def __repr__(self):
        return str({
            "lead_rename_dict": self.lead_rename_dict,
            "lead_columns": self.lead_columns,
            "pos_rename_dict": self.pos_rename_dict,
            "pos_columns": self.pos_columns,
            "account_dedup_columns": self.account_dedup_columns,
            "account_columns": self.account_columns,
            "lead_columns": self.lead_columns,
            "contact_columns": self.contact_columns,
        })


if __name__ == "__main__":
    # global db_details
    print("inside main")
    if len(sys.argv) == 1:
        print("error : required argument to run specific job")
        # exit(11)

    job_config = JobConfig()
    db_details = DatabaseDetail()
    lead_input_bucket_name = os.environ.get("LEAD_INPUT_BUCKET")
    lead_input_blob_name = os.environ.get("LEAD_INPUT_BLOB")
    lead_staging_bucket_name = os.environ.get("LEAD_STAGING_BUCKET")
    lead_staging_blob_name = os.environ.get("LEAD_STAGING_BLOB")
    archive_bucket_name = os.environ.get("LEAD_ARCHIVE_BUCKET")
    archive_blob_name = os.environ.get("LEAD_ARCHIVE_BLOB")
    pos_input_bucket_name = os.environ.get("POS_INPUT_BUCKET")
    pos_input_blob_name = os.environ.get("POS_INPUT_BLOB")
    pos_staging_bucket_name = os.environ.get("POS_STAGING_BUCKET")
    pos_staging_blob_name = os.environ.get("POS_STAGING_BLOB")
    pos_archive_bucket_name = os.environ.get("POS_ARCHIVE_BUCKET")
    pos_archive_blob_name = os.environ.get("POS_ARCHIVE_BLOB")

    stage = sys.argv[1]
    if stage.lower() == "lead_cleansing":
        is_lead_file_exist = False
        # check lead file existing in landing folder then process the file
        if check_file_exist_in_gcs(lead_input_bucket_name, lead_input_blob_name):
            is_lead_file_exist = True
            is_lead_processed = cleanse_and_transform_lead(lead_input_bucket_name, lead_input_blob_name, lead_staging_bucket_name,
                                                           lead_staging_blob_name,
                                                           archive_bucket_name, archive_blob_name, job_config)
        else:
            print("Lead file not available")

    elif stage.lower() == "pos_cleansing":
        is_pos_file_exist = False
        # check lead file existing in landing folder then process the file
        if check_file_exist_in_gcs(pos_input_bucket_name, pos_input_blob_name):
            is_pos_file_exist = True
            is_pos_processed = cleanse_and_transform_pos(pos_input_bucket_name, pos_input_blob_name,
                                                         pos_staging_bucket_name, pos_staging_blob_name,
                                                         pos_archive_bucket_name, pos_archive_blob_name,job_config)

        else:
            print("POS  file not available")
    elif stage.lower() == "lead_loading_to_db":
        print("loading data into DB")
        batch_id, status = get_latest_batch_id('lead', "pre-processing")
        if batch_id and status.lower() == 'completed':
            staging_blob = f"{lead_staging_blob_name}/{lead_input_blob_name.split('/')[-1]}"
            lead_load_to_postgres(lead_staging_bucket_name, staging_blob, archive_bucket_name, archive_blob_name, batch_id,
                                  job_config)
        else:
            print("Previous stage pre-processing failed or not not ran yet")
    elif stage.lower() == "pos_loading_to_db":
        print("POS loading data into DB")

        batch_id, status = get_latest_batch_id('pos', "pre-processing")
        if batch_id and status.lower() == 'completed':
            staging_blob = f"{pos_staging_blob_name}/{pos_input_blob_name.split('/')[-1]}"
            pos_load_to_postgres(pos_staging_bucket_name, staging_blob, pos_archive_bucket_name, pos_archive_blob_name, batch_id)
        else:
            print("Previous stage pre-processing failed or not not ran yet")

    elif stage.lower() == "lead_extract_batch_data":
        print("extract data into staging folder from DB")
        batch_id, status = get_latest_batch_id('lead', "staging")
        print(batch_id)
        print(status)
        if batch_id and status.lower() == 'completed':
            extract_folder = "json_extract"
            extract_bucket_name = lead_staging_bucket_name
            lead_total_rec_count = lead_export_all_entity(extract_folder, extract_bucket_name,archive_bucket_name,
                                                          archive_blob_name, batch_id,job_config, chunk_size=50000)
            add_batch_id(batch_id, "lead", lead_total_rec_count, lead_total_rec_count, "extract", "Completed")
        else:
            print("Previous stage staging failed or not ran yet")
    elif stage.lower() == "pos_extract_batch_data":
        print("extract data into staging folder from DB")

        batch_id, status = get_latest_batch_id('pos', "staging")
        print(batch_id)
        print(status)
        if batch_id and status.lower() == 'completed':
            extract_folder = "json_extract"
            extract_bucket_name = pos_staging_bucket_name
            lead_total_rec_count = pos_export_data(extract_folder, extract_bucket_name,pos_archive_bucket_name,
                                                   pos_archive_blob_name,batch_id,job_config, chunk_size=20000)
            add_batch_id(batch_id, "pos", lead_total_rec_count, lead_total_rec_count, "extract", "Completed")
        else:
            print("Previous stage staging failed or not ran yet")

    elif stage.lower() == "lead_send_to_service_now":
        print("sending Lead data to service now using API")
    elif stage.lower() == "pos_send_to_service_now":
        print("sending POS data to service now using API")
    else:
        print("Error:invalid stage ")
        exit(10)
    print(f"stage - {stage} completed")

