import io
import json
import logging
import sys
import time
import uuid
import os
from datetime import datetime, timezone

import pandas as pd
import requests
from google.cloud import run_v2
from google.cloud import storage, pubsub_v1
from sqlalchemy import Integer, text, BigInteger
from sqlalchemy.dialects.postgresql import UUID, insert
import costco.leadmgmt.database.batch_audit_util as ba_util
import costco.leadmgmt.database.error_audit_util as ea_util
from costco.leadmgmt.config.Configuration import JobConfig, StorageConfig, SnowConfig, TransformConfig
from costco.leadmgmt.database.DBUtil import DatabaseDetail, get_table_row_count
from costco.leadmgmt.util.logger import app_logger


def split_lead_account_bo(lead_df, batch_id, transform_config):
    ls_account_columns = transform_config.account_columns
    ls_lead_columns = transform_config.lead_columns

    account_df = None
    final_lead_df = None
    app_logger.debug("inside split lead bo columns")
    if lead_df:
        lead_df["batch_id"] = batch_id
        account_df = lead_df[ls_account_columns]
        final_lead_df = lead_df[ls_lead_columns]
        app_logger.debug(f"length of account before dedup {len(account_df)}")
        account_df = account_df.drop_duplicates(subset=["account_id"], keep='first')
        app_logger.info(f"length of account after dedup {len(account_df)}")
    return account_df, final_lead_df


def split_lead_bo(lead_df, contact_df, batch_id, transform_config: TransformConfig,job_config):
    acct_dedup_columns = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'business_name']
    ls_account_columns = transform_config.account_columns
    ls_lead_columns = transform_config.lead_columns
    ls_contact_columns = transform_config.contact_columns

    app_logger.debug("inside split lead bo columns")
    final_lead_df = None
    account_df = None

    if lead_df is not None:
        lead_df["batch_id"] = batch_id

        # Separate rows where account_id is missing
        missing_account_df = lead_df[lead_df["account_id"].isna() | (lead_df["account_id"] == "")]

        # Log them into the error log table
        if not missing_account_df.empty:
            database_config: DatabaseDetail = job_config.db_config
            for _, row in missing_account_df.iterrows():
                ea_util.add_error_audit(
                    entity_type="lead_id",
                    entity_id=str(row.get("lead_id", "")),  # or whatever identifier you want
                    error_message="Missing account_id",
                    db_config=database_config,
                    batch_id=batch_id
                )
            app_logger.info(f"{len(missing_account_df)} rows logged into error_audit")


        # Keep only rows where account_id is present
        lead_df = lead_df[lead_df["account_id"].notna() & (lead_df["account_id"] != "")]

        account_df = lead_df[ls_account_columns]
        final_lead_df = lead_df[ls_lead_columns]
        app_logger.debug(f"length of account before dedup {len(account_df)}")


    if contact_df is not None:
        contact_df["batch_id"] = batch_id
        # Separate rows where account_id is missing
        missing_contact_df = contact_df[contact_df["contact_id"].isna() | (contact_df["contact_id"] == "")]

        # Log them into the error log table
        if not missing_contact_df.empty:
            database_config: DatabaseDetail = job_config.db_config
            for _, row in missing_contact_df.iterrows():
                ea_util.add_error_audit(
                    entity_type="lead_id",
                    entity_id=str(row.get("lead_id", "")),  # or whatever identifier you want
                    error_message="Missing contact_id",
                    db_config=database_config,
                    batch_id=batch_id
                )
                app_logger.info(f"{len(missing_contact_df)} rows logged into error_audit")


        # Keep only rows where contact_id is present
        contact_df = contact_df[contact_df["contact_id"].notna() & (contact_df["contact_id"] != "")]
        contact_df = contact_df[ls_contact_columns]
        app_logger.debug(f"length of contact dataframe {len(contact_df)}")

    return account_df, contact_df, final_lead_df


def find_duplicate_values(df, column_name):
    """Returns a list of duplicate values in a column."""
    return df[df.duplicated(subset=[column_name])][column_name].unique().tolist()


