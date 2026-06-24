
import os

import sqlalchemy
from google.cloud.sql.connector import Connector

def getconn():
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise RuntimeError("DB_PASSWORD is required")

    with Connector() as connector:
        conn = connector.connect(
            os.environ.get("CLOUDSQL_CONNECTION_NAME", "ctoteam:us-central1:lead-mgmt-db"),
            "pg8000",
            user=os.environ.get("DB_USER", "postgres"),
            password=password,
            db=os.environ.get("DB_NAME", "postgres"),
        )
        return conn

def main():
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
    )
    with engine.connect() as db_conn:
        inspector = sqlalchemy.inspect(db_conn)
        tables = inspector.get_table_names(schema="leadmgmt")
        print("Tables in schema 'leadmgmt':")
        for table in tables:
            print(table)

if __name__ == "__main__":
    main()
