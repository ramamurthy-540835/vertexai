
import os

import sqlalchemy
from google.cloud.sql.connector import Connector

from lead_match_runtime.business_rules import load_business_rules, get_cloudsql_connection_name, get_schema

_RULES = load_business_rules()
_CONNECTION_NAME = get_cloudsql_connection_name(_RULES)
_SCHEMA = get_schema(_RULES)

def getconn():
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise RuntimeError("DB_PASSWORD is required")

    with Connector() as connector:
        conn = connector.connect(
            os.environ.get("CLOUDSQL_CONNECTION_NAME", _CONNECTION_NAME),
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
        tables = inspector.get_table_names(schema=_SCHEMA)
        print(f"Row counts for tables in schema '{_SCHEMA}':")
        for table in tables:
            result = db_conn.execute(sqlalchemy.text(f'SELECT count(*) FROM {_SCHEMA}."{table}"'))
            count = result.scalar()
            print(f"- {table}: {count}")

if __name__ == "__main__":
    main()
