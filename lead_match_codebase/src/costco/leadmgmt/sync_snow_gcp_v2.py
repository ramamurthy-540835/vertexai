import io
import time
from datetime import datetime, timezone
import requests
import sys
import uuid
import sqlalchemy
import pandas as pd
from google.cloud import storage, pubsub_v1
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, text, BigInteger
import json
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from costco.leadmgmt.database.DBUtil import DatabaseDetail, TransactionBO
from costco.leadmgmt.config.Configuration import JobConfig, StorageConfig, SnowConfig, TransformConfig
from google.cloud import run_v2



def split_lead_account_bo(lead_df, batch_id):
    acct_dedup_columns = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'business_name']
    ls_account_columns = ["account_id", "batch_id", "bd_industry", "address_line_one", "address_line_two", "city",
                          "business_name", "phone", "state",
                          "zip_code", "industry_code", "type"]
    ls_lead_columns = ["lead_id", "account_id", "contact_id", "lead_status", "batch_id",
                       "membership_number", "warehouse_number", "fiscal_year", "fiscal_period"]

    print("inside split lead bo columns")
    print(lead_df.columns)
    lead_df["batch_id"] = batch_id

    account_df = lead_df[ls_account_columns]
    final_lead_df = lead_df[ls_lead_columns]
    print(f"length of account before dedup {len(account_df)}")
    account_df = account_df.drop_duplicates(subset=["account_id"], keep='first')
    print(f"length of account after dedup {len(account_df)}")
    return account_df, final_lead_df


def split_lead_bo(lead_df, contact_df, batch_id):
    acct_dedup_columns = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'business_name']
    ls_account_columns = ["account_id", "batch_id", "bd_industry", "address_line_one", "address_line_two", "city",
                          "business_name", "phone", "state",
                          "zip_code", "industry_code", "type"]
    ls_lead_columns = ["lead_id", "account_id", "contact_id", "lead_status", "batch_id",
                       "membership_number", "warehouse_number", "fiscal_year", "fiscal_period"]
    ls_contact_columns = ["account_id", "contact_id", "first_name", "last_name", "email", "batch_id"]

    print("inside split lead bo columns")
    print(lead_df.columns)
    lead_df["batch_id"] = batch_id
    contact_df["batch_id"] = batch_id
    account_df = lead_df[ls_account_columns]
    final_lead_df = lead_df[ls_lead_columns]
    contact_df = contact_df[ls_contact_columns]
    print(f"length of account before dedup {len(account_df)}")
    account_df = account_df.drop_duplicates(subset=["account_id"], keep='first')
    print(f"length of account after dedup {len(account_df)}")
    return account_df, contact_df, final_lead_df


def add_batch_id(batch_id: str, data_type: str, stage: str, status: str,
                 db_config):
    try:

        params = {"batch_id": batch_id, "data_type": data_type, "stage": stage, "status": status}
        insert_query = f"insert into {db_config.schema_name}.batch_audit (batch_id,data_type,stage,status) values( :batch_id,:data_type" \
                       f",:stage,:status);"
        with db_config.get_engine().begin() as conn:
            conn.execute(text(insert_query), params)
            print(f"batch id added successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")

        raise e


def update_batch_id(batch_id: str, data_type: str, stage: str, total_count: int, success_count: int, status: str,
                    db_config: DatabaseDetail):
    try:

        schema = db_config.schema_name
        params = {"batch_id": batch_id, "data_type": data_type, "total_volume": total_count,
                  "success_count": success_count,
                  "status": status, "stage": stage}
        update_query = f"update {schema}.batch_audit set total_volume=:total_volume, success_count =:success_count, " \
                       f"end_date=current_timestamp ,status =:status where batch_id = :batch_id and data_type = :data_type and stage =:stage ;"
        with db_config.get_engine().begin() as conn:
            conn.execute(text(update_query), params)
            print(f"batch id update successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")
        with db_config.get_engine().connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e


def get_latest_batch_id(db_config: DatabaseDetail, data_type, stage):
    batch_id = None
    status = None
    try:

        schema = db_config.schema_name
        params = {"data_type": data_type, "stage": stage}
        select_query = f"select batch_id,status from {schema}.batch_audit where  data_type = :data_type and stage =:stage order by load_date desc limit 1 ;"
        print(select_query)
        with db_config.get_engine().begin() as conn:
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


def get_latest_batch_by_status(db_config: DatabaseDetail, data_type, stage, status):
    batch_id = None
    load_date = None
    try:

        schema = db_config.schema_name
        params = {"data_type": data_type, "stage": stage, "status": status}
        select_query = (f"select batch_id,load_date from {schema}.batch_audit where  data_type = :data_type and "
                        f"stage =:stage and lower(status) = lower(:status) order by load_date desc limit 1 ;")
        print(select_query)
        with db_config.get_engine().begin() as conn:
            result = conn.execute(text(select_query), params)
            first_result = result.first()
            if first_result:
                batch_id = first_result[0]
                load_date = first_result[1]
            return batch_id, load_date
    except Exception as ex:
        print("Error happened reading  batch audit data")
        print(ex)
        raise ex


