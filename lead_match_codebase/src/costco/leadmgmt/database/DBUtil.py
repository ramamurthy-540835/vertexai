import pandas as pd
from google.cloud.sql.connector import Connector
from sqlalchemy import text
from costco.leadmgmt.config.Configuration import DatabaseDetail
from costco.leadmgmt.util.logger import app_logger


def load_data_from_cloudsql(engine, query_input):
    # Create a connection using Google Cloud SQL Connector
    connector = Connector()

    # Query data
    df = pd.read_sql(query_input, engine)

    # Close the connection
    connector.close()

    return df


def get_table_row_count(db_config: DatabaseDetail, table_name):
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
        app_logger.error("Error happened reading  batch audit data")
        app_logger.error(ex)
        raise ex

    return row_count
