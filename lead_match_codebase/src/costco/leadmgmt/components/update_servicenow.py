"""
Send match results from GCS final output to ServiceNow.

Includes:
  - PII-redacted debug logging at key stages (dataframe load, payload
    generation, per-batch POST request, failures)
  - Failed batches now RAISE at the end of process_batches instead of
    being silently swallowed
"""

import json
import time

import pandas as pd
import requests
from google.cloud import storage

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs


# ==============================================================
# PII REDACTION HELPER (for safe logging)
# ==============================================================
def _redact_for_logging(record: dict) -> dict:
    """
    Returns a copy of a payload record with PII fields masked for safe
    logging. Useful for printing samples to Cloud Logging without exposing
    customer email, phone, names, addresses, or membership numbers.

    IDs (pos_id, lead_id, account_number, warehouse_number) stay visible
    so you can still identify rows in logs for troubleshooting.
    """
    PII_FIELDS = {
        "u_email",
        "u_phone_number",
        "u_first",
        "u_last",
        "u_address_1",
        "u_address_2",
        "u_zip_code",
        "u_membership_number",
    }

    def _mask(v):
        if not v or v == "":
            return v
        v_str = str(v)
        if len(v_str) <= 3:
            return "***"
        return v_str[:2] + "*" * (len(v_str) - 2)

    redacted = {}
    for key, value in record.items():
        if key in PII_FIELDS:
            redacted[key] = _mask(value)
        elif isinstance(value, dict):
            redacted[key] = _redact_for_logging(value)
        else:
            redacted[key] = value
    return redacted


# ==============================================================
# GCS FILE PATH RESOLUTION
# ==============================================================
def get_gcs_file_path(uri: str) -> str:

    if not uri.startswith("gs://"):
        raise ValueError("Invalid GCS URI. Must start with 'gs://'.")

    path = uri[5:]
    parts = path.split('/', 1)
    bucket_name = parts[0]
    folder_path = parts[1] if len(parts) > 1 else ""

    if folder_path and not folder_path.endswith('/'):
        folder_path += '/'

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=folder_path))
    files = [blob.name for blob in blobs if not blob.name.endswith('/')]

    if len(files) != 1:
        raise ValueError(
            f"Expected exactly one file in '{uri}', found {len(files)}"
        )

    return f"gs://{bucket_name}/{files[0]}"


# ==============================================================
# OAUTH TOKEN
# ==============================================================
def get_oauth_token(token_url: str, client_id: str, client_secret: str) -> str:

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(
        token_url,
        data=payload,
        headers=headers,
        timeout=(10, 60),
    )
    response.raise_for_status()

    token_response = response.json()
    access_token = token_response.get("access_token")

    if not access_token:
        raise Exception("OAuth token missing in token response")

    return access_token