def get_table_row_count(db_config: DatabaseDetail, table_name ):
    row_count = 0
    try:

        schema = db_config.schema_name
        select_query = f"select count(1) from {schema}.{table_name};"
        print(select_query)
        with db_config.get_engine().begin() as conn:
            result = conn.execute(text(select_query))
            first_result = result.first()
            if first_result:
                row_count = first_result[0]

    except Exception as ex:
        print("Error happened reading  batch audit data")
        print(ex)
        raise ex

    return row_count

def find_duplicate_values(df, column_name):
    """Returns a list of duplicate values in a column."""
    return df[df.duplicated(subset=[column_name])][column_name].unique().tolist()


def update_account_id_in_contact(contact_df, df_account):
    df_merged = contact_df.merge(df_account, left_on='account_id', right_on='temp_account_id', how='right')
    print(f"merged data length contact {len(df_merged)}")
    df_merged = df_merged.drop(columns=['account_id_x', 'temp_account_id'])
    contact = df_merged.rename(columns={'account_id_y': 'account_id'})
    print(f"merged contact {len(contact)}")
    # contact = contact[contact['contact_id'] not None]
    return contact


def update_account_id_in_lead(lead_df, df_account):
    df_merged = lead_df.merge(df_account, left_on='account_id', right_on='temp_account_id', how='right')
    df_merged = df_merged.drop(columns=['account_id_x', 'temp_account_id'])
    lead = df_merged.rename(columns={'account_id_y': 'account_id'})

    return lead


def upsert_using_business_key(df, table_name, unique_key_columns, primary_key_column, update_column,
                              db_config: DatabaseDetail, temp_id_column="temp_id"):
    try:
        print("inside upsert_dataframe_sqlalchemy")

        print("upsert dataframe")
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
                # print(insert_query)
                row_data = row.to_dict()
                del row_data[temp_id_column]

                result = connection.execute(insert_query, row_data)
                # print(result)
                # print("result")
                primary_key = result.scalar()
                if primary_key != row_data[primary_key_column]:
                    df.loc[index, primary_key_column] = primary_key
                    df.loc[index, temp_id_column] = row_data[primary_key_column]

            connection.commit()
            out_df = df[[primary_key_column, temp_id_column]]
            return out_df
    except Exception as e:
        print(f"Error: {e}")
        print(e)
        raise


def upsert_using_primary_key(df, table_name, primary_key_column, db_config: DatabaseDetail):
    log_limit = 1000
    total_error_record = 0
    total_success_count = 0
    max_error_limit = 10
    try:
        print("inside upsert_dataframe_sqlalchemy")

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
        # print(insert_query)

        with db_config.get_engine().connect() as connection:
            for index, row in df.iterrows():
                # update_column = 'batch_id'
                try:

                    # Convert NaN values to None for SQL NULL
                    row_data = {col: None if pd.isna(value) else value for col, value in row.items()}
                    # row_data = row.to_dict()
                    result = connection.execute(insert_query, row_data)
                    total_success_count += 1
                except Exception as e:
                    print(
                        f"Error UPSERTing row with index {index} and {primary_key_column} = {row[primary_key_column]}: {e}")
                    total_error_record += 1

                if index % log_limit == 0:
                    print(f"processed {index} records ")
                    connection.commit()
                if total_error_record > max_error_limit:
                    raise Exception(f"Number of records failure reached max limit {max_error_limit} ")
            connection.commit()
            print(f"Total records processed successfully :{total_success_count}")
    except Exception as e:
        print(f"Error occurred while insert/update records to table {table_name} ")
        print(e)
        raise
    return total_success_count, total_error_record


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
                print('Status:', response.status_code, 'Headers:', response.headers, 'Error Response:', response.json())
                print(f"Connecting to Service Now API failed with status code - {response.status_code}")
                retry_attempt += 1
        except Exception as ex:
            retry_attempt += 1


    if not is_data_retrived:
        raise Exception("Error Occurred while getting data from ServiceNow.")


def archive_gcs_folder(gcs_config):
    pass


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

        destination_path = f"{destination_folder}/{source_path.split('/')[-1]}/{timestamp}"

        # Copy file to archive bucket
        source_blob = source_bucket.blob(source_path)
        destination_blob = destination_bucket.blob(destination_path)

        destination_blob.rewrite(source_blob)

        # Optionally delete from source after successful copy (move instead of copy)
        source_blob.delete()

        print(f"Moved {source_path} to {destination_path}")


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
    print(f"Uploaded {blob_name} with {len(data['result']['results'])} records")


def read_gcs_to_dataframe(bucket_name: str, file_pattern: str, datatype: dict, encoding: str = "utf-8",
                          file_type: str = 'csv') -> pd.DataFrame:
    # Initialize the GCS client
    client = storage.Client()

    # Get the GCS bucket
    bucket = client.get_bucket(bucket_name)

    # List all the files in the bucket that match the pattern
    blobs = bucket.list_blobs(prefix=file_pattern)

    dfs = []
    for blob in blobs:
        # Download the file content into memory
        # file_content = blob.download_as_text()
        file_path = f"gs://{bucket_name}/{blob.name}"
        print(file_path)
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
        # Download the file content into memory
        # file_content = blob.download_as_text()
        file_path = f"gs://{bucket_name}/{blob.name}"
        print(file_path)
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
    #combined_df = pd.concat(dfs, ignore_index=True)

    return dfs


