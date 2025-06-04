import pandas as pd
import requests
import json
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from google.cloud import storage
import time


def get_failure_logs_df(uri: str) -> str:
    pass

def generate_post_json(df):

    # Normalize types
    df['lead_id'] = df['lead_id'].astype(str)
    df['pos_id'] = df['pos_id'].astype(str)
    df['confidence_level'] = df['confidence_level'].astype(str)
    df['account_number'] = df['account_number'].astype(int)
    df['similarity_score'] = pd.to_numeric(df['similarity_score'], errors='coerce')

    # Step 1: Sort by similarity_score in descending order
    df_sorted = df.sort_values(by=['lead_id', 'similarity_score'], ascending=[True, False])

    # Step 2: Drop duplicates to keep only the row with max similarity_score per lead_id
    top_rows = df_sorted.drop_duplicates(subset='lead_id', keep='first')[
        ['lead_id', 'confidence_level', 'account_number']
    ]

    # Step 3: Get list of all pos_ids per lead_id
    pos_ids_df = df.groupby('lead_id')['pos_id'].apply(list).reset_index()

    # Step 4: Merge the two
    merged = pd.merge(top_rows, pos_ids_df, on='lead_id')

    # Step 5: Rename columns to match required JSON
    merged = merged.rename(columns={
        'confidence_level': 'confidence',
        'pos_id': 'pos_ids'
    })

    # Step 6: Convert to JSON list
    return merged.to_dict(orient='records')

def process_batches(data,batch_size,url,max_retries,retry_delay,username=None, password=None):
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    auth = (username, password) if username and password else None

    for i in range(0, len(data), batch_size):
        batch = data[i:i+batch_size]
        payload = json.dumps(batch)
        batch_number = i // batch_size + 1

        success = False

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, data=payload, auth=auth)

                if response.status_code == 200:
                    try:
                        result = response.json().get("result", {})
                    except ValueError:
                        print(f"[Batch {batch_number}] Attempt {attempt}: Failed to parse JSON response.")
                        break

                    status = result.get("status", "").lower()
                    message = result.get("message", "")
                    count = result.get("successcount", "")

                    if status == "success":
                        print(f"[Batch {batch_number}] Success: {message}, Count: {count}")
                    else:
                        print(f"[Batch {batch_number}] Failed: {message}")
                    success = True
                    break

                elif response.status_code == 404:
                    print(f"[Batch {batch_number}] Attempt {attempt}: Resource not found (404). Not retrying.")
                    break

                else:
                    print(f"[Batch {batch_number}] Attempt {attempt}: HTTP {response.status_code} - {response.text}")

            except requests.RequestException as e:
                print(f"[Batch {batch_number}] Attempt {attempt}: Request failed - {e}")

            if attempt < max_retries:
                time.sleep(retry_delay)

        if not success:
            print(f"[Batch {batch_number}] Failed after {max_retries} attempts.")



def update_servicenow(config_file_path: str,file_path: str = ""):
    # Initialization
    job_config = JobConfig(config_file_path)
    # in case of failure
    # storage_config = job_config.storage_config
    servicenow_config = job_config.snow_config

    BATCH_SIZE = servicenow_config.batch_size
    url= servicenow_config.match_result_update_url
    MAX_RETRIES=servicenow_config.max_retries
    RETRY_DELAY=servicenow_config.retry_delay
    username = servicenow_config.snow_user
    password = servicenow_config.snow_password


    if file_path == "":
        final_df = get_failure_logs_df()
    else:
        # Load the files from GCS into pandas DataFrames
        final_df = load_file_from_gcs(file_path)

    final_df = final_df[final_df['confidence_level'].isin(['High', 'Medium', 'Low'])]
    final_df = final_df[['lead_id', 'pos_id', 'confidence_level', 'account_number', 'similarity_score']]
    json_data = generate_post_json(final_df)
    process_batches(json_data, BATCH_SIZE, url, MAX_RETRIES, RETRY_DELAY,username,password)
