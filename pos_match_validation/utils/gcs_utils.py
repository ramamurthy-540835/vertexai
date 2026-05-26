from google.cloud import storage
import os

client = storage.Client()

def download_blob(bucket_name, source_blob_name, destination_file_name):

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)

    os.makedirs(os.path.dirname(destination_file_name), exist_ok=True)

    blob.download_to_filename(destination_file_name)

    print(f"Downloaded {source_blob_name} -> {destination_file_name}")


def upload_blob(bucket_name, source_file_name, destination_blob_name):

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_name)

    print(f"Uploaded {source_file_name} -> {destination_blob_name}")