def transform_lead(raw_df, rename_dic, batch_id):
    raw_df['batch_id'] = batch_id
    raw_df["lead_status"] = "New"
    raw_df["email"] = ""
    raw_df['address_line_two'] = ""
    trans_df = raw_df.rename(columns=rename_dic)

    print(trans_df.head())

    return trans_df


def write_lead_data_to_db(batch_id, storage_config, database_config, transform_config:TransformConfig,data_load_type="initial"):
    print("inside write_lead_data_to_db")
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, "lead", "db_load", "Started", database_config)

    lead_rename_dict =  transform_config.initial_load_lead_mapping
    contact_rename_dict = {"contact": "contact_id", "account": "account_id", "email": "email", "phone": "phone",
                           "first_name": "first_name", "last_name": "last_name"}
    lead_type_dict = transform_config.initial_load_lead_datatype_mapping

    delta_rename_dict =  transform_config.delta_load_lead_mapping
    delta_lead_data_type = transform_config.delta_load_lead_datatype_mapping


    contact_type_dict = {}

    input_count = 0
    output_count = 0

    if data_load_type == 'initial':
        print("*******************Doing initial load of data")
        df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                   file_pattern=storage_config.lead_input_folder,
                                   datatype=lead_type_dict, encoding="ISO8859-1", file_type='csv')
        cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                        file_pattern=storage_config.contact_input_folder,
                                        datatype=contact_type_dict, encoding="ISO8859-1",
                                        file_type='csv')
        cleansed_lead_df = transform_lead(df, lead_rename_dict, batch_id)
        cleansed_contact_df = transform_lead(cont_df, contact_rename_dict, batch_id)
        account_df, contact_df, lead_df = split_lead_bo(cleansed_lead_df, cleansed_contact_df, batch_id)
        schema = database_config.schema_name

        with database_config.get_engine().connect() as connection:
            with connection.begin():
                print("writing account data")
                account_df.to_sql("account", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=1000,
                                  dtype={"batch_id": UUID(as_uuid=True), "industry_code": Integer,
                                         "fiscal_period": Integer, "fiscal_year": Integer, "warehouse_number": Integer})
                print("writing contact data")
                contact_df.to_sql("contact", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=1000, dtype={"batch_id": UUID(as_uuid=True)})
                print("writing lead data")
                lead_df.to_sql("lead", con=connection, schema=schema, if_exists="append", index=False,
                               method="multi", chunksize=1000,
                               dtype={"batch_id": UUID(as_uuid=True), "membership_number": BigInteger})
                print("data write completed successfully")

        update_batch_id(batch_id, 'lead', "db_load", len(lead_df), len(lead_df), "Completed", database_config)
    elif data_load_type == 'delta':

        print("****************Doing delta load of data")
        df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                   file_pattern=storage_config.lead_input_folder,
                                   datatype=lead_type_dict, encoding="ISO8859-1", file_type='json')
        cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                        file_pattern=storage_config.lead_input_folder,
                                        datatype=contact_type_dict, encoding="ISO8859-1",
                                        file_type='json')
        cleansed_lead_df = transform_lead(df, delta_rename_dict, batch_id)
        cleansed_contact_df = transform_lead(cont_df, contact_rename_dict, batch_id)
        schema = database_config.schema_name
        try:
            # cleansed_df = read_data_from_source(staging_bucket, staging_blob, job_config)
            input_count = len(cleansed_lead_df)
            account_df, contact_df, lead_df = split_lead_bo(cleansed_lead_df, cleansed_contact_df, batch_id)
            print("upsert account data")
            upsert_using_primary_key(account_df, f"{schema}.account", "account_id", database_config)
            print("upsert contact data")
            upsert_using_primary_key(contact_df, f"{schema}.contact", "contact_id", database_config)
            print("upsert lead data")
            lead_success, lead_failure = upsert_using_primary_key(lead_df, f"{schema}.lead", "lead_id", database_config)
            print(f'{"batch_id":"{batch_id}" }')
            update_batch_id(batch_id, 'lead', "db_load", lead_success, lead_failure, "Completed", database_config)
        except Exception as ex:
            print("ERROR: load to database failed")
            print(ex)
            update_batch_id(batch_id, "lead", "db_load", len(cleansed_lead_df), 0,
                            "Failed", database_config)