# ==============================================================
# GENERATE SERVICENOW PAYLOAD
# ==============================================================
def generate_post_json(df):

    # ── Normalize types ──
    df = df.fillna("")

    numeric_columns = [
        "similarity_score",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "warehouse_number",
        "primary_transaction",
        "order_amount",
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # ── Counts ──
    unique_count_lead = df['lead_id'].nunique()
    print("Number of unique lead IDs:", unique_count_lead)

    unique_count_pos = df['pos_id'].nunique()
    print("Number of unique pos IDs:", unique_count_pos)

    # ── Build result payload ──
    results = []

    for _, row in df.iterrows():
        result = {
            # POS information
            "u_gcp_id":            str(row.get("pos_id", "")),
            "active":              "true",
            "u_type":              str(row.get("shop_type", "")),
            "u_business_name":     str(row.get("business_name_transaction", "")),
            "u_address_1":         str(row.get("address_line_one", "")),
            "u_address_2":         str(row.get("address_line_two", "")),
            "u_first":             str(row.get("first_name", "")),
            "u_last":              str(row.get("last_name", "")),
            "u_city":              str(row.get("city", "")),
            "u_state_pos":         str(row.get("state", "")),
            "u_zip_code":          str(row.get("zip_code", "")),
            "u_email":             str(row.get("email", "")),
            "u_phone_number":      str(row.get("phone", "")),

            # Transaction details
            "u_fiscal_year":       str(int(row.get("fiscal_year_transaction", 0))),
            "u_period_1":          str(int(row.get("fiscal_period_transaction", 0))),
            "u_week":              str(int(row.get("week", 0))),
            "u_sales_reference_id": str(row.get("sales_reference_id", "")),
            "u_account_number":    str(row.get("account_number", "")),
            "u_warehouse_number":  str(int(row.get("warehouse_number", 0))),
            "u_membership_number": str(row.get("membership_number", "")),
            "u_industry_description": "",
            "u_bd_industry_pos":   str(row.get("bd_industry", "")),
            "u_order_amount_rounded": str(round(float(row.get("order_amount", 0)), 2)),

            # Match details
            "u_matching_comments": str(row.get("matching_comments", "")),
            "u_matched_by":        "System",
            "u_match_value":       str(row.get("similarity_score", "")),
            "u_match_result":      str(row.get("match_result", "")),

            # Matched lead
            "u_matched_lead": {
                "number": str(row.get("lead_id", ""))
            },
        }
        results.append(result)

    total_matched = unique_count_pos

    # ── DEBUG: Sample of first generated record (redacted) ──
    if results:
        print("\n" + "=" * 60)
        print("DEBUG: First generated record (before sending)")
        print("=" * 60)
        print(json.dumps(_redact_for_logging(results[0]), indent=2))
        print("=" * 60)
        print(f"Total records generated: {len(results)}\n")

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
        access_token,
):

    headers = {
        'Content-Type':  'application/json',
        'Accept':        'application/json',
        'Authorization': f'Bearer {access_token}',
    }

    batch_size = int(batch_size)
    max_retries = int(max_retries)
    retry_delay = int(retry_delay)

    print("URL:", url)
    print("OAuth Enabled: True")
    print(f"Total records to send: {len(data)}")
    print(f"Batch size: {batch_size}")

    failed_batches = []
    total_batches = (len(data) + batch_size - 1) // batch_size

    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        batch_number = i // batch_size + 1

        payload = json.dumps({"result": batch})

        print(
            f"\n[Batch {batch_number}/{total_batches}] "
            f"Records: {len(batch)}, Payload size: {len(payload)} bytes"
        )

        # ── DEBUG: For batch 1 only, show first 2 records of payload (redacted) ──
        if batch_number == 1:
            print(f"[Batch 1] First 2 records of payload (redacted):")
            sample_payload = {
                "result": [_redact_for_logging(r) for r in batch[:2]]
            }
            sample_str = json.dumps(sample_payload, indent=2)
            print(sample_str[:3000])
            if len(sample_str) > 3000:
                print("[Batch 1] (truncated to first 3000 chars)")

        success = False
        last_error = None
        last_response_text = None

        for attempt in range(1, max_retries + 1):

            try:
                print(
                    f"[Batch {batch_number}] Attempt {attempt}/{max_retries}: "
                    f"POST (payload {len(payload)} bytes)"
                )

                response = requests.post(
                    url,
                    headers=headers,
                    data=payload,
                    timeout=(10, 120),
                )

                print(
                    f"[Batch {batch_number}] Status Code: {response.status_code}"
                )

                response_preview = response.text[:1000]
                print(f"[Batch {batch_number}] Response: {response_preview}")
                if len(response.text) > 1000:
                    print(
                        f"[Batch {batch_number}] "
                        f"(Response truncated; full size: {len(response.text)} bytes)"
                    )

                last_response_text = response.text

                if response.status_code in [200, 201]:
                    success = True
                    print(f"[Batch {batch_number}] ✅ Success")
                    break
                else:
                    last_error = (
                        f"HTTP {response.status_code} - {response.text[:300]}"
                    )
                    print(
                        f"[Batch {batch_number}] ❌ Attempt {attempt}: {last_error}"
                    )

            except requests.RequestException as e:
                last_error = f"Request exception: {e}"
                print(f"[Batch {batch_number}] ❌ Attempt {attempt}: {last_error}")

            if attempt < max_retries:
                print(
                    f"[Batch {batch_number}] Sleeping {retry_delay}s before retry..."
                )
                time.sleep(retry_delay)

        if not success:
            print(
                f"[Batch {batch_number}] ❌ FAILED after {max_retries} attempts. "
                f"Last error: {last_error}"
            )

            # ── DEBUG: log first record of failed batch for diagnosis ──
            print(
                f"[Batch {batch_number}] Failed-batch sample record (redacted):"
            )
            print(json.dumps(_redact_for_logging(batch[0]), indent=2))

            failed_batches.append({
                "batch_number":          batch_number,
                "batch_size":            len(batch),
                "last_error":            last_error,
                "last_response":         (last_response_text[:500]
                                          if last_response_text else None),
                "sample_record_pos_id":  batch[0].get("u_gcp_id", "unknown"),
            })

    # ── Final summary + RAISE if any failed ──
    print("\n" + "=" * 60)
    print("ServiceNow update summary:")
    print(f"  Total batches: {total_batches}")
    print(f"  Successful:    {total_batches - len(failed_batches)}")
    print(f"  Failed:        {len(failed_batches)}")
    print("=" * 60)

    if failed_batches:
        print("Failed batches detail:")
        for fb in failed_batches:
            print(
                f"  - Batch {fb['batch_number']} "
                f"(size {fb['batch_size']}, sample pos_id {fb['sample_record_pos_id']}): "
                f"{fb['last_error']}"
            )
        raise RuntimeError(
            f"ServiceNow update failed: {len(failed_batches)}/{total_batches} "
            f"batches did not succeed after {max_retries} attempts each."
        )

    print("All batches succeeded ✅")