def upsert_using_business_key(df, table_name, unique_key_columns, primary_key_column, update_column,
                              db_config: DatabaseDetail, temp_id_column="temp_id"):
    try:
        app_logger.debug("inside upsert_dataframe_sqlalchemy")


        columns = ", ".join(f'{col}' for col in df.columns)
        placeholders = ", ".join(f":{col}" for col in df.columns)

        unique_key_identifiers = ", ".join(f"COALESCE({col}, '__NULL__')" for col in unique_key_columns)
        df[temp_id_column] = ""
        with db_config.get_engine().connect() as connection:
            for index, row in df.iterrows():
                # update_column = 'batch_id'
                insert_query = text(
                    f"""
                    INSERT INTO {table_name} ({columns}) VALUES ({placeholders})
                    ON CONFLICT ({unique_key_identifiers})
                    DO UPDATE SET {update_column} = EXCLUDED.{update_column} 
                    RETURNING {primary_key_column}
                    """
                    #
                )

                row_data = row.to_dict()
                del row_data[temp_id_column]

                result = connection.execute(insert_query, row_data)
                primary_key = result.scalar()
                if primary_key != row_data[primary_key_column]:
                    df.loc[index, primary_key_column] = primary_key
                    df.loc[index, temp_id_column] = row_data[primary_key_column]

            connection.commit()
            out_df = df[[primary_key_column, temp_id_column]]
            return out_df
    except Exception as e:
        app_logger.error(f"Error: {e}")
        app_logger.error(e)
        raise

_engine_cache = {}
def upsert_using_primary_key(df, table_name, primary_key_column, db_config: DatabaseDetail,batch_id):
    print( "inside  upsert_using_primary_key ###")
    log_limit = 1000
    total_error_record = 0
    total_success_count = 0
    max_error_limit = -1
    batch_size = 1000  # Define your batch size for commits
    current_batch = [] # To accumulate rows for a single transaction
   

    failed_ids = set()   # <-- keep track of failed primary keys
    if df is None:
        app_logger.debug(f"No records to process. input dataframe is empty for table - {table_name}")
        return total_success_count, total_error_record,failed_ids
    try:
        #engine = db_config.get_engine()
        cache_key = f"{db_config.instance_connection_name}/{db_config.db_name}/{db_config.db_user}"
        if cache_key not in _engine_cache:
            _engine_cache[cache_key] = db_config.get_engine()
        engine = _engine_cache[cache_key]
        app_logger.debug("inside upsert_dataframe_sqlalchemy")

        columns = ", ".join(f'{col}' for col in df.columns)
        placeholders = ", ".join(f":{col}" for col in df.columns)
        update_columns = ", ".join(f'{col} = EXCLUDED.{col}' for col in df.columns if col != primary_key_column)
        insert_query = text(
            f"""
                        INSERT INTO {table_name} ({columns}) VALUES ({placeholders})
                        ON CONFLICT ({primary_key_column})
                        DO UPDATE SET {update_columns}
                        RETURNING {primary_key_column}
                        """
        )
        print(f"inside  upsert_using_primary_key ### - {insert_query}")
       
        with engine.connect() as connection:   
            for index, row in df.iterrows(): 
                # update_column = 'batch_id'
                # Store rows (or their processed data) in a temporary batch
                current_batch.append((index, row))

                # If the batch is full or it's the last row, process the batch in a transaction
                if len(current_batch) >= batch_size or index == len(df) - 1:
                    try:
                        with connection.begin():  # Use a fresh transaction for each batch
                        # Start a new transaction for this batch        
                            for batch_index, batch_row in current_batch:
                                try:
                                    with connection.begin_nested():  # create savepoint
                                        # Convert NaN values to None for SQL NULL
                                        row_data = {col: None if pd.isna(value) or value =="" else value for col, value in batch_row.items()}
                                        result = connection.execute(insert_query, row_data)
                                        total_success_count += 1
                                except Exception as e:
                                    failed_id = batch_row[primary_key_column]
                                    failed_ids.add(failed_id)   # <-- collect failed ID
                                    #connection.commit()
                                    ea_util.add_error_audit(primary_key_column, failed_id, str(e), db_config,batch_id)
                                    app_logger.error(
                                        f"Error UPSERTing row with index {batch_index} and {primary_key_column} = {batch_row[primary_key_column]}: {e}")    
                                    row_data = batch_row.to_dict()
                                    print("The data involved with error: ", row_data)
                                    total_error_record += 1

                    except Exception as batch_e:
                    # This 'except' would catch issues with the entire batch transaction (e.g., network error)
                        app_logger.error(f"Critical error processing batch starting at index {current_batch[0][0]}: {batch_e}")
                    # If a batch transaction fails, it's already rolled back by the context manager.
                    # You might want to log all IDs in this failed batch to failed_ids or retry the whole batch.

                    # Clear the batch after processing
                    finally:
                        current_batch = []
                        if index != 0 and index % log_limit == 0:
                            app_logger.debug(f"processed {index} records ")
                    #     #connection.commit()
                    if max_error_limit != -1 and total_error_record > max_error_limit:
                        raise Exception(f"Number of records failure reached max limit {max_error_limit} ")
                #app_logger.debug(f"Total records processed successfully :{total_success_count}")
    except Exception as e:
        app_logger.error(f"Error occurred while insert/update records to table {table_name} ")
        app_logger.error(e)
        import traceback
        app_logger.debug(traceback.format_exc())
        raise e
    return total_success_count, total_error_record, list(failed_ids)