def process_contact(batch_id, storage_config, database_config, data_load_type="initial"):
    contact_rename_dict = {"contact": "contact_id", "account": "account_id", "email": "email", "phone": "phone",
                           "first_name": "first_name", "last_name": "last_name"}
    lead_type_dict = {'case': str, 'account.u_warehouse_number': 'Int64', 'account': str, 'contact': str,
                      'account.number': str, 'account.u_industry_code.u_code_value': str,
                      'account.u_bd_industry': str, 'account.phone': str,
                      'account.street': str, 'account.city': str, 'account.state': str, 'account.zip': str,
                      'u_type': str,
                      'u_membership_number': 'Int64', 'u_confidence_level': str, 'u_fiscal_year': 'Int16',
                      'u_period': 'Int16'}
    contact_type_dict = {"contact": str}
    cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                    file_pattern=storage_config.lead_input_folder,
                                    datatype=contact_type_dict, encoding="ISO8859-1",
                                    file_type='json')

    cleansed_contact_df = transform_lead(cont_df, contact_rename_dict, batch_id)
    schema = database_config.schema_name
    try:
        # cleansed_df = read_data_from_source(staging_bucket, staging_blob, job_config)
        contact_df = cleansed_contact_df
        contact_df['batch_id'] = batch_id
        print("upsert contact data")
        upsert_using_primary_key(contact_df, f"{schema}.contact", "contact_id", database_config)
    except Exception as ex:
        print("ERROR: load to database failed")
        print(ex)
        update_batch_id(batch_id, "lead", "db_load", 0, 0,
                        "Failed", database_config)


def process_lead_n_account(batch_id, storage_config, database_config, data_load_type="initial"):
    delta_rename_dict = {'lead_number': 'lead_id', 'account_u_warehouse_number': 'warehouse_number',
                         'account': 'account_id',
                         'contact_id': 'contact_id',
                         'phone': 'phone', 'street': 'address_line_one', 'city': 'city', 'state': 'state',
                         'zipcode': 'zip_code',
                         'country': 'country', 'bd_industry': 'bd_industry', 'sic6': 'industry_code',
                         'membership_number': 'membership_number',
                         'confidence_level': 'confidence_level', 'period': 'fiscal_period', 'u_status': 'lead_status'}
    delta_lead_data_type = {'lead_number': str, 'account_u_warehouse_number': 'Int64', 'account': str,
                            'contact_id': 'Int64',
                            'phone': str, 'street': str, 'city': str, 'state': str, 'zipcode': str,
                            'country': str, 'bd_industry': 'Int64', 'sic6': 'Int64', 'membership_number': 'Int64',
                            'confidence_level': str, 'period': 'int64', 'u_status': str}
    df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                               file_pattern=storage_config.lead_input_folder,
                               datatype=delta_lead_data_type, encoding="ISO8859-1", file_type='json')
    cleansed_lead_df = transform_lead(df, delta_rename_dict, batch_id)
    schema = database_config.schema_name
    try:
        input_count = len(cleansed_lead_df)

        account_df, lead_df = split_lead_account_bo(cleansed_lead_df, batch_id)
        print("upsert account data")
        upsert_using_primary_key(account_df, f"{schema}.account", "account_id", database_config)
        lead_success, lead_failure = upsert_using_primary_key(lead_df, f"{schema}.lead", "lead_id", database_config)
        print(f'{"batch_id":"{batch_id}" }')
        update_batch_id(batch_id, 'lead', "db_load", lead_success, lead_failure, "Completed", database_config)

    except Exception as ex:
        print("ERROR: load to database failed")
        print(ex)
        update_batch_id(batch_id, "lead", "db_load", len(cleansed_lead_df), 0,
                        "Failed", database_config)


def write_lead_data_to_db_v1(batch_id, storage_config, database_config, data_load_type="initial"):
    print("inside write_lead_data_to_db")
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, "lead", "db_load", "Started", database_config)

    lead_rename_dict = {"case": "lead_id", "account.u_warehouse_number": "warehouse_number", "account": "business_name"
        , "contact": "contact_id", "account.number": "account_id",
                        "account.u_industry_code.u_code_value": "industry_code", "account.u_bd_industry": "bd_industry"
        , "account.phone": "phone", "account.street": "address_line_one", "account.city": "city",
                        "account.state": "state"
        , "account.zip": "zip_code", "account.country": "country", "u_type": "type",
                        "u_membership_number": "membership_number"
        , "u_confidence_level": "confidence_level", "u_fiscal_year": "fiscal_year", "u_period": "fiscal_period"}
    contact_rename_dict = {"contact": "contact_id", "account": "account_id", "email": "email", "phone": "phone",
                           "first_name": "first_name", "last_name": "last_name"}
    lead_type_dict = {'case': str, 'account.u_warehouse_number': 'Int64', 'account': str, 'contact': str,
                      'account.number': str, 'account.u_industry_code.u_code_value': str,
                      'account.u_bd_industry': str, 'account.phone': str,
                      'account.street': str, 'account.city': str, 'account.state': str, 'account.zip': str,
                      'u_type': str,
                      'u_membership_number': 'Int64', 'u_confidence_level': str, 'u_fiscal_year': 'Int16',
                      'u_period': 'Int16'}
    contact_type_dict = {}
    input_count = 0
    output_count = 0

    if data_load_type == 'initial':
        print("*******************Doing initial load of data")
        df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                   file_pattern=storage_config.lead_input_folder,
                                   datatype=lead_type_dict, encoding="ISO8859-1", file_type='csv')
        cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                        file_pattern=storage_config.contact_input_folder,
                                        datatype=contact_type_dict, encoding="ISO8859-1",
                                        file_type='csv')
        cleansed_lead_df = transform_lead(df, lead_rename_dict, batch_id)
        cleansed_contact_df = transform_lead(cont_df, contact_rename_dict, batch_id)
        account_df, contact_df, lead_df = split_lead_bo(cleansed_lead_df, cleansed_contact_df, batch_id)
        schema = database_config.schema_name

        with database_config.get_engine().connect() as connection:
            with connection.begin():
                print("writing account data")
                account_df.to_sql("account", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=1000,
                                  dtype={"batch_id": UUID(as_uuid=True), "industry_code": Integer,
                                         "fiscal_period": Integer, "fiscal_year": Integer, "warehouse_number": Integer})

                # account_df.to_sql("account", con=connection, schema=schema, if_exists="append", index=False,
                #                 method="multi", chunksize=10, dtype=acct_table.dtypes)
                print("writing contact data")
                contact_df.to_sql("contact", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=1000, dtype={"batch_id": UUID(as_uuid=True)})
                print("writing lead data")
                lead_df.to_sql("lead", con=connection, schema=schema, if_exists="append", index=False,
                               method="multi", chunksize=1000,
                               dtype={"batch_id": UUID(as_uuid=True), "membership_number": BigInteger})
                print("data write completed successfully")

        update_batch_id(batch_id, 'lead', "db_load", len(lead_df), len(lead_df), "Completed", database_config)
    elif data_load_type == 'delta':

        print("****************Doing delta load of data")

        cont_df = read_gcs_to_dataframe(bucket_name=storage_config.input_bucket_name,
                                        file_pattern=storage_config.lead_input_folder,
                                        datatype=contact_type_dict, encoding="ISO8859-1",
                                        file_type='json')

        cleansed_contact_df = transform_lead(cont_df, contact_rename_dict, batch_id)
        schema = database_config.schema_name
        try:
            # cleansed_df = read_data_from_source(staging_bucket, staging_blob, job_config)
            print("upsert lead data")
            process_lead_n_account(batch_id, storage_config, database_config)
            process_contact(batch_id, storage_config, database_config)
            print("upsert contact data")


        except Exception as ex:
            print("ERROR: load to database failed")
            print(ex)
            update_batch_id(batch_id, "lead", "db_load", 0, 0,
                            "Failed", database_config)


