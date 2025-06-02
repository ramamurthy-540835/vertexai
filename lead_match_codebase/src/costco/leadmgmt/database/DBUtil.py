import sqlalchemy
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, relationship, Mapped, sessionmaker
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, text,BigInteger,Uuid, Date,Double
#from src.costco.leadmgmt.sync_snow_gcp import DatabaseDetail
from google.cloud.sql.connector import Connector, IPTypes
import os
from dataclasses import dataclass
import pandas as pd
from costco.leadmgmt.config.Configuration import DatabaseDetail
from costco.leadmgmt.util import apputil



db_config = DatabaseDetail.from_env()
schema_name = db_config.schema_name
engine = create_engine("postgresql+pg8000://", creator=db_config.get_conn)
Base = declarative_base()
#Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class TransactionBO(Base):
    __tablename__ = "transaction"
    __table_args__ = {"schema": schema_name}

    order_number = Column(BigInteger, primary_key=True)
    lead_id = Column(Uuid, ForeignKey(f"{schema_name}.lead.lead_id"))
    batch_id = Column(Uuid)
    order_date = Column(Date)
    membership_number = Column(BigInteger)
    order_amount = Column(Double)
    first_name = Column(String)
    last_name = Column(String)
    email = Column(String)
    warehouse_number = Column(Integer)
    street = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    phone = Column(String)
    sic4_code = Column(Integer, nullable=True)
    sic6_code = Column(Integer, nullable=True)
    business_name = Column(String)
    transaction_type = Column(String)
    bd_industry = Column(String)
    fiscal_year = Column(Integer)
    fiscal_period = Column(Integer)
    single_parent = True


def get_latest_batch_id(data_type, stage,db_config:DatabaseDetail):
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

def get_latest_success_batch(data_type, stage,db_config:DatabaseDetail):
    batch_id = None
    status = None
    try:

        schema = db_config.schema_name
        params = {"data_type": data_type, "stage": stage}
        select_query = f"select batch_id,status from {schema}.batch_audit where status = 'Completed' data_type = :data_type and stage =:stage order by load_date desc limit 1 ;"
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


def add_batch_id(batch_id: str, data_type: str, total_count: int, success_count: int, stage: str, status: str,db_details):
    try:

        params = {"batch_id": batch_id, "data_type": data_type, "total_count": total_count,
                  "success_count": success_count, "stage": stage, "status": status}
        insert_query = f"insert into {db_details.schema_name}.batch_audit (batch_id,data_type,total_volume,success_count,stage,status) values( :batch_id,:data_type" \
                       f",:total_count,:success_count,:stage,:status);"
        with db_details.get_engine().begin() as conn:
            conn.execute(text(insert_query), params)
            print(f"batch id added successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")
        with engine.connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e

def load_data_from_cloudsql(engine,query_input):
    # Create a connection using Google Cloud SQL Connector
    connector = Connector()

    # Query data
    df = pd.read_sql(query_input, engine)

    # Close the connection
    connector.close()

    return df