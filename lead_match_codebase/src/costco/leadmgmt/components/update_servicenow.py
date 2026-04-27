import pandas as pd
import requests
import json
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from google.cloud import storage
import time

def get_gcs_file_path(uri: str) -> str:
    if not uri.startswith("gs://"):
        raise ValueError("Invalid GCS URI. Must start with 'gs://'.")

    # Extract bucket and folder from URI
    path = uri[5:]
    parts = path.split('/', 1)
    bucket_name = parts[0]
    folder_path = parts[1] if len(parts) > 1 else ""

    if folder_path and not folder_path.endswith('/'):
        folder_path += '/'

    # Connect to GCS
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # List blobs under the folder
    blobs = list(bucket.list_blobs(prefix=folder_path))
    files = [blob.name for blob in blobs if not blob.name.endswith('/')]

    if len(files) != 1:
        raise ValueError(f"Expected exactly one file in '{uri}', found {len(files)}")

    return f"gs://{bucket_name}/{files[0]}"

def generate_post_json(df):

    # Normalize types
    df['lead_id'] = df['lead_id'].astype(str)
    df['pos_id'] = df['pos_id'].astype(str)
    df['match_result'] = df['match_result'].astype(str)
    df['business_name'] = df['business_name_transaction'].astype(str)
    df['match_value'] = pd.to_numeric(df['similarity_score'], errors='coerce')
    df['matched_by'] = 'System'
    df['fiscal_year'] = df['fiscal_year_transaction'].astype('int64')
    df['fiscal_period'] = df['fiscal_period_transaction'].astype('int64')
    df['week'] = df['week'].astype('int64')
    df['warehouse_number'] = df['warehouse_number'].astype('int64')
    df['primary_transaction'] = df['primary_transaction'].astype('int64')

    unique_count_lead = df['lead_id'].nunique()
    print("Number of unique lead IDs:", unique_count_lead)

    unique_count_pos = df['pos_id'].nunique()
    print("Number of unique pos IDs:", unique_count_pos)

    # build pos results
    results = []
    for _, row in df.iterrows():
        results.append({
            "pos_id":           row['pos_id'],
            "lead_id":          row['lead_id'],
            "business_name":    row['business_name'],
            "warehouse_number": row['warehouse_number'],
            "fiscal_period":    row['fiscal_period'],
            "fiscal_year":      row['fiscal_year'],
            "week":             row['week'],
            "match_value":      row['match_value'],
            "matched_by":       row['matched_by'],
            "match_percentage": None,   # map similarity_score → match_percentage
            "match_result":     row['match_result'],
            "primary_transaction": row['primary_transaction'],
        })
    total_matched = unique_count_pos  # total POS records matched

    return total_matched, results


def process_batches(total_matched, data, batch_size, url, max_retries, retry_delay, username=None, password=None):
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    batch_size, max_retries, retry_delay = map(int, [batch_size, max_retries, retry_delay])  
    auth = (username, password) if username and password else None

    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        batch_number = i // batch_size + 1

        # ---- NEW wrapper structure ----
        payload = json.dumps({
            "result": {
                "total_matched":   str(total_matched),
                "returned_count":  str(len(batch)),
                "results":         batch
            }
        })

        success = False
        last_error = None  # To store the last error message

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, data=payload, auth=auth, timeout=(10, 60))
                print(f"[Batch {batch_number}] Raw response: {response.text}")

                if response.status_code == 200:
                    try:
                        result = response.json().get("result", {})
                    except ValueError:
                        last_error = "Failed to parse JSON response."
                        print(f"[Batch {batch_number}] Attempt {attempt}: {last_error}")
                        print(response)
                        print("The response from the ServiceNow: ",response.text)

                        break

                    status = result.get("status", "").lower()
                    message = result.get("message", "")
                    success_count = result.get("successcount", "")

                    if status == "success":
                        print(f"[Batch {batch_number}] Success: {message}, Success Count: {success_count}")
                        success = True

                    else:
                        last_error = f"Failed: {message}"
                        print(f"[Batch {batch_number}] {last_error}")
                    break

                elif response.status_code == 404:
                    last_error = "Resource not found (404). Not retrying."
                    print(f"[Batch {batch_number}] Attempt {attempt}: {last_error}")
                    break

                else:
                    last_error = f"HTTP {response.status_code} - {response.text}"
                    print(f"[Batch {batch_number}] Attempt {attempt}: {last_error}")

            except requests.RequestException as e:
                last_error = f"Request failed - {e}"
                print(f"[Batch {batch_number}] Attempt {attempt}: {last_error}")

            if attempt < max_retries:
                time.sleep(retry_delay)

        if not success:
            print(f"[Batch {batch_number}] Failed after {max_retries} attempts. Last error: {last_error}")




def update_servicenow(config_file_path: str,file_path: str = ""):
    # Initialization
    job_config = JobConfig(config_file_path)

    # service now configurayion
    servicenow_config = job_config.snow_config
    
    #in case of failure
    storage_config=job_config.storage_config
    standalone_file_path=storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    BATCH_SIZE = servicenow_config.batch_size
    url= servicenow_config.match_result_update_url
    MAX_RETRIES=servicenow_config.max_retries
    RETRY_DELAY=servicenow_config.retry_delay
    username = servicenow_config.snow_user
    password = servicenow_config.snow_password



    final_df = load_file_from_gcs(file_path)

    final_df = final_df[final_df['match_result'].isin(['Complete','Potential'])]
    final_df = final_df[['lead_id', 'pos_id', 'match_result', 'account_number',
                          'similarity_score', 'business_name_transaction',
                          'fiscal_year_transaction', 'fiscal_period_transaction',
                          'week', 'warehouse_number', 'primary_transaction']]
    total_matched,json_data = generate_post_json(final_df)
    process_batches(total_matched,json_data, BATCH_SIZE, url, MAX_RETRIES, RETRY_DELAY,username,password)