def transform_pos(df, transform_dict):
    # TODO add transformation logic for SNOW to GCP
    transform_data = df.rename(columns=transform_dict)
    input_count = 0
    output_count = 0
    batch_id = uuid.uuid4()
    transform_data['batch_id'] = batch_id
    transform_data["email"] = ""
    transform_data['fiscal_year'] = 2025
    transform_data['fiscal_period'] = 7
    date_format = "%d-%m-%Y"
    date_column_name = "order_date"
    transform_data[date_column_name] = pd.to_datetime(transform_data[date_column_name], format=date_format, utc=True)
    return transform_data


def upsert_dataframe_sqlalchemy(df, table_name, primary_key_column, database_config: DatabaseDetail,
                                temp_id_column="temp_id"):
    try:
        print("inside upsert_dataframe_sqlalchemy")
        engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=database_config.get_conn, )
        print("upsert dataframe")

        columns = ", ".join(f'{col}' for col in df.columns)
        placeholders = ", ".join(f":{col}" for col in df.columns)
        update_columns_set = ", ".join(f'{col} = EXCLUDED.{col} ' for col in df.columns if col != primary_key_column)
        # unique_key_identifiers = ", ".join(f"COALESCE({col}, '__NULL__')" for col in unique_key_columns)
        with engine.connect() as connection:
            for index, row in df.iterrows():
                # update_column = 'batch_id'
                insert_query = text(
                    f"""
                    INSERT INTO {table_name} ({columns}) VALUES ({placeholders})
                    ON CONFLICT ({primary_key_column})
                    DO UPDATE SET {update_columns_set} 
                    RETURNING {primary_key_column}
                    """
                    #
                )
                # print(insert_query)
                row_data = row.to_dict()

                result = connection.execute(insert_query, row_data)
                primary_key = result.scalar()
                if primary_key != row_data[primary_key_column]:
                    df.loc[index, primary_key_column] = primary_key
                    df.loc[index, temp_id_column] = row_data[primary_key_column]

            connection.commit()
            out_df = df[[primary_key_column, temp_id_column]]
            return out_df
    except Exception as e:
        print(f"Error: {e}")
        print(e)


def remove_null_attributes(obj):
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if v is not None}
    return obj


def remove_na_attributes(obj):
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if v is not None and not pd.isna(v) and v != 'None'}
    return obj


