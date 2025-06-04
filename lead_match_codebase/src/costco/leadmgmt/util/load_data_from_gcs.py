from google.cloud import storage
from io import StringIO


# Initialize GCS client
storage_client = storage.Client()

# Load the files from GCS into pandas DataFrames
def load_file_from_gcs(file_path):
    # Access the file from GCS using the full path
    bucket_name, file_name = file_path.replace("gs://", "").split("/", 1)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    file_content = blob.download_as_text()
    csv_data = StringIO(file_content)
    return pd.read_csv(csv_data)