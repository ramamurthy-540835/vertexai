import pandas as pd
from costco.leadmgmt.config.Configuration import JobConfig
from google.cloud import storage
import sqlalchemy
from sqlalchemy import text
from datetime import datetime
from costco.leadmgmt.util.apputil import load_file_from_gcs
from costco.leadmgmt.components.update_servicenow import get_gcs_file_path

from datetime import datetime, timezone

def mark_match_failed(match_id: str, config_file_path: str, error_message: str = ""):
    """Mark a match_audit row as Failed. Idempotent — safe to call multiple times."""
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    engine = db_config.get_engine()

    end_date = datetime.now(timezone.utc)
    
    # Use the existing failed_status_query (from configuration_adt.ini)
    # It does INSERT ... ON CONFLICT (match_id) DO UPDATE — so it handles
    # both "row doesn't exist yet" and "row exists but stuck in InProgress".
    failed_status_query = query_config.failed_status_query
    
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(failed_status_query),
                [{
                    'match_id': match_id,
                    'start_date': end_date,
                    'end_date': end_date,
                    'status': 'Failed',
                    'comments': error_message or "Pipeline stage failed",
                }]
            )
    print(f"⚠️  Marked match_id={match_id} as Failed")
def delete_temp_files_from_gcs(match_id: str, config_file_path: str, file_path: str = ""):
    """Delete temporary files from the 'temporary folder' folder in GCS."""
    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config

    standalone_file_path=storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    # storage
    input_bucket = storage_config.input_bucket_name
    temp_folder = storage_config.temporary_folder

    # query
    update_match_audit_query = query_config.update_match_audit_query

    # engine
    engine = db_config.get_engine()

    final_df = load_file_from_gcs(file_path)

    match_count = final_df[final_df['match_result'].isin(['Match','Potential'])]['lead_id'].nunique()
    high_match_count = final_df[final_df['match_result'] == 'Match']['lead_id'].nunique()
    medium_match_count = final_df[final_df['match_result'] == 'Potential']['lead_id'].nunique()
    end_date = datetime.now()

    stats = f"Complete: {high_match_count}, Potential: {medium_match_count}"

    with engine.connect() as connection:
        with connection.begin():  # Automatically commits the transaction
            # Update Leads table
            connection.execute(
                text(update_match_audit_query),
                [{'match_count': match_count, 'stats': stats, 'status': 'completed',
                  'end_date': end_date, 'match_id': match_id}]
            )

    # Initialize the GCS client
    storage_client = storage.Client()
    bucket = storage_client.bucket(input_bucket)

    # List all objects in the 'temporary_folder' folder
    blobs = bucket.list_blobs(prefix=temp_folder)
    print(blobs)

    for blob in blobs:

        if not blob.name.endswith('/'):
            print(f"Deleting file: gs://{input_bucket}/{blob.name}")
            blob.delete()
        else:
            print(f"Skipping folder: gs://{input_bucket}/{blob.name}")