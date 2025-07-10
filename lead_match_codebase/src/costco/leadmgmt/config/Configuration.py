import configparser
import json
import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes
import os
from dataclasses import dataclass
from costco.leadmgmt.util import apputil


@dataclass
class MatchQuery:
    query_leads:str
    query_pos:str
    query_leads_insert_ids:str
    query_pos_inserts_ids:str
    query_leads_update_ids:str
    query_fuzzy_wh:str
    query_fuzzy_null_wh: str
    update_match_audit_query:str
    query_match_configuration:str
    failed_status_query:str
    create_temp_table_transaction: str
    create_temp_table_lead: str
    insert_query_transaction: str
    insert_query_lead: str

    @classmethod
    def from_config(cls, config):

        return cls(query_leads=config.get("QUERY","query_leads"),
                query_pos=config.get("QUERY","query_pos"),
                query_leads_insert_ids=config.get("QUERY","query_leads_insert_ids"),
                query_pos_inserts_ids =config.get("QUERY","query_pos_inserts_ids"),
                query_leads_update_ids=config.get("QUERY","query_leads_update_ids"),
                query_fuzzy_wh=config.get("QUERY","query_fuzzy_wh"),
                query_fuzzy_null_wh=config.get("QUERY","query_fuzzy_null_wh"),
                create_temp_table_transaction=config.get("QUERY","create_temp_table_transaction"),
                create_temp_table_lead=config.get("QUERY","create_temp_table_lead"),
                insert_query_transaction=config.get("QUERY","insert_query_transaction"),
                insert_query_lead=config.get("QUERY","insert_query_lead"),
                update_match_audit_query =config.get("QUERY","update_match_audit_query"),
                query_match_configuration =config.get("QUERY","query_match_configuration"),
                failed_status_query =config.get("QUERY","failed_status_query")
                   )



