from google.cloud.sql.connector import Connector, IPTypes
from costco.leadmgmt.util.get_secret_from_secret_manager import access_secret
import sqlalchemy

def get_ip_type(cloud_sql_ip_type):
    if cloud_sql_ip_type == "PRIVATE":
        ip_type = IPTypes.PRIVATE
    elif cloud_sql_ip_type == "PUBLIC":
        ip_type = IPTypes.PUBLIC
    elif cloud_sql_ip_type == "PSC":
        ip_type = IPTypes.PSC
    else:
        ip_type = cloud_sql_ip_type

    return ip_type
    
def engine_creation(connection_string,secret_user_name,secret_password,database_name,project_id,cloud_sql_ip_type):

    # Create a connection using Google Cloud SQL Connector
    connector = Connector()

    def getconn():
        conn = connector.connect(
            connection_string,
            "pg8000",
            user = access_secret(project_id,secret_user_name),
            password = access_secret(project_id,secret_password),
            db = database_name,
            ip_type = get_ip_type(cloud_sql_ip_type)
        )
        return conn

    # Create SQLAlchemy engine
    engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn, isolation_level="AUTOCOMMIT")
        
    return engine
