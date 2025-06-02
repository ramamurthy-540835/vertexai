import sqlalchemy
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
import numpy
import pandas as pd
from google.cloud.sql.connector import Connector
import os
from datetime import datetime, timedelta
from google.cloud import secretmanager
from google.cloud.sql.connector import Connector, IPTypes


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
        # print(fiscal_start)

    # Determine weeks since fiscal start
    days_since_start = (input_date - fiscal_start).days

    # print(days_since_start)
    weeks_since_start = days_since_start // 7
    # print(weeks_since_start)
    # Fiscal periods are 4 weeks long (except the last one)
    fiscal_period = min(12, (weeks_since_start // 4) + 1)

    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period
    }


# function to return the database connection object
def getconn():
    # initialize Connector object
    connector = Connector()
    db_password_id = os.environ.get("POSTGRES_DB_PASSWORD_ID")
    project_id = os.environ.get("GCP_PROJECT_ID")

    INSTANCE_CONNECTION_NAME = os.environ.get("DB_CONNECTION_NAME")
    DB_USER = os.environ.get("POSTGRES_DB_USER")
    DB_PASS = access_secret_version(project_id, db_password_id, version_id="latest")
    if DB_PASS is None:
        raise Exception("Unable to get password")
    DB_NAME = os.environ.get("POSTGRES_DB_NAME")

    if os.environ.get("CLOUD_SQL_IP_TYPE") == "PRIVATE":
        ip_type = IPTypes.PRIVATE
    elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PUBLIC":
        ip_type = IPTypes.PUBLIC
    elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PSC":
        ip_type = IPTypes.PSC
    else:
        ip_type = os.environ.get("CLOUD_SQL_IP_TYPE")

    print("inside get connection")
    conn = connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        password=DB_PASS,
        db=DB_NAME,
        ip_type=ip_type
    )
    print("returning from get connection")
    return conn


def dedup_account(cleansed_df):
    ls_account_columns = ["account_id", 'contact_id', 'lead_id', "bd_industry", "address_line_one", "address_line_two",
                          "city", "business_name", "phone", "state",
                          "zip_code", "industry_code", "type"]
    dedup_columns = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'business_name']
    account_df = cleansed_df[ls_account_columns]
    df_clone = account_df.copy()

    df_clone = df_clone.fillna('-1')
    # Step 1: Identify groups based on the deduplication columns
    df_clone['group_id'] = df_clone.groupby(dedup_columns).ngroup()

    # Step 2: Find the "master" record for each group (smallest id1 within each group)
    group_master_ids = (
        df_clone.groupby('group_id')['account_id']
        .transform('min')
    )

    # Step 3: Assign the master id1 to all records in the same duplicate group
    df_clone['account_id'] = group_master_ids

    df_clone = df_clone.drop(
        columns=["group_id", "bd_industry", "address_line_one", "address_line_two", "city", "business_name", "phone",
                 "state",
                 "zip_code", "industry_code", "type"])

    merged = cleansed_df.merge(df_clone, on=['contact_id', 'lead_id'], how='left', suffixes=('', '_new'))

    # Update status where there is a match
    merged['account_id'] = merged['account_id_new'].combine_first(merged['account_id'])

    deduped_df = merged.drop(columns=['account_id_new'])
    # print(deduped_df)
    return deduped_df


def split_bo_from_df(cleansed_df, batch_id):
    acct_dedup_columns = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'business_name']
    ls_account_columns = ["account_id", "batch_id", "bd_industry", "address_line_one", "address_line_two", "city",
                          "business_name", "phone", "state",
                          "zip_code", "industry_code", "type"]
    ls_lead_columns = ["lead_id", "firefly_id", "account_id", "contact_id", "lead_status", "batch_id", "lead_source",
                       "membership_number", "warehouse_number", "fiscal_year", "fiscal_period"]
    ls_contact_columns = ["account_id", "contact_id", "first_name", "last_name", "email", "batch_id"]
    """
    cleansed_df["lead_id"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID
    cleansed_df["account_id"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID
    cleansed_df["contact_id"] = [uuid.uuid4() for _ in range(len(cleansed_df))]  # You can also use a UUID

    """
    cleansed_df["contact_id"] = cleansed_df.apply(lambda row: uuid.uuid4(), axis=1)
    cleansed_df["account_id"] = cleansed_df.apply(lambda row: uuid.uuid4(), axis=1)
    cleansed_df["lead_id"] = cleansed_df.apply(lambda row: uuid.uuid4(), axis=1)
    cleansed_df[['fiscal_year', 'fiscal_period']] = cleansed_df.apply(lambda row: get_costco_fiscal_info(),
                                                                      axis=1).apply(pd.Series)

    cleansed_df["batch_id"] = batch_id
    cleansed_df["lead_status"] = "New"
    cleansed_df["lead_source"] = "Firefly"
    cleansed_df = dedup_account(cleansed_df)
    lead_df = cleansed_df[ls_lead_columns]
    account_df = cleansed_df[ls_account_columns].drop_duplicates(subset=acct_dedup_columns, keep='first')
    contact_df = cleansed_df[ls_contact_columns]

    return account_df, contact_df, lead_df, batch_id


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