# ==============================================================
# UPDATE SERVICENOW (entry point)
# ==============================================================
def update_servicenow(config_file_path: str, file_path: str = ""):

    # ── Initialization ──
    job_config = JobConfig(config_file_path)
    servicenow_config = job_config.snow_config
    storage_config = job_config.storage_config

    # ── Resolve file path ──
    standalone_file_path = storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    # ── ServiceNow config ──
    BATCH_SIZE   = servicenow_config.batch_size
    url          = servicenow_config.match_result_update_url
    MAX_RETRIES  = servicenow_config.max_retries
    RETRY_DELAY  = servicenow_config.retry_delay
    token_url    = servicenow_config.token_url
    client_id    = servicenow_config.snow_client_id
    client_secret = servicenow_config.snow_client_secret

    # ── Get access token ──
    access_token = get_oauth_token(token_url, client_id, client_secret)
    print("Successfully obtained OAuth token")

    # ── Load final output from GCS ──
    final_df = load_file_from_gcs(file_path)

    # ── DEBUG: dataframe loaded from GCS ──
    print(f"\n=== Loaded dataframe from GCS ===")
    print(f"File:        {file_path}")
    print(f"Total rows:  {len(final_df)}")
    print(f"Columns:     {list(final_df.columns)}")

    if not final_df.empty:
        print(f"Sample row (first, redacted):")
        sample = final_df.iloc[0].to_dict()
        # Convert all values to strings for clean printing (handles NaN, Timestamps)
        sample = {k: (str(v) if v is not None else None) for k, v in sample.items()}
        # Light redaction on the few PII-looking columns
        for pii_col in ("email", "phone", "first_name", "last_name",
                        "address_line_one", "address_line_two",
                        "zip_code", "membership_number"):
            if pii_col in sample and sample[pii_col]:
                v = sample[pii_col]
                sample[pii_col] = v[:2] + "*" * max(len(v) - 2, 0) if len(v) > 2 else "***"
        print(json.dumps(sample, indent=2, default=str))

    if 'match_result' in final_df.columns:
        print(f"Match result distribution: "
              f"{final_df['match_result'].value_counts().to_dict()}")

    # ── Filter to relevant matches ──
    final_df = final_df[
        final_df['match_result'].isin(['Match', 'Potential'])
    ]
    print(f"After filtering for Match/Potential: {len(final_df)} rows")

    if final_df.empty:
        print("No matched/potential records to send. Skipping ServiceNow update.")
        return

    # ── Generate ServiceNow payload ──
    total_matched, json_data = generate_post_json(final_df)

    # ── Process batches (raises if any fail) ──
    process_batches(
        total_matched,
        json_data,
        BATCH_SIZE,
        url,
        MAX_RETRIES,
        RETRY_DELAY,
        access_token,
    )

    print("ServiceNow update completed")