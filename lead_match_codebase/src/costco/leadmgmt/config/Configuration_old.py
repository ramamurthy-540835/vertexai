import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes
import os
from dataclasses import dataclass
from costco.leadmgmt.util import apputil
#import apputil


@dataclass
class DatabaseDetail:
    db_user: str
    db_password: str
    schema_name: str
    instance_connection_name: str
    db_name: str
    project_id: str
    ip_type: IPTypes

    def __repr__(self):
        return {"db_user" : self.db_user, "db_password":"*********" ,"schema_name" : self.schema_name
                ,"instance_connection_name" : self.instance_connection_name,"db_name" : self.db_name
                ,"project_id" : self.project_id ,"ip_type":self.ip_type}

    def __str__(self):
        return {"db_user" : self.db_user, "db_password":"*********" ,"schema_name" : self.schema_name
                ,"instance_connection_name" : self.instance_connection_name,"db_name" : self.db_name
                ,"project_id" : self.project_id ,"ip_type":self.ip_type}
    @classmethod
    def from_env(cls):
        # Initialize the dataclass from environment variables
        if os.environ.get("CLOUD_SQL_IP_TYPE") == "PRIVATE":
            ip_type = IPTypes.PRIVATE
        elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PUBLIC":
            ip_type = IPTypes.PUBLIC
        elif os.environ.get("CLOUD_SQL_IP_TYPE") == "PSC":
            ip_type = IPTypes.PSC
        else:
            ip_type = os.environ.get("CLOUD_SQL_IP_TYPE")
        db_user_id: str = os.environ.get("POSTGRES_DB_USER")
        db_password_id: str = os.environ.get("POSTGRES_DB_PASSWORD_ID")
        project_id = os.environ.get("GCP_PROJECT_ID")
        db_user: str = apputil.access_secret_version(project_id, db_user_id, version_id="latest")
        db_password: str = apputil.access_secret_version(project_id, db_password_id, version_id="latest")

        return cls(
            schema_name=os.environ.get("DB_SCHEMA"),
            instance_connection_name=os.environ.get("DB_CONNECTION_NAME"),
            db_user=db_user,
            db_password=db_password,
            db_name=os.environ.get("POSTGRES_DB_NAME"),
            project_id=project_id,
            ip_type=ip_type
        )

    def get_conn(self):
        # initialize Connector object
        connector = Connector()
        print("inside get connection")

        conn = connector.connect(
            self.instance_connection_name,
            "pg8000",
            user=self.db_user,
            password=self.db_password,
            db=self.db_name,
            ip_type=self.ip_type
        )
        print("returning from get connection")
        return conn

    def get_engine(self):
        engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=self.get_conn, )
        return engine


@dataclass
class SnowConfig:
    lead_url: str
    pos_url: str
    match_result_url: str
    max_batch_size: int
    project_id: str
    snow_password: str
    snow_user: str
    api_urls :dict

    def __repr__(self):
        return {"lead_url": self.lead_url, "pos_url": self.pos_url
            , "match_result_url": self.match_result_url, "max_batch_size": self.max_batch_size
            , "project_id": self.project_id, "snow_user": self.snow_user, "snow_password": "*********"}

    def __str__(self):
        return {"lead_url": self.lead_url, "pos_url": self.pos_url
            , "match_result_url": self.match_result_url, "max_batch_size": self.max_batch_size
            , "project_id": self.project_id, "snow_user": self.snow_user, "snow_password": "*********"}


    @classmethod
    def from_env(cls):
        snow_user_id = os.environ.get("SNOW_USER")
        snow_password_id = os.environ.get("SNOW_PASSWORD")
        project_id: str = os.environ.get("GCP_PROJECT_ID")
        snow_password: str = apputil.access_secret_version(project_id, snow_password_id, version_id="latest")
        snow_user: str = apputil.access_secret_version(project_id, snow_user_id, version_id="latest")
        lead_url = os.environ.get("LEAD_URL")
        contact_url = os.environ.get("CONTACT_URL")
        match_url = os.environ.get("MATCH_URL")
        pos_url = os.environ.get("POS_URL")

        urls = {'pos':pos_url, "lead":lead_url, "contact":contact_url,"match":match_url}
        return cls(
            lead_url=os.environ.get("LEAD_URL"),
            pos_url=os.environ.get("POS_URL"),
            match_result_url=os.environ.get("MATCH_RESULT_URL"),
            max_batch_size=int(os.environ.get("BATCH_SIZE")),
            project_id=project_id,
            snow_password=snow_password,
            snow_user=snow_user,
            api_urls = urls
        )


@dataclass
class StorageConfig:
    input_bucket_name: str
    archive_bucket_name: str
    lead_input_folder: str
    contact_input_folder: str
    pos_input_folder: str
    match_result_input_folder: str
    archive_folder: str
    input_folders:dict

    project_id: str = os.environ.get("GCP_PROJECT_ID")

    @classmethod
    def from_env(cls):
        input_folders = {"lead": os.environ.get("LEAD_INPUT_FOLDER"), "pos": os.environ.get("POS_INPUT_FOLDER"),
                         "contact":os.environ.get("CONTACT_INPUT_FOLDER"),
                         "match_result":os.environ.get("MATCH_RESULT_INPUT_FOLDER")}
        return cls(input_bucket_name=os.environ.get("INPUT_BUCKET_NAME"),
                   archive_bucket_name=os.environ.get("ARCHIVE_BUCKET_NAME"),
                   lead_input_folder=os.environ.get("LEAD_INPUT_FOLDER"),
                   contact_input_folder=os.environ.get("CONTACT_INPUT_FOLDER"),
                   pos_input_folder=os.environ.get("POS_INPUT_FOLDER"),
                   match_result_input_folder=os.environ.get("MATCH_RESULT_INPUT_FOLDER"),
                   archive_folder=os.environ.get("ARCHIVE_FOLDER"),
                   project_id=os.environ.get("GCP_PROJECT_ID"),
                  input_folders=input_folders
                   )


@dataclass
class JobConfig:
    data_load_type: str
    snow_config: SnowConfig
    storage_config: StorageConfig
    db_config: DatabaseDetail

    @classmethod
    def from_env(cls):
        return cls(data_load_type=os.environ.get("DATA_LOAD_TYPE", "delta"),
                   snow_config=SnowConfig.from_env(),
                   storage_config=StorageConfig.from_env(),
                   db_config=DatabaseDetail.from_env()
                   )