def upsert_pos_data_v3(df, table_name, schema_name, database_config: DatabaseDetail):
    DROP_TEMP_TABLE_QUERY = f"""DROP TABLE IF EXISTS {schema_name}.temp_{table_name};"""

    CREATE_TEMP_TABLE_QUERY = f"""
    DROP TABLE IF EXISTS {schema_name}.temp_{table_name};
    CREATE TABLE
      {schema_name}.temp_{table_name} ( 
    order_number bigint PRIMARY KEY,
    lead_id UUID NULL,
    match_score float,
    match_type varchar(20),
    batch_id uuid,
    order_date date,
    membership_number bigint,
    customer_id  bigint,
    order_amount float,
    fiscal_period int,
    fiscal_year int,
    shop_type varchar(20),
    warehouse_number bigint,
    bd_industry varchar(200),
    business_name varchar(100),
    address_line_one VARCHAR(100),
    address_line_two VARCHAR(100),
    city varchar(50),
    state varchar(50),
    zip_code varchar(10),
    phone varchar(30) NULL,
    first_name varchar(100) NULL,
    last_name varchar(100),
    email varchar(50) NULL,
    sic4_code int,
    sic6_code int,
    load_date timestamp	DEFAULT current_timestamp,
    updated_by varchar(20) NULL,
    updated_date timestamp DEFAULT current_timestamp
   ) ;  """
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=database_config.get_conn)
    with engine.begin() as connection:
        print("Create a temp table")
        result = connection.execute(text(CREATE_TEMP_TABLE_QUERY))

        print("loading data into temp table ")
        df.to_sql(f"temp_{table_name}", con=connection, schema=schema_name, if_exists="append", index=False,
                  method="multi", chunksize=500, dtype={"batch_id": UUID(as_uuid=True)})
        print("data inserted into database")

        primary_key_column = "order_number"
        columns = ", ".join(f'{col}' for col in df.columns)
        placeholders = ", ".join(f":{col}" for col in df.columns)
        update_columns_set = ", ".join(f'{col} = EXCLUDED.{col} ' for col in df.columns if col != primary_key_column)

        insert_query = text(f"""INSERT INTO {schema_name}.transaction ({columns})
        SELECT {columns} FROM {schema_name}.temp_{table_name}
        ON CONFLICT ({primary_key_column}) 
        DO UPDATE SET {update_columns_set};
            """)
        print(f"inserting data into {table_name} table")
        result = connection.execute(insert_query)

        # print(f"data inserted into {table_name} table")
        print("Dropping temp table")
        result = connection.execute(text(DROP_TEMP_TABLE_QUERY))
        connection.commit()


def upsert_pos_data_v2(df, database_config: DatabaseDetail, model: TransactionBO = TransactionBO,
                       batch_size: int = 100):
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=database_config.get_conn, )
    Session = sessionmaker(bind=engine)
    total_rows = len(df)
    for start in range(0, total_rows, batch_size):
        end = min(start + batch_size, total_rows)
        batch_df = df.iloc[start:end]
        # Perform database operations
        try:
            with Session() as session:
                # bulk_upsert(batch_df, session, config)
                records = batch_df.to_dict(orient='records')
                stmt = insert(model).values(records)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[model.order_number],  # Specify the column that causes conflict
                    set_={col.name: col for col in stmt.excluded if col.name != 'order_number'}
                    # Exclude 'id' from being updated
                )
                session.execute(stmt)
                session.commit()
        except Exception as ex:
            print("ERROR happened in bulk pos upsert")
            print(ex)
            raise  # re raise the exception.


def upsert_pos_data_v1(df, database_config: DatabaseDetail, model: TransactionBO = TransactionBO,
                       batch_size: int = 100):
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=database_config.get_conn, )
    Session = sessionmaker(bind=engine)
    total_rows = len(df)
    for start in range(0, total_rows, batch_size):
        end = min(start + batch_size, total_rows)
        batch_df = df.iloc[start:end]
        # Perform database operations
        try:
            with Session() as session:
                # bulk_upsert(batch_df, session, config)
                records = batch_df.to_dict(orient='records')
                stmt = insert(model).values(records)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[model.order_number],  # Specify the column that causes conflict
                    set_={col.name: col for col in stmt.excluded if col.name != 'order_number'}
                    # Exclude 'id' from being updated
                )
                session.execute(stmt)
                session.commit()
        except Exception as ex:
            print("ERROR happened in bulk pos upsert")
            print(ex)
            raise  # re raise the exception.


def upsert_pos_data(df, database_config: DatabaseDetail):
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=database_config.get_conn, )
    Session = sessionmaker(bind=engine)
    with Session() as session:
        # Perform database operations
        try:
            # Upsert logic
            for index, row in df.iterrows():
                data_dict = remove_na_attributes(row.to_dict())
                existing_record = session.query(TransactionBO).filter_by(order_number=row['order_number']).first()

                if existing_record:
                    print("record already exists")
                    # Update existing record
                    # data_dict = remove_na_attributes(row.to_dict())
                    for key, value in data_dict.items():
                        setattr(existing_record, key, value)
                else:
                    print("create new record")
                    # Create new record
                    # data_dict = remove_na_attributes(row.to_dict())
                    new_record = TransactionBO(**data_dict)
                    session.add(new_record)

            session.commit()  # Commit the transaction.
        except:
            session.rollback()  # rollback the transaction in case of errors.
            raise  # re raise the exception.