def upsert_dataframe_sqlalchemy(df, table_name, unique_key_columns, primary_key_column, update_column,
                                temp_id_column="temp_id"):
    try:
        print("inside upsert_dataframe_sqlalchemy")
        engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=getconn, )
        print("upsert dataframe")
        columns = ", ".join(f'{col}' for col in df.columns)
        placeholders = ", ".join(f":{col}" for col in df.columns)
        unique_key_identifiers = ", ".join(f"COALESCE({col}, '__NULL__')" for col in unique_key_columns)
        df[temp_id_column] = ""
        with engine.connect() as connection:
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
        # SQLAlchemy handles rollback automatically when using a context manager (with engine.connect())


def write_to_sql(account_df, contact_df, lead_df, batch_id, schema="lead_mgmt"):
    # schema = "lead_mgmt"
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn, )
    ls_contact_columns = ["account_id", "contact_id", "first_name", "last_name", "email", "batch_id"]
    ls_lead_columns = ["lead_id", "account_id", "contact_id", "lead_status", "batch_id", "lead_source",
                       "membership_number", "warehouse_number", "fiscal_year", "fiscal_period"]
    # Insert DataFrames into PostgreSQL
    try:
        # df_account = upsert_account_data(account_df, batch_id)
        account_unique_key_columns = ["business_name", "address_line_one", "address_line_two", "city", "state",
                                      "zip_code"]
        account_primary_key_column = "account_id"
        account_update_column = "batch_id"
        table_name = f"{schema}.account"
        df_account = upsert_dataframe_sqlalchemy(account_df, table_name, account_unique_key_columns,
                                                 account_primary_key_column,
                                                 account_update_column, "temp_account_id")
        print("before")
        print(contact_df.head())
        print(len(contact_df))
        print(df_account.head())
        contact_df = update_account_id_in_contact(contact_df, df_account)
        print("After")
        print(contact_df.head())
        print(len(contact_df))
        lead_df = update_account_id_in_lead(lead_df, df_account)
        with engine.connect() as connection:
            with connection.begin():
                # account_df.to_sql("account", con=connection, schema='costco_poc', if_exists="append", index=False,
                #                      method="multi", chunksize=1000, dtype={ "account_id_gcp": UUID(as_uuid=True)})
                contact_df.to_sql("contact", con=connection, schema=schema, if_exists="append", index=False,
                                  method="multi", chunksize=5,
                                  dtype={"account_id": UUID(as_uuid=True), "contact_id": UUID(as_uuid=True),
                                         "batch_id": UUID(as_uuid=True)})
                lead_df.to_sql("lead", con=connection, schema=schema, if_exists="append", index=False,
                               method="multi", chunksize=5,
                               dtype={"lead_id": UUID(as_uuid=True), "contact_id": UUID(as_uuid=True),
                                      "batch_id": UUID(as_uuid=True), "account_id": UUID(as_uuid=True)})
    except SQLAlchemyError as e:
        print(f"Database error: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state

    print("Data successfully inserted!")


# batch_id,status = get_latest_batch_id('lead',"pre-processing")


if __name__ == "__main__":
    lead_input_bucket_name = os.environ.get("LEAD_INPUT_BUCKET")
    lead_input_blob_name = os.environ.get("LEAD_INPUT_BLOB")
    schema_name = os.environ.get("DB_SCHEMA")
    lead_path = f"gs://{lead_input_bucket_name}/{lead_input_blob_name}"
    df = pd.read_csv(lead_path)
    print(len(df))
    rename_dict = {'Whs': 'warehouse_number', 'Account #': 'account_number', 'Member #': 'membership_number',
                   'Business Name': 'business_name', 'First Name': 'first_name',
                   'Last Name': 'last_name', 'Email': 'email', 'Phone Number': 'phone', 'Address 1': 'address_line_one',
                   'City': 'city', 'State': 'state',
                   'Zip Code': 'zip_code', 'SIC Code 6': 'industry_code', 'BD Industry': 'bd_industry',
                   "Firefly ID": "firefly_id"}

    df = df.rename(columns=rename_dict)
    df['address_line_two'] = None
    df = df.where(pd.notna(df), None)
    batch_id = uuid.uuid4()
    account_df, contact_df, lead_df, batch_id = split_bo_from_df(df, batch_id)
    update_column = "batch_id"
    write_to_sql(account_df, contact_df, lead_df, batch_id, schema_name)