def read_data_from_snow(url, username, password, payload, auth_type='Basic'):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    max_retry = 3
    retry_attempt = 0
    is_data_retrived = False

    while not is_data_retrived and retry_attempt < max_retry:
        try:
            response = requests.post(url, auth=(username, password), headers=headers, data=json.dumps(payload))

            if response.status_code == 200:
                res_data = response.json()
                isDataRetrived = True
                return res_data
            else:
                app_logger.debug('Status:', response.status_code, 'Headers:', response.headers, 'Error Response:', response.json())
                app_logger.debug(f"Connecting to Service Now API failed with status code - {response.status_code}")
                retry_attempt += 1
        except Exception as ex:
            retry_attempt += 1

    if not is_data_retrived:
        app_logger.error("Error Occurred while getting data from ServiceNow.")
        raise Exception("Error Occurred while getting data from ServiceNow.")


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
        app_logger.debug(f"No files found in {source_bucket_name}/{source_folder}")
        return
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    for blob in blobs:
        source_path = blob.name  # Full path in source bucket

        destination_path = f"{destination_folder}/{source_path.split('/')[-2]}/{timestamp}"

        # Copy file to archive bucket
        source_blob = source_bucket.blob(source_path)
        destination_blob = destination_bucket.blob(destination_path)

        destination_blob.rewrite(source_blob)

        # Optionally delete from source after successful copy (move instead of copy)
        source_blob.delete()

        app_logger.debug(f"Moved {source_path} to {destination_path}")


def write_to_gcs(data, folder_path, file_name, bucket_name, chunk_number, service_account_path=None, file_type="json"):
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials

    bucket = client.bucket(bucket_name)
    # Define GCS path
    blob_name = f"{folder_path}/{file_name}_{chunk_number}.{file_type}"

    blob = bucket.blob(blob_name)
    # Upload string data to the blob
    json_string = json.dumps(data, indent=2)
    blob.upload_from_string(json_string)
    app_logger.debug(f"Uploaded {blob_name} with {len(data['result']['results'])} records")


def read_gcs_to_dataframe_lead(bucket_name: str, file_pattern: str, datatype: dict, encoding: str = "utf-8",
                               file_type: str = 'csv') -> tuple:
    # Initialize the GCS client
    client = storage.Client()

    # Get the GCS bucket
    bucket = client.get_bucket(bucket_name)

    # List all the files in the bucket that match the pattern
    blobs = bucket.list_blobs(prefix=file_pattern)

    dfs_lead = []
    dfs_contact = []
    combined_lead_df = None
    combined_contact_df = None
    for blob in blobs:
        # Download the file content into memory
        # file_content = blob.download_as_text()
        file_path = f"gs://{bucket_name}/{blob.name}"
        app_logger.debug(file_path)
        if blob.size == 0 and blob.name.endswith("/"):
            continue

        if file_type == "csv":
            df = pd.read_csv(file_path, encoding=encoding, dtype=datatype)
            dfs_lead.append(df)
        elif file_type == "json":
            # Convert the content into a DataFrame
            # df = pd.read_json(file_path, encoding=encoding, dtype=datatype)
            # Create a BytesIO object to act as a file-like object
            blob_content = io.BytesIO(blob.download_as_bytes())

            # Decode the bytes to a string (required by json.load)
            text_stream = io.TextIOWrapper(blob_content, encoding=encoding)

            data = json.load(text_stream)

            ls_result = data['result']['results']
            df = pd.DataFrame(ls_result)
            if "membership_number" in df.columns:
                df["membership_number"] = pd.to_numeric(df["membership_number"], errors="coerce")
            df = df.astype(datatype)
            dfs_lead.append(df)
            df_cont = pd.json_normalize(ls_result, 'cont_details')
            if len(df_cont) > 0:
                dfs_contact.append(df_cont)
        else:
            raise Exception("Invalid file Type")

    if len(dfs_lead) > 0:
        # Concatenate all DataFrames into a single one
        combined_lead_df = pd.concat(dfs_lead, ignore_index=True)

    if len(dfs_contact) > 0:
        # Concatenate all DataFrames into a single one
        combined_contact_df = pd.concat(dfs_contact, ignore_index=True)

    return combined_lead_df, combined_contact_df


