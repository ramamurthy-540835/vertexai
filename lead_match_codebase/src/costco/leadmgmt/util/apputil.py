from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
from google.cloud import secretmanager
from google.cloud import storage
from pandas.errors import EmptyDataError

from costco.leadmgmt.util.logger import app_logger

def access_secret_version(project_id, secret_id, version_id="latest"):
    """
    Accesses a secret version from Secret Manager.

    Args:
        project_id: The GCP project ID.
        secret_id: The ID of the secret.
        version_id: The version ID of the secret. Defaults to "latest".

    Returns:
        The secret payload as a string, or None if an error occurs.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"

    try:
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        return payload
    except Exception as e:
        app_logger.error(f"Error accessing secret: {e}")
        return None


def get_costco_fiscal_info(input_date=None):
    # Default to today if no input date is provided
    if input_date is None:
        input_date = datetime.today()
    else:
        input_date = datetime.strptime(input_date, '%Y-%m-%d')

    # Extract year and determine fiscal year
    year = input_date.year
    fiscal_year = year + 1 if input_date.month >= 9 else year

    if input_date.month < 9:
        year = year - 1

        # Find first Monday closest to September 1st of the current fiscal year
    fiscal_start = datetime(year, 9, 1)
    while fiscal_start.weekday() != 0:  # Monday is 0
        fiscal_start += timedelta(days=1)

    # Determine weeks since fiscal start
    days_since_start = (input_date - fiscal_start).days

    weeks_since_start = days_since_start // 7
    # Fiscal periods are 4 weeks long (except the last one)
    fiscal_period = min(12, (weeks_since_start // 4) + 1)

    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period
    }


# Load the files from GCS into pandas DataFrames
def load_file_from_gcs(file_path, dtype=None):
    storage_client = storage.Client()
    bucket_name, file_name = file_path.replace("gs://", "").split("/", 1)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    file_content = blob.download_as_text()
    if not file_content.strip():
        raise ValueError(f"GCS CSV is empty: {file_path}")
    csv_data = StringIO(file_content)
    try:
        return pd.read_csv(csv_data, dtype=dtype)
    except EmptyDataError as exc:
        raise ValueError(f"GCS CSV has no parseable columns: {file_path}") from exc


def build_match_output_uri(storage_config, match_id="", warehouse=""):
    warehouse_label = (warehouse or "all").replace(",", "-").strip() or "all"
    file_name = (
        f"final_update_dataframe_{match_id}_{warehouse_label}.csv"
        if match_id else f"final_update_dataframe_{warehouse_label}.csv"
    )
    return (
        f"gs://{storage_config.output_bucket_name}/"
        f"{storage_config.source_folder_output}/"
        f"{file_name}"
    )


def gcs_blob_exists(file_path):
    if not file_path.startswith("gs://"):
        raise ValueError("Invalid GCS URI. Must start with 'gs://'.")
    storage_client = storage.Client()
    bucket_name, file_name = file_path.replace("gs://", "").split("/", 1)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    return blob.exists()


def process_and_archive_files(source_bucket_name, source_folder, destination_bucket_name, destination_folder, new_file,
                              base_name, service_account_path=None):
    storage_client = storage.Client()
    # Initialize Google Cloud Storage client
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials

    source_bucket = client.bucket(source_bucket_name)
    destination_bucket = client.bucket(destination_bucket_name)

    # Step 1: Check if there are any files in the source folder
    blobs = list(client.list_blobs(source_bucket_name, prefix=source_folder))

    if not blobs:
        print(f"No files found in {source_bucket_name}/{source_folder}")
    else:
        # Step 2: Move files to Archive and delete them from the input folder
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')

        for blob in blobs:
            source_path = blob.name  # Full path of the file in the source folder

            # Create destination path for the archived file
            destination_path = f"{destination_folder}/{timestamp}/{source_path.split('/')[-1]}"

            # Copy the file to the archive bucket
            source_blob = source_bucket.blob(source_path)
            destination_blob = destination_bucket.blob(destination_path)

            # Copy the file (move it)
            destination_blob.rewrite(source_blob)

            # Delete the file from the source bucket after successful copy
            source_blob.delete()

            app_logger.debug(f"Moved {source_path} to {destination_path} and deleted from the source folder.")

    # Step 3: Upload new files to the input folder for the matching process
    # destination_path = f"{source_folder} "
    if new_file is not None:
        file_name = f"{source_folder}/{base_name}.csv"
        bucket = storage_client.get_bucket(source_bucket_name)
        new_blob = bucket.blob(file_name)
        new_blob.upload_from_string(new_file.to_csv(index=False), 'text/csv')
        app_logger.debug(f"Uploaded new file {base_name}.csv to {source_folder}.")
        return f"gs://{source_bucket_name}/{file_name}"
    else:
        app_logger.debug("No DataFrame provided — skipping upload step.")
        return None
