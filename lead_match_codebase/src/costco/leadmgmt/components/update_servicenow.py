import json
import time

import pandas as pd
import requests
from google.cloud import storage

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs


def get_gcs_file_path(uri: str) -> str:

    if not uri.startswith("gs://"):
        raise ValueError(
            "Invalid GCS URI. Must start with 'gs://'."
        )

    # ==========================================================
    # EXTRACT BUCKET + PATH
    # ==========================================================
    path = uri[5:]

    parts = path.split('/', 1)

    bucket_name = parts[0]

    folder_path = parts[1] if len(parts) > 1 else ""

    if folder_path and not folder_path.endswith('/'):
        folder_path += '/'

    # ==========================================================
    # GCS CLIENT
    # ==========================================================
    client = storage.Client()

    bucket = client.bucket(bucket_name)

    blobs = list(
        bucket.list_blobs(prefix=folder_path)
    )

    files = [
        blob.name
        for blob in blobs
        if not blob.name.endswith('/')
    ]

    if len(files) != 1:

        raise ValueError(
            f"Expected exactly one file in '{uri}', "
            f"found {len(files)}"
        )

    return f"gs://{bucket_name}/{files[0]}"


# ==============================================================
# OAUTH TOKEN
# ==============================================================
def get_oauth_token(
        token_url: str,
        client_id: str,
        client_secret: str
) -> str:

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }

    headers = {
        "Content-Type":
            "application/x-www-form-urlencoded"
    }

    response = requests.post(
        token_url,
        data=payload,
        headers=headers,
        timeout=(10, 60)
    )

    response.raise_for_status()

    token_response = response.json()

    access_token = token_response.get("access_token")

    if not access_token:
        raise Exception(
            "OAuth token missing in token response"
        )

    return access_token


# ==============================================================
# GENERATE SERVICENOW PAYLOAD
# ==============================================================
def generate_post_json(df):

    # ==========================================================
    # NORMALIZE TYPES
    # ==========================================================
    df = df.fillna("")

    numeric_columns = [
        "similarity_score",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "warehouse_number",
        "primary_transaction",
        "order_amount"
    ]

    for col in numeric_columns:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            ).fillna(0)

    # ==========================================================
    # COUNTS
    # ==========================================================
    unique_count_lead = df['lead_id'].nunique()

    print(
        "Number of unique lead IDs:",
        unique_count_lead
    )

    unique_count_pos = df['pos_id'].nunique()

    print(
        "Number of unique pos IDs:",
        unique_count_pos
    )

    # ==========================================================
    # BUILD RESULT PAYLOAD
    # ==========================================================
    results = []

    for _, row in df.iterrows():

        result = {

            # ==================================================
            # POS INFORMATION
            # ==================================================
            "u_gcp_id": str(
                    row.get(
                        "pos_id",
                        ""
                    )
                ),
            "active": "true",

            "u_type":
                str(row.get("shop_type", "")),

            "u_business_name":
                str(
                    row.get(
                        "business_name_transaction",
                        ""
                    )
                ),

            "u_address_1":
                str(
                    row.get(
                        "address_line_one",
                        ""
                    )
                ),

            "u_address_2":
                str(
                    row.get(
                        "address_line_two",
                        ""
                    )
                ),

            "u_first":
                str(
                    row.get(
                        "first_name",
                        ""
                    )
                ),

            "u_last":
                str(
                    row.get(
                        "last_name",
                        ""
                    )
                ),

            "u_city":
                str(
                    row.get(
                        "city",
                        ""
                    )
                ),

            "u_state_pos":
                str(
                    row.get(
                        "state",
                        ""
                    )
                ),

            "u_zip_code":
                str(
                    row.get(
                        "zip_code",
                        ""
                    )
                ),

            "u_email":
                str(
                    row.get(
                        "email",
                        ""
                    )
                ),

            "u_phone_number":
                str(
                    row.get(
                        "phone",
                        ""
                    )
                ),

            # ==================================================
            # TRANSACTION DETAILS
            # ==================================================
            "u_fiscal_year":
                str(
                    int(
                        row.get(
                            "fiscal_year_transaction",
                            0
                        )
                    )
                ),

            "u_period_1":
                str(
                    int(
                        row.get(
                            "fiscal_period_transaction",
                            0
                        )
                    )
                ),

            "u_week":
                str(
                    int(
                        row.get(
                            "week",
                            0
                        )
                    )
                ),

            "u_sales_reference_id":
                str(
                    row.get(
                        "sales_reference_id",
                        ""
                    )
                ),

            "u_account_number":
                str(
                    row.get(
                        "account_number",
                        ""
                    )
                ),

            "u_warehouse_number":
                str(
                    int(
                        row.get(
                            "warehouse_number",
                            0
                        )
                    )
                ),

            "u_membership_number":
                str(
                    row.get(
                        "membership_number",
                        ""
                    )
                ),

            "u_industry_description": "",

            "u_bd_industry_pos": str(
                    row.get(
                        "bd_industry",
                        ""
                    )
                ),

            "u_order_amount_rounded":
                str(
                    round(
                        float(
                            row.get(
                                "order_amount",
                                0
                            )
                        ),
                        2
                    )
                ),

            # ==================================================
            # MATCH DETAILS
            # ==================================================
            "u_matching_comments":
                str(
                    row.get(
                        "matching_comments",
                        ""
                    )
                ),

            "u_matched_by":
                "System",

            "u_match_value":
                str(
                    row.get(
                        "similarity_score",
                        ""
                    )
                ),

            "u_match_result":
                str(
                    row.get(
                        "match_result",
                        ""
                    )
                ),

            # ==================================================
            # MATCHED LEAD
            # ==================================================
            "u_matched_lead": {
                "number":
                    str(
                        row.get(
                            "lead_id",
                            ""
                        )
                    )
            }

        }

        results.append(result)

    total_matched = unique_count_pos

    return total_matched, results