def write_pos_data_to_db(batch_id, storage_config: StorageConfig, database_config: DatabaseDetail,
                    transform_config:TransformConfig,
                         data_load_type="delta",
                         chunk_size=1000):
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, "pos", "db_load", "Started", database_config)

    pos_record_count_before_load = get_table_row_count(database_config,"transaction")
    data_type_dict =transform_config.initial_load_pos_datatype_mapping
    pos_df = read_gcs_to_dataframe(storage_config.input_bucket_name,
                                   f"{storage_config.pos_input_folder}/", datatype=data_type_dict,
                                   encoding="ISO8859-1")

    rename_dict = transform_config.initial_load_pos_mapping
    transformed_df = transform_pos(pos_df, rename_dict)
    ls_pos_columns = transform_config.pos_columns
    transformed_df = transformed_df[ls_pos_columns]
    try:
        if data_load_type == "initial":

            with database_config.get_engine().begin() as conn:
                transformed_df.to_sql("transaction", con=conn, schema=database_config.schema_name, if_exists="append",
                                      index=False,
                                      method="multi", chunksize=chunk_size,
                                      dtype={"batch_id": UUID(as_uuid=True)})
                print("Transaction data inserted into database")

            update_batch_id(batch_id, "pos", "db_load", len(transformed_df), len(transformed_df),
                            "Completed", database_config)
        elif data_load_type == "delta":
            # TODO - validate the code change
            # upsert_pos_data(transformed_df, database_config)
            success_count, failure_count = upsert_using_primary_key(transformed_df,
                                                                    f"{database_config.schema_name}.transaction",
                                                                    "pos_id",
                                                                    database_config)
            update_batch_id(batch_id, "pos", "db_load", success_count, failure_count,
                            "Completed", database_config)
        pos_record_count_after_load = get_table_row_count(database_config, "transaction")
        if pos_record_count_after_load > pos_record_count_before_load:
            print("New sales records added to transaction table . Match will be triggered")
            trigger_cloud_run_job(storage_config.project_id,location="us-central1",job_name="lead-match-job")

    except Exception as ex:
        print("Error occurred while loading POS data into Database")
        print(ex)
        update_batch_id(batch_id, "pos", "db_load", 0, 0,
                        "Failed", database_config)


def read_lead_data(batch_id, snow_config: SnowConfig, gcs_config: StorageConfig, database_config: DatabaseDetail,

                   batch_size=10000):
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, "lead", "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, "lead")
    start_index = 1
    # batch_size = snow_config.max_batch_size
    end_index = batch_size
    data_found = True
    total_rec_count = 0
    # TODO Remove hard coded date values after testing
    #start_date = "2025-04-10 00:00:00"
    #end_date = "2025-04-11 12:00:00"
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
        update_batch_id(batch_id, "lead", "staging", total_rec_count, total_rec_count,
                        "Completed", database_config)
    except Exception as exc:
        print("Error while getting LEAD data from  service Now")
        update_batch_id(batch_id, "lead", "staging", total_rec_count, total_rec_count,
                        "Failed", database_config)
        raise exc
    return total_rec_count


def read_pos_data(batch_id, snow_config: SnowConfig, gcs_config: StorageConfig, database_config: DatabaseDetail,
                  batch_size=5000):
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, "pos", "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, "pos")
    start_index = 1
    # batch_size = snow_config.max_batch_size
    end_index = batch_size
    data_found = True
    total_rec_count = 0
    
    try:
        # Archive existing old files from gcs folder
        #archive_gcs_folder(gcs_config)
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

        update_batch_id(batch_id, "pos", "staging", total_rec_count, total_rec_count,
                        "Completed", database_config)

    except Exception as exc:
        print("Error while getting POS data from  service Now")
        update_batch_id(batch_id, "pos", "staging", total_rec_count, total_rec_count,
                        "Failed", database_config)
        raise exc
    return total_rec_count

def get_record_snow_to_gcs(batch_id,data_type:str, snow_config: SnowConfig, gcs_config: StorageConfig, database_config: DatabaseDetail,
                  batch_size=5000):
    if not batch_id:
        batch_id = uuid.uuid4()
    add_batch_id(batch_id, data_type, "staging", "Started", database_config)
    start_date, end_date = get_date_range(database_config, data_type)
    start_index = 1
    # batch_size = snow_config.max_batch_size
    end_index = batch_size
    data_found = True
    total_rec_count = 0
    #TODO remove hardcoded values
    #start_date = "2025-04-10 00:00:00"
    #end_date = "2025-04-11 12:00:00"
    try:
        # Archive existing old files from gcs folder
        #archive_gcs_folder(gcs_config)
        input_folder = gcs_config.input_folders[data_type]

        archive_gcs_files(gcs_config.input_bucket_name, input_folder,
                          gcs_config.archive_bucket_name, gcs_config.archive_folder)
        while data_found:
            payload = {
                "start_index": start_index,
                "end_index": end_index,
                "start_date": start_date,
                "end_date": end_date}
            output_data = read_data_from_snow(snow_config.api_urls[data_type], snow_config.snow_user, snow_config.snow_password,
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

        update_batch_id(batch_id, data_type, "staging", total_rec_count, total_rec_count,
                        "Completed", database_config)

    except Exception as exc:
        print("Error while getting POS data from  service Now")
        update_batch_id(batch_id, data_type, "staging", total_rec_count, total_rec_count,
                        "Failed", database_config)
        raise exc
    return total_rec_count

def get_last_run_batch_date():
    pass
    return ""


def get_date_range(db_config, data_type='lead'):
    utc_now = datetime.now(timezone.utc)
    # Print the UTC datetime
    print("Current UTC datetime:", utc_now)
    # You can also format it as a string if needed

    batch_id, load_date = get_latest_batch_by_status(db_config, data_type, "staging", "completed")

    end_date = utc_now.strftime("%Y-%m-%d %X")
    if load_date:
        start_date = load_date.strftime("%Y-%m-%d %X")
    else:
        start_date = "2025-03-28 00:00:00" #project go live date

    return start_date, end_date


def get_data_from_snow(batch_id, config: JobConfig, ):
    print("inside get_data_from_snow ")

    if not batch_id:
        batch_id = uuid.uuid4()

    read_lead_data(batch_id, config.snow_config, config.storage_config, config.db_config,
                   config.snow_config.max_batch_size)
    #read contact_info
    read_pos_data(batch_id, config.snow_config, config.storage_config, config.db_config,
                  config.snow_config.max_batch_size)
    return batch_id


def load_data_to_db(batch_id, config: JobConfig):
    # TODO add logic
    print("inside load_data_to_db ")
    if not batch_id:
        batch_id = uuid.uuid4()

    write_lead_data_to_db(batch_id, config.storage_config, config.db_config, config.data_load_type)
    write_pos_data_to_db(batch_id, config.storage_config, config.db_config, config.data_load_type, chunk_size=1000)


def __init__():
    pass


def publish_message(project_id: str, topic_name: str, message: str) -> None:
    """Publishes a message to a Pub/Sub topic."""
    try:
        print("inside publish message method")
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project_id, topic_name)

        # Data must be a bytestring
        data = message.encode("utf-8")

        # When you publish a message, the client returns a future.
        future = publisher.publish(topic_path, data=data)
        print(f"Published message to {topic_path}: {future.result()}")

    except Exception as e:
        print(f"Error publishing message: {e}")