def read_gcs_to_dataframe(bucket_name: str, file_pattern: str, datatype: dict, encoding: str = "utf-8",
                          file_type: str = 'csv') -> pd.DataFrame:
    # Initialize the GCS client
    client = storage.Client()

    # Get the GCS bucket
    bucket = client.get_bucket(bucket_name)

    # List all the files in the bucket that match the pattern
    blobs = bucket.list_blobs(prefix=file_pattern)

    dfs = []
    combined_df = None
    for blob in blobs:
        app_logger.debug(blob.name)
        if blob.size == 0 and blob.name.endswith("/"):
            continue
        # Download the file content into memory
        # file_content = blob.download_as_text()
        file_path = f"gs://{bucket_name}/{blob.name}"
        app_logger.debug(file_path)
        if file_type == "csv":
            #df = pd.read_csv(file_path, encoding=encoding, dtype=datatype)
            try:
                df = pd.read_csv(file_path, encoding=encoding, dtype=datatype)

            except Exception as e:

                app_logger.warning(f"Datatype error while reading {file_path}: {e}")

                # Read without datatype enforcement
                df = pd.read_csv(file_path, encoding=encoding)

                # Try converting column by column
                for col, dtype in datatype.items():

                    if col not in df.columns:
                        continue

                    try:
                        df[col] = df[col].astype(dtype)

                    except Exception as col_error:

                        bad_rows = df[~df[col].astype(str).str.match(r"^-?\d+$")]

                        if not bad_rows.empty:

                            for _, row in bad_rows.iterrows():

                                ea_util.add_error_audit(
                                    entity_type="pos_id",
                                    entity_id=str(row.get("pos_id", "")),
                                    error_message=f"Invalid datatype for column {col}: {row[col]}",
                                    db_config=database_config,
                                    batch_id=batch_id
                                )

                            # remove bad rows
                            df = df.drop(bad_rows.index)

                        # convert remaining rows
                        df[col] = df[col].astype(dtype)

            dfs.append(df)
        elif file_type == "json":

            blob_content = io.BytesIO(blob.download_as_bytes())

            # Decode the bytes to a string (required by json.load)
            text_stream = io.TextIOWrapper(blob_content, encoding=encoding)

            data = json.load(text_stream)

            ls_result = data['result']['results']
            df = pd.DataFrame(ls_result)
            df = df.astype(datatype)
            dfs.append(df)
        else:
            raise Exception("Invalid file Type")

    if len(dfs) > 0:
        # Concatenate all DataFrames into a single one
        combined_df = pd.concat(dfs, ignore_index=True)

    return combined_df


def read_gcs_to_dataframe_as_list(bucket_name: str, file_pattern: str, datatype: dict, encoding: str = "utf-8",
                                  file_type: str = 'csv') -> list[pd.DataFrame]:
    # Initialize the GCS client
    client = storage.Client()

    # Get the GCS bucket
    bucket = client.get_bucket(bucket_name)

    # List all the files in the bucket that match the pattern
    blobs = bucket.list_blobs(prefix=file_pattern)

    dfs = []
    for blob in blobs:
        if blob.size == 0 and blob.name.endswith("/"):
            continue
        # Download the file content into memory
        # file_content = blob.download_as_text()
        file_path = f"gs://{bucket_name}/{blob.name}"
        app_logger.debug(file_path)
        if file_type == "csv":
            df = pd.read_csv(file_path, encoding=encoding, dtype=datatype)
            dfs.append(df)
        elif file_type == "json":
            # Convert the content into a DataFrame
            # df = pd.read_json(file_path, encoding=encoding, dtype=datatype)
            # Create a BytesIO object to act as a file-like object
            blob_content = io.BytesIO(blob.download_as_bytes())

            # Decode the bytes to a string (required by json.load)
            text_stream = io.TextIOWrapper(blob_content, encoding=encoding)

            data = json.load(text_stream)

            ls_result = data['result']['results']
            df = pd.DataFrame(ls_result)
            df = df.astype(datatype)
            dfs.append(df)
        else:
            raise Exception("Invalid file Type")

    # Concatenate all DataFrames into a single one
    # combined_df = pd.concat(dfs, ignore_index=True)

    return dfs