# ==============================================================
# PROCESS BATCHES
# ==============================================================
def process_batches(
        total_matched,
        data,
        batch_size,
        url,
        max_retries,
        retry_delay,
        access_token
):

    headers = {

        'Content-Type':
            'application/json',

        'Accept':
            'application/json',

        'Authorization':
            f'Bearer {access_token}'
    }

    batch_size = int(batch_size)

    max_retries = int(max_retries)

    retry_delay = int(retry_delay)

    print("URL:", url)

    print("OAuth Enabled: True")

    for i in range(0, len(data), batch_size):

        batch = data[i:i + batch_size]

        batch_number = i // batch_size + 1

        # ======================================================
        # FINAL PAYLOAD
        # ======================================================
        payload = json.dumps({
            "result": batch
        })

        print(
            f"[Batch {batch_number}] "
            f"Payload size: {len(payload)}"
        )

        success = False

        last_error = None

        for attempt in range(1, max_retries + 1):

            try:

                response = requests.post(
                    url,
                    headers=headers,
                    data=payload,
                    timeout=(10, 120)
                )

                print(
                    f"[Batch {batch_number}] "
                    f"Status Code: {response.status_code}"
                )

                print(
                    f"[Batch {batch_number}] "
                    f"Response: {response.text}"
                )

                # ==============================================
                # SUCCESS
                # ==============================================
                if response.status_code in [200, 201]:

                    success = True

                    print(
                        f"[Batch {batch_number}] "
                        f"Success"
                    )

                    break

                # ==============================================
                # FAILURE
                # ==============================================
                else:

                    last_error = (
                        f"HTTP {response.status_code} - "
                        f"{response.text}"
                    )

                    print(
                        f"[Batch {batch_number}] "
                        f"Attempt {attempt}: "
                        f"{last_error}"
                    )

            except requests.RequestException as e:

                last_error = (
                    f"Request failed - {e}"
                )

                print(
                    f"[Batch {batch_number}] "
                    f"Attempt {attempt}: "
                    f"{last_error}"
                )

            # ==============================================
            # RETRY
            # ==============================================
            if attempt < max_retries:

                time.sleep(retry_delay)

        # ======================================================
        # FINAL FAILURE
        # ======================================================
        if not success:

            print(
                f"[Batch {batch_number}] "
                f"Failed after "
                f"{max_retries} attempts. "
                f"Last error: {last_error}"
            )


# ==============================================================
# UPDATE SERVICENOW
# ==============================================================
def update_servicenow(
        config_file_path: str,
        file_path: str = ""
):

    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    job_config = JobConfig(config_file_path)

    servicenow_config = job_config.snow_config

    storage_config = job_config.storage_config

    # ==========================================================
    # FILE PATH
    # ==========================================================
    standalone_file_path = (
        storage_config.standalone_file_path
    )

    if file_path == "":

        file_path = get_gcs_file_path(
            standalone_file_path
        )

    # ==========================================================
    # SERVICENOW CONFIG
    # ==========================================================
    BATCH_SIZE = servicenow_config.batch_size

    url = servicenow_config.match_result_update_url

    MAX_RETRIES = servicenow_config.max_retries

    RETRY_DELAY = servicenow_config.retry_delay

    token_url = servicenow_config.token_url

    client_id = servicenow_config.snow_client_id

    client_secret = servicenow_config.snow_client_secret

    # ==========================================================
    # GET ACCESS TOKEN
    # ==========================================================
    access_token = get_oauth_token(
        token_url,
        client_id,
        client_secret
    )

    print("Successfully obtained OAuth token")

    # ==========================================================
    # LOAD FINAL OUTPUT
    # ==========================================================
    final_df = load_file_from_gcs(file_path)

    final_df = final_df[
        final_df['match_result'].isin(
            ['Match', 'Potential']
        )
    ]

    # ==========================================================
    # GENERATE PAYLOAD
    # ==========================================================
    total_matched, json_data = generate_post_json(
        final_df
    )

    # ==========================================================
    # PROCESS BATCHES
    # ==========================================================
    process_batches(
        total_matched,
        json_data,
        BATCH_SIZE,
        url,
        MAX_RETRIES,
        RETRY_DELAY,
        access_token
    )

    print("ServiceNow update completed")