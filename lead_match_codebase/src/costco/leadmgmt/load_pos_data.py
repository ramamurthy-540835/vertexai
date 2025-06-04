from uuid import uuid4
import uuid
from sqlalchemy.dialects.postgresql import UUID, INTEGER
import pandas as pd
from google.cloud.sql.connector import Connector, IPTypes
import sqlalchemy
from sqlalchemy.orm import sessionmaker
from datetime import date, datetime
from pydantic import BaseModel, EmailStr
# from uuid import UUID, uuid4
from typing import List
# from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import declarative_base, relationship, Mapped
from sqlalchemy import create_engine, Column, Integer, String, Uuid, DateTime, ForeignKey, Date, Double, BigInteger
from sqlalchemy.sql import func
from datetime import datetime
import os
from google.cloud import secretmanager
from datetime import datetime, timedelta

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


def write_to_sql(df, table_name, schema_name):
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn, )
    with engine.begin() as conn:
        df.to_sql(table_name, con=conn, schema=schema_name, if_exists="append", index=False,
                  method="multi", chunksize=5, dtype={"batch_id": UUID(as_uuid=True)})
        print("data inserted into database")


if __name__ == "__main__":
    pos_bucket_name = os.environ.get("POS_INPUT_BUCKET")
    pos_input_blob_name = os.environ.get("POS_INPUT_BLOB")
    schema_name = os.environ.get("DB_SCHEMA")
    path = f"gs://{pos_bucket_name}/{pos_input_blob_name}"
    df = pd.read_csv(path)
    rename_dict = {'Whse': 'warehouse_number', 'Transaction_id': 'order_number', 'Card#': 'membership_number',
                   'Business Name': 'business_name', 'SIC 4 digit': 'sic4_code',
                   'SIC 6 digit': 'sic6_code', 'BD Industry': 'bd_industry', 'Transaction Amount': 'order_amount',
                   'Transaction Date': 'order_date',
                   'Shop Type': 'shop_type', 'Phone Number': 'phone', 'Email Address': 'email',
                   'Address': 'address_line_one', 'City': 'city', 'State': 'state',
                   'Zip Code': 'zip_code', 'First Name': 'first_name', 'Last Name': 'last_name'}
    date_format = "%Y-%m-%d"
    date_format = "%m/%d/%Y"
    date_format = "%d-%m-%Y"
    date_format = 'ISO8601'
    date_format = 'mixed'
    column_name = "order_date"

    df = df.rename(columns=rename_dict)

    df[column_name] = pd.to_datetime(df[column_name], format=date_format, utc=True)
    # df.drop(columns =['MSPIPK'],inplace=True)
    # df[['fiscal_year', 'fiscal_period']] = df.apply(lambda row: get_costco_fiscal_info(datetime.strptime(str(row['transaction_date']), '%Y-%m-%d')), axis=1).apply(pd.Series)
    df[['fiscal_year', 'fiscal_period']] = df.apply(
        lambda row: get_costco_fiscal_info(row['order_date'].strftime('%Y-%m-%d')), axis=1).apply(pd.Series)

    batch_id = uuid.uuid4()
    df["batch_id"] = batch_id  # You can also use a UUID
    df = df.drop_duplicates()
    print(len(df))
    write_to_sql(df, "transaction", schema_name)