def transform_lead(raw_df, rename_dic, batch_id):
    raw_df['batch_id'] = batch_id
    raw_df['address_line_two'] = ""
    raw_df['updated_by'] = "Service Now"
    # raw_df['type'] = ""
    # raw_df['industry_code'] = pd.to_numeric(raw_df['industry_code'])
    # raw_df['industry_code'] = raw_df['industry_code'].astype('Int64')
    # raw_df['warehouse_number'] = pd.to_numeric(raw_df['warehouse_number'])
    # raw_df['warehouse_number'] = raw_df['warehouse_number'].astype('Int64')
    trans_df = raw_df.rename(columns=rename_dic)

    return trans_df


def transform_contact(raw_df, rename_dic, batch_id):
    raw_df['batch_id'] = batch_id
    raw_df['updated_by'] = "Service Now"
    trans_df = raw_df.rename(columns=rename_dic)
    return trans_df


def df_write_to_sql(df, table_name, schema, connection, dype_dict, chunksize=1000):
    if df:
        app_logger.debug(f"writing {table_name} data")
        df.to_sql(table_name, con=connection, schema=schema, if_exists="append", index=False,
                  method="multi", chunksize=chunksize,
                  dtype=dype_dict)
    else:
        app_logger.debug(f"dataframe is None. so data inserted to {table_name} ")


def write_lead_data_to_db(batch_id, job_config, chunk_size=1000):
    app_logger.info("inside write_lead_data_to_db")
    if not batch_id:
        batch_id = uuid.uuid4()

    storage_config: StorageConfig = job_config.storage_config
    database_config: DatabaseDetail = job_config.db_config
    transform_config: TransformConfig = job_config.transform_config
    data_load_type = job_config.data_load_type
    file_type = job_config.file_type
    encoding = job_config.file_encoding

    ba_util.add_batch_id(batch_id, "lead", "db_load", "Started", database_config)

    lead_rename_dict = transform_config.initial_load_lead_mapping
    contact_rename_dict = transform_config.delta_load_contact_mapping
    lead_type_dict = transform_config.initial_load_lead_datatype_mapping

    delta_rename_dict = transform_config.delta_load_lead_mapping
    delta_lead_data_type = transform_config.delta_load_lead_datatype_mapping
    contact_type_dict = {}
    input_count = 0
    output_count = 0
    if data_load_type == 'initial':
        app_logger.info("*******************Doing initial load of data")
        df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                   file_pattern=storage_config.lead_input_folder,
                                   datatype=lead_type_dict, encoding=encoding, file_type=file_type)
        cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                        file_pattern=storage_config.contact_input_folder,
                                        datatype=contact_type_dict, encoding=encoding,
                                        file_type=file_type)
        cleansed_lead_df = transform_lead(df, lead_rename_dict, batch_id)
        contact_df = None
        account_df = None
        lead_df = None
        if cont_df is not None:
            contact_df = transform_contact(cont_df, contact_rename_dict, batch_id)

        if df is not None:
            account_df, lead_df = split_lead_account_bo(cleansed_lead_df, batch_id,transform_config)
            input_count = len(df)
            output_count = len(df)
        schema = database_config.schema_name

        with database_config.get_engine().connect() as connection:
            with connection.begin():
                acct_dtype = {"batch_id": UUID(as_uuid=True), "industry_code": Integer,
                              "fiscal_period": Integer, "fiscal_year": Integer, "warehouse_number": Integer}
                cont_dtype = {"batch_id": UUID(as_uuid=True)}
                lead_dtype = {"batch_id": UUID(as_uuid=True), "membership_number": BigInteger}
                df_write_to_sql(account_df, "account", schema, connection, acct_dtype, chunksize=chunk_size)
                df_write_to_sql(lead_df, "lead", schema, connection, lead_dtype, chunksize=chunk_size)
                df_write_to_sql(contact_df, "contact", schema, connection, cont_dtype, chunksize=chunk_size)
                app_logger.info("data write completed successfully")

        ba_util.update_batch_id(batch_id, 'lead', "db_load", input_count, output_count, "Completed", database_config)
    elif data_load_type == 'delta':

        app_logger.info("****************Doing delta load of data")
        df, cont_df = read_gcs_to_dataframe_lead(bucket_name=storage_config.input_bucket_name,
                                                 file_pattern=storage_config.lead_input_folder,
                                                 datatype=delta_lead_data_type, encoding=encoding, file_type=file_type)

        cleansed_contact_df = None
        cleansed_lead_df = None
        if cont_df is not None:
            cleansed_contact_df = transform_contact(cont_df, contact_rename_dict, batch_id)
        if df is not None:
            cleansed_lead_df = transform_lead(df, delta_rename_dict, batch_id)
            app_logger.debug("data present in dataframe")
            input_count = len(cleansed_lead_df)
            app_logger.debug(f"data present in dataframe - count {input_count}")

        schema = database_config.schema_name
        try:

            account_df, contact_df, lead_df = split_lead_bo(cleansed_lead_df, cleansed_contact_df, batch_id,
                                                            transform_config,job_config)
            #app_logger.debug("upsert account data")
            #upsert_using_primary_key(account_df, f"{schema}.account", "account_id", database_config,batch_id)
            app_logger.debug("upsert lead data")
            lead_success, lead_failure, failed_ids = upsert_using_primary_key(lead_df, f"{schema}.lead", "lead_id", database_config,batch_id)
            app_logger.debug("upsert contact data")
            upsert_using_primary_key(contact_df, f"{schema}.contact", "contact_id", database_config,batch_id)

            ba_util.update_batch_id(batch_id, 'lead', "db_load", input_count, lead_success, "Completed",
                                    database_config)
        except Exception as ex:
            app_logger.error("ERROR: load to database failed")
            app_logger.error(ex)
            ba_util.update_batch_id(batch_id, "lead", "db_load", input_count, output_count,
                                    "Failed", database_config)
            raise ex


