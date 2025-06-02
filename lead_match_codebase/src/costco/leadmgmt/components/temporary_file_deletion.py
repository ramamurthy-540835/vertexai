import pandas as pd
from costco.leadmgmt.config.Configuration import JobConfig
from google.cloud import storage
import sqlalchemy
from sqlalchemy import text
from datetime import datetime
from costco.leadmgmt.util.apputil import load_file_from_gcs



def delete_temp_files_from_gcs(match_id: str, file_path: str,config_file_path:str):

    """Delete temporary files from the 'temporary folder' folder in GCS."""
    #Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config

    #storage
    input_bucket = storage_config.input_bucket_name
    temp_folder = storage_config.temporary_folder

    #query
    update_match_audit_query = query_config.update_match_audit_query


    #engine
    engine = db_config.get_engine()


    final_df = load_file_from_gcs(file_path)

    # Filter rows with High, Medium, or Low confidence level
    high_medium_low_df = final_df[final_df['confidence_level'].isin(['High', 'Medium', 'Low'])]
    # Filter rows with No Match confidence level
    no_match_df = final_df[final_df['confidence_level'] == 'No Match']
    # Remove duplicates based on lead_id between the two dataframes
    no_match_df_unique = no_match_df[~no_match_df['lead_id'].isin(high_medium_low_df['lead_id'])]
    # Concatenate the two dataframes
    final_df = pd.concat([high_medium_low_df, no_match_df_unique], ignore_index=True)

    match_count = final_df[final_df['confidence_level'] != 'No Match']['lead_id'].nunique()
    no_match_count = final_df[final_df['confidence_level'] == 'No Match']['lead_id'].nunique()
    high_match_count = final_df[final_df['confidence_level'] == 'High']['lead_id'].nunique()
    medium_match_count = final_df[final_df['confidence_level'] == 'Medium']['lead_id'].nunique()
    low_match_count = final_df[final_df['confidence_level'] == 'Low']['lead_id'].nunique()
    end_date = datetime.now()

    stats=f"High: {high_match_count}, Medium: {medium_match_count}, Low: {low_match_count}"

    with engine.connect() as connection:
                with connection.begin():  # Automatically commits the transaction
                    # Update Leads table
                    connection.execute(
                        text(update_match_audit_query),
                        [{'match_count': match_count,'no_match_count':no_match_count, 'stats': stats,'status': 'completed','end_date': end_date,'match_id': match_id} ]
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