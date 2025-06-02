from sqlalchemy import create_engine,text
from datetime import datetime
from costco.leadmgmt.util.cloud_sql_conn import engine_creation

def execute_failed_query(connection_string,secret_user_name,secret_password,database_name,project_id,match_id,failed_status_query,cloud_sql_ip_type):
    engine = engine_creation(connection_string,secret_user_name,secret_password,database_name,project_id,cloud_sql_ip_type)

    end_date = datetime.now()
    start_date = datetime.now()

    with engine.connect() as connection:
                    with connection.begin():  # Automatically commits the transaction
                        # Update failed status
                        connection.execute(
                            text(failed_status_query),
                            [{'status': 'Failed','end_date': end_date,'start_date':start_date,'match_id': match_id} ]
                        )