def transform_pos(df, transform_dict):
    transform_data = None
    if df is not None:
        transform_data = df.rename(columns=transform_dict)
        batch_id = uuid.uuid4()
        transform_data['batch_id'] = batch_id

    return transform_data


def write_pos_data_to_db(batch_id, job_config: JobConfig, chunk_size=1000):
    if not batch_id:
        batch_id = uuid.uuid4()

    storage_config: StorageConfig = job_config.storage_config
    database_config: DatabaseDetail = job_config.db_config
    transform_config: TransformConfig = job_config.transform_config
    data_load_type = job_config.data_load_type
    file_type = job_config.file_type
    encoding = job_config.file_encoding

    ba_util.add_batch_id(batch_id, "pos", "db_load", "Started", database_config)

    pos_record_count_before_load = get_table_row_count(database_config, "transaction")

    success_count = 0
    failure_count = 0
    total_count = 0
    try:
        if data_load_type == "initial":
            data_type_dict = transform_config.initial_load_pos_datatype_mapping
            pos_df = read_gcs_to_dataframe(storage_config.input_bucket_name,
                                           f"{storage_config.pos_input_folder}/", datatype=data_type_dict,
                                           encoding=encoding, file_type=file_type)
            if pos_df:
                rename_dict = transform_config.initial_load_pos_mapping
                transformed_df = transform_pos(pos_df, rename_dict)
                ls_pos_columns = transform_config.pos_columns
                transformed_df = transformed_df[ls_pos_columns]
                total_count = len(transformed_df)

                with database_config.get_engine().begin() as conn:
                    transformed_df.to_sql("transaction", con=conn, schema=database_config.schema_name,
                                          if_exists="append",
                                          index=False,
                                          method="multi", chunksize=chunk_size,
                                          dtype={"batch_id": UUID(as_uuid=True)})
                    app_logger.debug("Transaction data inserted into database")

            ba_util.update_batch_id(batch_id, "pos", "db_load", len(transformed_df), len(transformed_df),
                                    "Completed", database_config)
        elif data_load_type == "delta":
            data_type_dict = transform_config.delta_load_pos_datatype_mapping
            pos_df = read_gcs_to_dataframe(storage_config.input_bucket_name,
                                           f"{storage_config.pos_input_folder}/", datatype=data_type_dict,
                                           encoding=encoding, file_type=file_type)
            if pos_df is not None:
                rename_dict = transform_config.delta_load_pos_mapping
                transformed_df = transform_pos(pos_df, rename_dict)
                ls_pos_columns = transform_config.pos_columns
                transformed_df = transformed_df[ls_pos_columns]
                total_count = len(transformed_df)
                # upsert_pos_data(transformed_df, database_config)
                print("before invoking upsert_using_primary_key ")
                success_count, failure_count, failed_ids = upsert_using_primary_key(transformed_df,
                                                        f"{database_config.schema_name}.transaction",
                                                         "pos_id",
                                                                        database_config,batch_id)
                ba_util.update_batch_id(batch_id, "pos", "db_load", total_count, success_count,
                                        "Completed", database_config)
                pos_record_count_after_load = get_table_row_count(database_config, "transaction")

                if pos_record_count_after_load > pos_record_count_before_load:
                    app_logger.warning("New sales records added to transaction table . Match will be triggered")
                    trigger_match_job(job_config)
                else:
                    app_logger.warning("No Change in sales records count in transaction table . Match job will NOT be triggered")
    except Exception as ex:
        app_logger.error("Error occurred while loading POS data into Database")
        app_logger.error(ex)
        app_logger.exception(ex)
        import traceback
        app_logger.debug(traceback.format_exc())
        ba_util.update_batch_id(batch_id, "pos", "db_load", total_count, success_count,
                                "Failed", database_config)
        raise ex

    ba_util.update_batch_id(batch_id, "pos", "db_load", total_count, success_count,
                            "Completed", database_config)