def trigger_cloud_run_job(project_id,location,job_name):
    print("inside gcs trigger cloud run job")
    # Create a client
    client = run_v2.JobsClient()
    #job_name = "lead-matching-job"
    # Initialize request argument(s)
    request = run_v2.RunJobRequest(
        name=f"projects/{project_id}/location/{location}/jobs/{job_name}",
    )

    # Make the request
    operation = client.run_job(request=request)

    #print("Waiting for operation to complete...")
    #response = operation.result()
    time.sleep(20)
    response = operation.operation.name

    # Handle the response
    print(response)
    return response

def trigger_match_job():
    project_id = "p-601-lab-bc-leads-mgmt"
    location = "us-central1"
    job_name = "lead-matching-job"
    # publish_message(project_id, topic_name, message_to_publish)
    out = trigger_cloud_run_job(project_id, location, job_name)
    print(out)
    print("Matching pipeline triggered")

def send_message_to_pubsub():
    project_id = "p-601-lab-bc-leads-mgmt"
    topic_name = "eventarc-us-central1-trigger-pubsub2-870"
    message_to_publish = "run_job"
    location = "us-central1"
    publish_message(project_id, topic_name, message_to_publish)

if __name__ == "__main__":

    job_config = JobConfig("sync_config.ini")

    print("inside main")
    if len(sys.argv) == 1:
        print("error : required argument to run specific job ")

    stage = sys.argv[1]
    if stage.lower() == "snow_to_gcs":
        try:
            batch_id = uuid.uuid4()
            get_data_from_snow(batch_id, job_config)
        except Exception as ex:
            print("Error happened during snow_to_gcs process")
            print(ex)
            raise ex
    elif stage.lower() == "gcs_to_db":
        try:
            batch_id = uuid.uuid4()
            load_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during gcs_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_db":
        try:
            batch_id = uuid.uuid4()
            get_data_from_snow(batch_id, job_config)
            load_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during snow_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "lead_to_db":
        try:
            batch_id = uuid.uuid4()
            write_lead_data_to_db(batch_id, job_config.storage_config, job_config.db_config,job_config.transform_config,
                                  job_config.data_load_type)
        except Exception as ex:
            print("Error happened during write_lead_data_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "pos_to_db":
        try:
            batch_id = uuid.uuid4()
            write_pos_data_to_db(batch_id, job_config.storage_config, job_config.db_config,job_config.transform_config,
                                 job_config.data_load_type, chunk_size=1000)
        except Exception as ex:
            print("Error happened during write_pos_data_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_gcs_lead":
        try:

            batch_id = uuid.uuid4()
            read_lead_data(batch_id, job_config.snow_config, job_config.storage_config, job_config.db_config,
                           job_config.snow_config.max_batch_size)
        except Exception as ex:
            print("Error happened during read_lead_data process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_gcs_pos":
        try:
            start_date = ""
            end_date = ""
            batch_id = uuid.uuid4()
            read_pos_data(batch_id, job_config.snow_config, job_config.storage_config, job_config.db_config,
                          job_config.snow_config.max_batch_size)
        except Exception as ex:
            print("Error happened during read_pos_data process")
            print(ex)
            raise ex
    else:
        print("Invalid Argument - Valid arguments are [snow_to_gcs,gcs_to_db ,snow_to_db]")
        raise Exception("Invalid Arguments - Valid arguments are snow_to_gcs,gcs_to_db ,snow_to_db")