@dataclass
class DatabaseDetail:
    db_user: str
    schema_name: str
    instance_connection_name: str
    db_name: str
    project_id: str
    insert_lead_table_name: str
    insert_pos_table_name: str
    audit_table_name: str
    ip_type: IPTypes

    def __repr__(self):
        return str({"db_user": self.db_user,  "schema_name": self.schema_name
                       , "instance_connection_name": self.instance_connection_name, "db_name": self.db_name
                       , "project_id": self.project_id, "insert_lead_table_name": self.insert_lead_table_name,
                    "insert_pos_table_name": self.insert_pos_table_name
                       , "audit_table_name": self.audit_table_name, "ip_type": self.ip_type})

    def __str__(self):
        return str({"db_user": self.db_user, "schema_name": self.schema_name
                       , "instance_connection_name": self.instance_connection_name, "db_name": self.db_name
                       , "project_id": self.project_id, "insert_lead_table_name": self.insert_lead_table_name,
                    "insert_pos_table_name": self.insert_pos_table_name
                       , "audit_table_name": self.audit_table_name, "ip_type": self.ip_type})

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
        db_user: str = os.environ.get("POSTGRES_DB_USER")
        project_id = os.environ.get("GCP_PROJECT_ID")
        # db_user: str = apputil.access_secret_version(project_id, db_user_id, version_id="latest")
        # db_password: str = apputil.access_secret_version(project_id, db_password_id, version_id="latest")

        return cls(
            schema_name=os.environ.get("DB_SCHEMA"),
            instance_connection_name=os.environ.get("DB_CONNECTION_NAME"),
            db_user=db_user,
            db_name=os.environ.get("POSTGRES_DB_NAME"),
            project_id=project_id,
            insert_lead_table_name=os.environ.get("INSERT_LEAD_TABLE_NAME"),
            insert_pos_table_name=os.environ.get("INSERT_POS_TABLE_NAME"),
            audit_table_name=os.environ.get("AUDIT_TABLE_NAME"),
            ip_type=ip_type
        )

    @staticmethod
    def get_config(section, key, config, default):
        env_value = os.getenv(key.upper())
        return env_value if env_value is not None else config.get(section, key, fallback=default)

    @classmethod
    def from_config(cls, config):
        sql_ip_type = cls.get_config("DATABASE", "cloud_sql_ip_type", config, "PRIVATE")
        # Initialize the dataclass from environment variables
        if sql_ip_type == "PRIVATE":
            ip_type = IPTypes.PRIVATE
        elif sql_ip_type == "PUBLIC":
            ip_type = IPTypes.PUBLIC
        elif sql_ip_type == "PSC":
            ip_type = IPTypes.PSC
        else:
            ip_type = os.environ.get("CLOUD_SQL_IP_TYPE")
        db_user: str = cls.get_config("DATABASE", "postgres_db_user", config, "postgres")
        # db_password_id: str = cls.get_config("DATABASE", "postgres_db_password_id", config, "postgres")
        project_id = cls.get_config("DATABASE", "gcp_project_id", config, None)
        # db_user: str = apputil.access_secret_version(project_id, db_user_id, version_id="latest")
        # db_password: str = apputil.access_secret_version(project_id, db_password_id, version_id="latest")

        return cls(
            schema_name=cls.get_config("DATABASE", "db_schema", config, None),
            instance_connection_name=cls.get_config("DATABASE", "db_connection_name", config, None),
            db_user=db_user,
            db_name=cls.get_config("DATABASE", "postgres_db_name", config, None),
            project_id=project_id,
            insert_lead_table_name=cls.get_config("DATABASE", "insert_lead_table_name", config, None),
            insert_pos_table_name=cls.get_config("DATABASE", "insert_pos_table_name", config, None),
            audit_table_name=cls.get_config("DATABASE", "audit_table_name", config, None),
            ip_type=ip_type
        )

    def get_conn(self):
        # initialize Connector object
        connector = Connector()
        print("inside get connection")

        print(f"  instance_connection_name = {self.instance_connection_name}")
        print(f"  db_user                  = {self.db_user}")
        print(f"  db_name                  = {self.db_name}")
        print(f"  ip_type                  = {self.ip_type}")

        conn = connector.connect(
            self.instance_connection_name,
            "pg8000",
            user=self.db_user,
            enable_iam_auth=True,
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
    contact_url: str
    match_result_url: str
    max_batch_size: int
    project_id: str
    snow_password: str
    snow_user: str
    api_urls: dict
    default_start_date: str
    match_result_update_url: str
    batch_size: int
    max_retries: int
    retry_delay: int

    def __repr__(self):
        return {"lead_url": self.lead_url, "pos_url": self.pos_url
            , "match_result_url": self.match_result_url, "max_batch_size": self.max_batch_size
            , "project_id": self.project_id, "snow_user": self.snow_user, "snow_password": "*********",
                "default_start_date": self.default_start_date}

    def __str__(self):
        return str({"lead_url": self.lead_url, "pos_url": self.pos_url
                       , "match_result_url": self.match_result_url, "max_batch_size": self.max_batch_size
                       , "project_id": self.project_id, "snow_user": self.snow_user, "snow_password": "*********",
                    "default_start_date": self.default_start_date})

    @staticmethod
    def get_config(section, key, config, default):
        env_value = os.getenv(key.upper())
        return env_value if env_value is not None else config.get(section, key, fallback=default)

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
        match_result_update_url = os.environ.get("MATCH_URL")
        batch_size = int(os.environ.get("BATCH_SIZE"))
        max_retries = int(os.environ.get("MAX_RETRIES"))
        retry_delay = int(os.environ.get("RETRY_DELAY"))
        urls = {'pos': pos_url, "lead": lead_url, "contact": contact_url, "match": match_url}

        return cls(
            lead_url=os.environ.get("LEAD_URL"),
            pos_url=os.environ.get("POS_URL"),
            match_result_url=os.environ.get("MATCH_RESULT_URL"),
            max_batch_size=int(os.environ.get("BATCH_SIZE")),
            project_id=project_id,
            snow_password=snow_password,
            snow_user=snow_user,
            api_urls=urls,
            contact_url=contact_url,
            default_start_date=os.environ.get("DEFAULT_START_DATE"),
            match_result_update_url = match_result_update_url,
            batch_size = batch_size,
            max_retries = max_retries,
            retry_delay = retry_delay

        )

    @classmethod
    def from_config(cls, config):
        snow_user_id = cls.get_config("SERVICENOW", "snow_user", config, None)
        snow_password_id = cls.get_config("SERVICENOW", "snow_password", config, None)
        project_id: str = cls.get_config("SERVICENOW", "gcp_project_id", config, None)
        snow_password: str = apputil.access_secret_version(project_id, snow_password_id, version_id="latest")
        snow_user: str = apputil.access_secret_version(project_id, snow_user_id, version_id="latest")
        lead_url = cls.get_config("SERVICENOW", "lead_url", config, None)
        contact_url = cls.get_config("SERVICENOW", "contact_url", config, None)
        match_url = cls.get_config("SERVICENOW", "match_url", config, None)
        pos_url = cls.get_config("SERVICENOW", "pos_url", config, None)
        match_result_update_url =cls.get_config("SERVICENOW", "match_result_update_url", config, None)
        batch_size = cls.get_config("SERVICENOW", "batch_size", config, None)
        max_retries = cls.get_config("SERVICENOW", "max_retries", config, None)
        retry_delay = cls.get_config("SERVICENOW", "retry_delay", config, None)
        urls = {'pos': pos_url, "lead": lead_url, "contact": contact_url, "match": match_url}

        return cls(
            lead_url=lead_url,
            pos_url=pos_url,
            contact_url=contact_url,
            match_result_url=match_url,
            max_batch_size=int(cls.get_config("SERVICENOW", "max_batch_size", config, None)),
            project_id=project_id,
            snow_password=snow_password,
            snow_user=snow_user,
            api_urls=urls,
            default_start_date=cls.get_config("SERVICENOW", "default_start_date", config, "2025-05-07 00:00:00"),
            match_result_update_url=match_result_update_url,
            batch_size=batch_size,
            max_retries=max_retries,
            retry_delay=retry_delay
        )


@dataclass
class StorageConfig:
    input_bucket_name: str
    output_bucket_name: str
    archive_bucket_name: str
    lead_input_folder: str
    contact_input_folder: str
    pos_input_folder: str
    match_result_input_folder: str
    archive_folder: str
    input_folders: dict
    project_id: str
    temporary_folder: str
    output_folder: str
    leads_classified_file_name: str
    source_bucket_name: str
    source_folder_input_leads: str
    destination_folder_input_leads: str
    source_folder_input_pos: str
    destination_folder_input_pos: str
    destination_bucket_name: str
    source_folder_output: str
    destination_folder_output: str
    standalone_file_path: str

    @staticmethod
    def get_config(section, key, config, default):
        env_value = os.getenv(key.upper())
        return env_value if env_value is not None else config.get(section, key, fallback=default)

    @classmethod
    def from_env(cls):
        input_folders = {"lead": os.environ.get("LEAD_INPUT_FOLDER"), "pos": os.environ.get("POS_INPUT_FOLDER"),
                         "contact": os.environ.get("CONTACT_INPUT_FOLDER"),
                         "match_result": os.environ.get("MATCH_RESULT_INPUT_FOLDER")}
        return cls(input_bucket_name=os.environ.get("INPUT_BUCKET_NAME"),
                   output_bucket_name=os.environ.get("OUTPUT_BUCKET_NAME"),
                   archive_bucket_name=os.environ.get("ARCHIVE_BUCKET_NAME"),
                   lead_input_folder=os.environ.get("LEAD_INPUT_FOLDER"),
                   contact_input_folder=os.environ.get("CONTACT_INPUT_FOLDER"),
                   pos_input_folder=os.environ.get("POS_INPUT_FOLDER"),
                   match_result_input_folder=os.environ.get("MATCH_RESULT_INPUT_FOLDER"),
                   archive_folder=os.environ.get("ARCHIVE_FOLDER"),
                   project_id=os.environ.get("GCP_PROJECT_ID"),
                   input_folders=input_folders,
                   temporary_folder=os.environ.get("TEMPORARY_FOLDER"),
                   output_folder=os.environ.get("OUTPUT_FOLDER"),
                   leads_classified_file_name=os.environ.get("LEADS_CLASSIFIED_FILE_NAME"),
                   source_bucket_name=os.environ.get("SOURCE_BUCKET_NAME"),
                   source_folder_input_leads=os.environ.get("SOURCE_FOLDER_INPUT_LEADS"),
                   destination_folder_input_leads=os.environ.get("DESTINATION_FOLDER_INPUT_LEADS"),
                   source_folder_input_pos=os.environ.get("SOURCE_FOLDER_INPUT_POS"),
                   destination_folder_input_pos=os.environ.get("DESTINATION_FOLDER_INPUT_POS"),
                   destination_bucket_name=os.environ.get("DESTINATION_BUCKET_NAME"),
                   source_folder_output=os.environ.get("SOURCE_FOLDER_OUTPUT"),
                   destination_folder_output=os.environ.get("GCP_PROJECT_ID"),
                   standalone_file_path=os.environ.get("STANDALONE_FILE_PATH"))

    @classmethod
    def from_config(cls, config):
        lead_input_folder = cls.get_config("STORAGE", "lead_input_folder", config, "staging/lead")
        pos_input_folder = cls.get_config("STORAGE", "pos_input_folder", config, "staging/pos")
        contact_input_folder = cls.get_config("STORAGE", "contact_input_folder", config, "staging/contact")
        match_result_input_folder = cls.get_config("STORAGE", "match_result_input_folder", config, "match_result")

        input_folders = {"lead": lead_input_folder,
                         "pos": pos_input_folder,
                         "contact": contact_input_folder,
                         "match_result": match_result_input_folder}

        return cls(input_bucket_name=cls.get_config("STORAGE", "input_bucket_name", config, ""),
                   archive_bucket_name=cls.get_config("STORAGE", "archive_bucket_name", config, ""),
                   lead_input_folder=lead_input_folder,
                   contact_input_folder=contact_input_folder,
                   pos_input_folder=pos_input_folder,
                   match_result_input_folder=match_result_input_folder,
                   archive_folder=cls.get_config("STORAGE", "archive_folder", config, "archive"),
                   input_folders=input_folders,
                   project_id=cls.get_config("STORAGE", "project_id", config, ""),
                   output_bucket_name=cls.get_config("STORAGE", "output_bucket_name", config, ""),
                   temporary_folder=cls.get_config("STORAGE", "temporary_folder", config, ""),
                   output_folder=cls.get_config("STORAGE", "output_folder", config, ""),
                   leads_classified_file_name=cls.get_config("STORAGE", "leads_classified_file_name", config, ""),
                   source_bucket_name=cls.get_config("STORAGE", "source_bucket_name", config, ""),
                   source_folder_input_leads=cls.get_config("STORAGE", "source_folder_input_leads", config, ""),
                   destination_folder_input_leads=cls.get_config("STORAGE", "destination_folder_input_leads", config,
                                                                 ""),
                   source_folder_input_pos=cls.get_config("STORAGE", "source_folder_input_pos", config, ""),
                   destination_folder_input_pos=cls.get_config("STORAGE", "destination_folder_input_pos", config, ""),
                   destination_bucket_name=cls.get_config("STORAGE", "destination_bucket_name", config, ""),
                   source_folder_output=cls.get_config("STORAGE", "source_folder_output", config, ""),
                   destination_folder_output=cls.get_config("STORAGE", "destination_folder_output", config, ""),
                   standalone_file_path=cls.get_config("STORAGE", "standalone_file_path", config, "")

                   )


@dataclass
class TransformConfig:
    initial_load_lead_mapping: dict
    initial_load_pos_mapping: dict
    delta_load_lead_mapping: dict
    delta_load_contact_mapping: dict
    delta_load_pos_mapping: dict
    initial_load_lead_datatype_mapping: dict
    initial_load_pos_datatype_mapping: dict
    delta_load_lead_datatype_mapping: dict
    delta_load_pos_datatype_mapping: dict
    lead_columns: list
    pos_columns: list
    account_columns: list
    contact_columns: list

    def __repr__(self):
        return {"initial_load_lead_mapping": self.initial_load_lead_mapping,
                "initial_load_pos_mapping": self.initial_load_pos_mapping
            , "delta_load_lead_mapping": self.delta_load_lead_mapping,
                "delta_load_pos_mapping": self.delta_load_pos_mapping
            , "initial_load_lead_datatype_mapping": self.initial_load_lead_datatype_mapping,
                "initial_load_pos_datatype_mapping": self.initial_load_pos_datatype_mapping,
                "delta_load_lead_datatype_mapping": self.delta_load_lead_datatype_mapping,
                "delta_load_pos_datatype_mapping": self.delta_load_pos_datatype_mapping,
                "lead_columns": self.lead_columns,
                "pos_columns": self.pos_columns,
                "account_columns": self.account_columns,
                "contact_columns": self.contact_columns
                }

    def __str__(self):
        return f""" initial_load_lead_mapping : {self.initial_load_lead_mapping}, initial_load_pos_mapping: {self.initial_load_pos_mapping}
            ,  delta_load_lead_mapping : {self.delta_load_lead_mapping}, delta_load_pos_mapping: {self.delta_load_pos_mapping}
            , initial_load_lead_datatype_mapping: {self.initial_load_lead_datatype_mapping},
        initial_load_pos_datatype_mapping: {self.initial_load_pos_datatype_mapping},
               delta_load_lead_datatype_mapping: {self.delta_load_lead_datatype_mapping},
               delta_load_pos_datatype_mapping: {self.delta_load_pos_datatype_mapping} ,
                lead_columns": {self.lead_columns},
              pos_columns": {self.pos_columns}"""

    @staticmethod
    def get_config(section, key, config, default):
        env_value = os.getenv(key.upper())
        return env_value if env_value is not None else config.get(section, key, fallback=default)

    @classmethod
    def from_config(cls, config):
        initial_load_lead_mapping = json.loads(str(config.get('TRANSFORMATION', 'initial_load_lead_mapping')))
        initial_load_pos_mapping = json.loads(config.get('TRANSFORMATION', 'initial_load_pos_mapping'))
        initial_load_lead_datatype_mapping = json.loads(
            config.get('TRANSFORMATION', 'initial_load_lead_datatype_mapping'))
        initial_load_pos_datatype_mapping = json.loads(
            config.get('TRANSFORMATION', 'initial_load_pos_datatype_mapping'))
        delta_load_lead_mapping = json.loads(config.get('TRANSFORMATION', 'delta_load_lead_mapping'))
        delta_load_contact_mapping = json.loads(config.get('TRANSFORMATION', 'delta_load_contact_mapping'))
        delta_load_pos_mapping = json.loads(config.get('TRANSFORMATION', 'delta_load_pos_mapping'))
        delta_load_lead_datatype_mapping = json.loads(config.get('TRANSFORMATION', 'delta_load_lead_datatype_mapping'))
        delta_load_pos_datatype_mapping = json.loads(config.get('TRANSFORMATION', 'delta_load_pos_datatype_mapping'))
        return cls(
            initial_load_lead_mapping=initial_load_lead_mapping,
            initial_load_pos_mapping=initial_load_pos_mapping,
            initial_load_lead_datatype_mapping=initial_load_lead_datatype_mapping,
            initial_load_pos_datatype_mapping=initial_load_pos_datatype_mapping,
            delta_load_pos_mapping=delta_load_pos_mapping,
            delta_load_lead_mapping=delta_load_lead_mapping,
            delta_load_contact_mapping=delta_load_contact_mapping,
            delta_load_lead_datatype_mapping=delta_load_lead_datatype_mapping,
            delta_load_pos_datatype_mapping=delta_load_pos_datatype_mapping,
            lead_columns=json.loads(config.get('TRANSFORMATION', 'lead_columns')),
            pos_columns=json.loads(config.get('TRANSFORMATION', 'pos_columns')),
            account_columns=json.loads(config.get('TRANSFORMATION', 'account_columns')),
            contact_columns=json.loads(config.get('TRANSFORMATION', 'contact_columns')),
        )


@dataclass
class JobConfig:
    data_load_type: str
    file_type: str
    file_encoding: str
    gcp_project_id: str
    location: str
    match_job_name: str
    snow_config: SnowConfig
    storage_config: StorageConfig
    db_config: DatabaseDetail
    transform_config: TransformConfig
    match_query: MatchQuery

    @staticmethod
    def get_config(section, key, config, default):
        env_value = os.getenv(key.upper())
        return env_value if env_value is not None \
            else config.get(section, key, fallback=default)

    def __init__(self, config_file='config.ini'):
        config = configparser.ConfigParser()
        config.read(config_file)
        self.data_load_type = self.get_config("GENERAL",
                                              "data_load_type", config, "delta")
        self.file_type = self.get_config("GENERAL",
                                         "file_type", config, "json")
        self.file_encoding = self.get_config("GENERAL",
                                             "file_encoding", config, "utf-8")

        self.gcp_project_id = self.get_config("GENERAL",
                                              "gcp_project_id", config, "")
        self.location = self.get_config("GENERAL",
                                        "location", config, "us-central1")
        self.match_job_name = self.get_config("GENERAL",
                                              "match_job_name", config, "lead_matching_job")
        self.transform_config = TransformConfig.from_config(config)
        self.storage_config = StorageConfig.from_config(config)
        self.db_config = DatabaseDetail.from_config(config)
        self.snow_config = SnowConfig.from_config(config)
        self.match_query = MatchQuery.from_config(config)