def read_lead_data(batch_id, snow_config: SnowConfig, gcs_config: StorageConfig, database_config: DatabaseDetail,

                   batch_size=10000):
    if not batch_id:
        batch_id = uuid.uuid4()
    ba_util.add_batch_id(batch_id, "lead", "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, snow_config, "lead")
    start_index = 1
    # batch_size = snow_config.max_batch_size
    end_index = batch_size
    data_found = True
    total_rec_count = 0
    try:
        # Archive existing old files from gcs folder
        archive_gcs_files(gcs_config.input_bucket_name, gcs_config.lead_input_folder,
                          gcs_config.archive_bucket_name, gcs_config.archive_folder)
        while data_found:
            payload = {
                "start_index": start_index,
                "end_index": end_index,
                "start_date": start_date,
                "end_date": end_date}
            output_data = read_data_from_snow(snow_config.lead_url, snow_config.snow_user, snow_config.snow_password,
                                              payload)

            if int(output_data['result']['returned_count']) > 0 and len(output_data['result']['returned_count']) > 0:
                # output_data, folder_path, file_name, bucket_name, chunk_number,service_account_path=None,file_type="json"
                write_to_gcs(output_data, gcs_config.lead_input_folder, "lead_data", gcs_config.input_bucket_name,
                             end_index, file_type="json")
                start_index = start_index + batch_size
                end_index = end_index + batch_size
                total_rec_count = total_rec_count + len(output_data['result'])
            else:
                data_found = False
        ba_util.update_batch_id(batch_id, "lead", "staging", total_rec_count, total_rec_count,
                                "Completed", database_config)
    except Exception as exc:
        app_logger.error("Error while getting LEAD data from  service Now")
        ba_util.update_batch_id(batch_id, "lead", "staging", total_rec_count, total_rec_count,
                                "Failed", database_config)
        raise exc
    return total_rec_count


def read_pos_data(batch_id, snow_config: SnowConfig, gcs_config: StorageConfig, database_config: DatabaseDetail,
                  batch_size=10000):
    if not batch_id:
        batch_id = uuid.uuid4()
    ba_util.add_batch_id(batch_id, "pos", "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, snow_config, "pos")
    start_index = 1
    # batch_size = snow_config.max_batch_size
    end_index = batch_size
    data_found = True
    total_rec_count = 0

    try:
        # Archive existing old files from gcs folder
        # archive_gcs_folder(gcs_config)
        archive_gcs_files(gcs_config.input_bucket_name, gcs_config.pos_input_folder,
                          gcs_config.archive_bucket_name, gcs_config.archive_folder)
        while data_found:
            payload = {
                "start_index": start_index,
                "end_index": end_index,
                "start_date": start_date,
                "end_date": end_date}
            output_data = read_data_from_snow(snow_config.pos_url, snow_config.snow_user, snow_config.snow_password,
                                              payload)

            if int(output_data['result']['returned_count']) > 0 and len(output_data['result']['returned_count']) > 0:
                # output_data, folder_path, file_name, bucket_name, chunk_number,service_account_path=None,file_type="json"
                write_to_gcs(output_data, gcs_config.pos_input_folder, "pos_data", gcs_config.input_bucket_name,
                             end_index, file_type="json")
                start_index = start_index + batch_size
                end_index = end_index + batch_size
                total_rec_count = total_rec_count + len(output_data['result']['results'])
            else:
                data_found = False

        ba_util.update_batch_id(batch_id, "pos", "staging", total_rec_count, total_rec_count,
                                "Completed", database_config)

    except Exception as exc:
        app_logger.error("Error while getting POS data from  service Now")
        ba_util.update_batch_id(batch_id, "pos", "staging", total_rec_count, total_rec_count,
                                "Failed", database_config)
        raise exc
    return total_rec_count


def get_record_snow_to_gcs(batch_id, data_type: str, job_config: JobConfig):
    if not batch_id:
        batch_id = uuid.uuid4()

    gcs_config: StorageConfig = job_config.storage_config
    database_config: DatabaseDetail = job_config.db_config
    snow_config: SnowConfig = job_config.snow_config

    ba_util.add_batch_id(batch_id, data_type, "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, snow_config, data_type, )
    start_index = 1
    batch_size = int(os.getenv("BATCH_SIZE", snow_config.max_batch_size))
    end_index = batch_size
    data_found = True
    total_rec_count = 0
    try:
        # Archive existing old files from gcs folder
        input_folder = gcs_config.input_folders[data_type]
        archive_gcs_files(gcs_config.input_bucket_name, input_folder,
                          gcs_config.archive_bucket_name, gcs_config.archive_folder)
        while data_found:
            payload = {
                "start_index": start_index,
                "end_index": end_index,
                "start_date": start_date,
                "end_date": end_date}
            output_data = read_data_from_snow(snow_config.api_urls[data_type], snow_config.snow_user,
                                              snow_config.snow_password,
                                              payload)

            if int(output_data['result']['returned_count']) > 0 and len(output_data['result']['returned_count']) > 0:
                # output_data, folder_path, file_name, bucket_name, chunk_number,service_account_path=None,file_type="json"
                write_to_gcs(output_data, input_folder, f"{data_type}_data", gcs_config.input_bucket_name,
                             end_index, file_type="json")
                start_index = start_index + batch_size
                end_index = end_index + batch_size
                total_rec_count = total_rec_count + len(output_data['result']['results'])
            else:
                data_found = False

        ba_util.update_batch_id(batch_id, data_type, "staging", total_rec_count, total_rec_count,
                                "Completed", database_config)

    except Exception as exc:
        app_logger.error("Error while getting POS data from  service Now")
        ba_util.update_batch_id(batch_id, data_type, "staging", total_rec_count, total_rec_count,
                                "Failed", database_config)
        raise exc
    return total_rec_count


def get_date_range(db_config: DatabaseDetail, snow_config: SnowConfig, data_type: str = 'lead'):
    utc_now = datetime.now(timezone.utc)
    # the UTC datetime
    app_logger.info("Current UTC datetime:", utc_now)
    # You can also format it as a string if needed

    batch_id, load_date = ba_util.get_latest_batch_by_status(db_config, data_type, "staging", "completed")

    end_date = utc_now.strftime("%Y-%m-%d %X")
    if load_date:
        start_date = load_date.strftime("%Y-%m-%d %X")
    else:
        start_date = snow_config.default_start_date  # project go live date

    return start_date, end_date


def get_data_from_snow(batch_id, job_config: JobConfig, ):
    app_logger.info("inside get_data_from_snow ")

    if not batch_id:
        batch_id = uuid.uuid4()

    get_record_snow_to_gcs(batch_id, "lead", job_config)
    get_record_snow_to_gcs(batch_id, "pos", job_config)

    return batch_id


def load_data_to_db(batch_id, config: JobConfig):
    app_logger.info("inside load_data_to_db ")
    if not batch_id:
        batch_id = uuid.uuid4()

    write_lead_data_to_db(batch_id, config)
    write_pos_data_to_db(batch_id, config)


def publish_message(project_id: str, topic_name: str, message: str) -> None:
    """Publishes a message to a Pub/Sub topic."""
    try:
        app_logger.info("inside publish message method")
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project_id, topic_name)

        # Data must be a bytestring
        data = message.encode("utf-8")

        # When you publish a message, the client returns a future.
        future = publisher.publish(topic_path, data=data)
        app_logger.info(f"Published message to {topic_path}: {future.result()}")

    except Exception as e:
        app_logger.error(f"Error publishing message: {e}")


def trigger_cloud_run_job(project_id, location, job_name):
    app_logger.info("inside gcs trigger cloud run job")

    try:
        # Create a client
        client = run_v2.JobsClient()
        job_full_name = f"projects/{project_id}/locations/{location}/jobs/{job_name}"
        app_logger.info(job_full_name)
        # Initialize request argument(s)
        request = run_v2.RunJobRequest(name=job_full_name)

        # Make the request
        response = client.run_job(request=request)

        app_logger.debug("wait for few seconds job to start")
        time.sleep(20)
        response = response.operation.name

        # Handle the response
        app_logger.info(response)
        return response
    except Exception as ex:
        app_logger.error("Issue occurred while invoking Match job")
        app_logger.error(ex)
        raise ex


def trigger_match_job(job_config: JobConfig):
    project_id = job_config.gcp_project_id
    location = job_config.location
    job_name = job_config.match_job_name
    # publish_message(project_id, topic_name, message_to_publish)
    out = trigger_cloud_run_job(project_id, location, job_name)
    app_logger.info(f"Matching pipeline triggered - {out}")


def send_message_to_pubsub(project_id, topic_name, message_to_publish):
    publish_message(project_id, topic_name, message_to_publish)



