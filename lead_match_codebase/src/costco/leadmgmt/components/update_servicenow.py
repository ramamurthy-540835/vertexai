"""
Send match results from GCS final output to ServiceNow.

Two ServiceNow API calls per run:
  1. Match/Potential matched records → match_result_update_url
     Failures RAISE (fatal — caller retries the whole job).
  2. Closed-Existing leads             → update_closed_existing_url
     Failures LOG ONLY (non-fatal — Match/Potential is already in
     ServiceNow, and the next run will re-attempt any leads still
     in non-CE status).

Includes:
  - PII-redacted debug logging at key stages (dataframe load, payload
    generation, per-batch POST request, failures)
  - OAuth token fetch retries with exponential backoff (handles
    ServiceNow test instance hibernation / transient timeouts)
"""

import json
import logging
import time

import pandas as pd
import requests
from requests.exceptions import ConnectTimeout, ConnectionError as ReqConnError
from google.cloud import storage

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import (
    build_match_output_uri,
    gcs_blob_exists,
    load_file_from_gcs,
)


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

def _safe_columns(val):
    """Strip float suffix from phone numbers read as float64 by pandas."""
    if val in ("", None, "nan", float("nan")):
        return ""
    try:
        return str(int(float(val)))
    except (ValueError, TypeError):
        return str(val).strip()
    
# ==============================================================
# GCS FILE PATH RESOLUTION
# ==============================================================
def get_gcs_file_path(uri: str, match_id: str = "") -> str:

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
    # Retries or archive timing can leave more than one final output in the
    # folder. Prefer this workflow's match_id-stamped CSV, then fall back to
    # the latest CSV so old artifacts remain recoverable.
    blobs = [
        blob for blob in bucket.list_blobs(prefix=folder_path)
        if (
            not blob.name.endswith("/")
            and blob.name.lower().endswith(".csv")
            and (blob.size or 0) > 0
        )
    ]

    if not blobs:
        raise ValueError(f"No non-empty CSV files found in '{uri}'")

    if match_id:
        scoped_blobs = [blob for blob in blobs if match_id in blob.name]
        if scoped_blobs:
            blobs = scoped_blobs
        else:
            print(f"No CSV files containing MATCH_ID '{match_id}' found in '{uri}', using latest CSV")

    blobs.sort(key=lambda blob: blob.updated, reverse=True)
    if len(blobs) > 1:
        print(f"Found {len(blobs)} CSV files in '{uri}', using latest: {blobs[0].name}")

    print(
        "Selected final match CSV: "
        f"gs://{bucket_name}/{blobs[0].name} "
        f"(size={blobs[0].size}, updated={blobs[0].updated})"
    )
    return f"gs://{bucket_name}/{blobs[0].name}"


# ==============================================================
# OAUTH TOKEN (with retry + exponential backoff)
# ==============================================================
def get_oauth_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    max_retries: int = 3,
) -> str:
    """
    Fetch OAuth token from ServiceNow with retry logic.

    Retries on ConnectTimeout and ConnectionError only — these cover:
      - ServiceNow test instance hibernation (needs time to wake up)
      - Transient VPC connector blips

    HTTP errors (4xx/5xx) are NOT retried — they indicate a real
    configuration problem (wrong credentials, bad URL, etc.).

    Backoff: 2s, 4s, 8s between attempts.
    Connect timeout raised from 10s → 15s to give hibernating instances
    more time to respond.
    """
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    last_exc = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                token_url,
                data=payload,
                headers=headers,
                timeout=(15, 60),
            )
            response.raise_for_status()

            token_response = response.json()
            access_token = token_response.get("access_token")

            if not access_token:
                raise Exception("OAuth token missing in token response")

            return access_token

        except (ConnectTimeout, ReqConnError) as e:
            last_exc = e
            wait = 2 ** attempt
            logging.warning(
                "[ServiceNow] OAuth attempt %d/%d failed (%s: %s). "
                "Retrying in %ds...",
                attempt, max_retries, type(e).__name__, str(e)[:200], wait,
            )
            if attempt < max_retries:
                time.sleep(wait)

    raise type(last_exc)(
        f"ServiceNow OAuth token fetch failed after {max_retries} attempts"
    ) from last_exc


# ==============================================================
# GENERATE MATCH/POTENTIAL PAYLOAD
# ==============================================================
def generate_post_json(df):

    # ── Normalize types ──
    df = df.fillna("")
    df = df.replace(
        to_replace=["nan", "NaN", "None", "none", "NULL", "null", "NaT"],
        value="",
    )

    numeric_columns = [
        "similarity_score",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "warehouse_number",
        "primary_transaction",
        "order_amount",
        "transaction_count"
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
    match_result_map = {"match": "Complete Match", "potential": "Potential Match"}

    for _, row in df.iterrows():

        zip_val = row.get("zip_code", "")
        zip_code = str(zip_val).strip() if zip_val not in ("", None, "nan") else ""

        result = {
            # POS information
            "u_gcp_id":            (row.get("pos_id", "")),
            "active":              True,
            "u_type":              str((row.get("shop_type", ""))),
            "u_business_name":     str(row.get("business_name_transaction", "")),
            "u_address_1":         str(row.get("address_line_one", "")),
            "u_address_2":         str(row.get("address_line_two", "")),
            "u_first":             str(row.get("first_name", "")),
            "u_last":              str(row.get("last_name", "")),
            "u_city":              str(row.get("city", "")),
            "u_state_pos":         str(row.get("state", "")),
            "u_zip_code":          zip_code,
            "u_email":             str(row.get("email", "")),
            "u_phone_number": _safe_columns(row.get("phone", "")),

            # Transaction details
            "u_fiscal_year":       str(int(row.get("fiscal_year_transaction", 0))),
            "u_period_1":          str(int(row.get("fiscal_period_transaction", 0))),
            "u_week":              str(int(row.get("week", 0))),
            "u_sales_reference_id": str(row.get("sales_reference_id", "")),
            "u_transaction_count" : str(int(row.get("transaction_count",0))),
            "u_account_number":    _safe_columns(row.get("account_number", "")),
            "u_warehouse_number":  str(int(row.get("warehouse_number", 0))),
            "u_membership_number": _safe_columns(row.get("membership_number", "")),
            "u_industry_description": str(row.get("industry_description", "")),
            "u_bd_industry_pos":   str(row.get("bd_industry", "")),
            "u_order_amount_rounded": str(round(float(row.get("order_amount", 0)), 2)),
            "u_primary_transaction": bool(row.get("primary_transaction", 0)),

            # Match details
            "u_matching_comments": str(row.get("matching_comments", "")),
            "u_matched_by":        "System",
            "u_match_value":       str(row.get("similarity_score", "")),
            "u_match_result":      match_result_map.get(
                str(row.get("match_result", "")).strip().lower(),
                str(row.get("match_result", "")),
            ),

            # Matched lead
            "u_matched_lead": {
                "number": str(row.get("lead_id", ""))
            },
        }
        results.append(result)

    total_matched = unique_count_pos

    if results:
        print("\n" + "=" * 60)
        print("DEBUG: First generated match record (before sending)")
        print("=" * 60)
        print(json.dumps(_redact_for_logging(results[0]), indent=2))
        print("=" * 60)
        print(f"Total match records generated: {len(results)}\n")

    return total_matched, results


# ==============================================================
# GENERATE CLOSED-EXISTING PAYLOAD
# ==============================================================
def generate_ce_json(df):
    """
    Build the Closed-Existing payload.

    Input: dataframe filtered to CE stub rows (closed_existing_flag=True).
    Each row contributes one entry:
        {"number": "<lead_id>", "u_status": "Closed - Existing"}

    Lead IDs are deduplicated — a single CE lead should appear once
    even if the dataframe somehow has duplicates.
    """
    df = df.fillna("")

    # Deduplicate by lead_id; preserve first occurrence
    unique_lead_ids = df["lead_id"].astype(str).str.strip()
    unique_lead_ids = unique_lead_ids[unique_lead_ids != ""].drop_duplicates()

    results = [
        {
            "number":   lead_id,
            "u_status": "Closed - Existing",
        }
        for lead_id in unique_lead_ids
    ]

    if results:
        print("\n" + "=" * 60)
        print("DEBUG: First generated CE record (before sending)")
        print("=" * 60)
        # CE records contain no PII so no redaction needed
        print(json.dumps(results[0], indent=2))
        print("=" * 60)
        print(f"Total CE records generated: {len(results)}\n")

    return results


# ==============================================================
# SHARED BATCH POSTER
# ==============================================================
def _post_batches(
    data,
    batch_size,
    url,
    max_retries,
    retry_delay,
    access_token,
    label,
    sample_id_field,
    redact_samples=True,
):
    """
    Shared batch-posting loop used by both Match/Potential and CE flows.

    Args:
        data:             list of dict records to POST
        batch_size:       records per POST
        url:              target ServiceNow URL
        max_retries:      attempts per batch
        retry_delay:      seconds to sleep between retries
        access_token:     OAuth bearer token
        label:            short string for log lines (e.g. "Match", "CE")
        sample_id_field:  field name to surface in failure logs (e.g.
                          "u_gcp_id" for Match, "number" for CE)
        redact_samples:   apply PII redaction to logged sample records

    Returns:
        list[dict] of failed batches (empty if all succeeded).
    """
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    batch_size  = int(batch_size)
    max_retries = int(max_retries)
    retry_delay = int(retry_delay)

    print(f"\n--- {label}: POST loop ---")
    print(f"URL: {url}")
    print(f"Total records to send: {len(data)}")
    print(f"Batch size: {batch_size}")

    failed_batches = []
    total_batches = (len(data) + batch_size - 1) // batch_size

    if total_batches == 0:
        print(f"[{label}] No records to send.")
        return failed_batches

    def _sample(record):
        return _redact_for_logging(record) if redact_samples else record

    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        batch_number = i // batch_size + 1

        payload = json.dumps({"result": batch})
        print(
            f"\n[{label} Batch {batch_number}/{total_batches}] "
            f"Records: {len(batch)}, Payload size: {len(payload)} bytes"
        )

        # Log first 2 records of batch 1 for debugging
        if batch_number == 1:
            print(f"[{label} Batch 1] First 2 records of payload:")
            sample_payload = {"result": [_sample(r) for r in batch[:2]]}
            sample_str = json.dumps(sample_payload, indent=2)
            print(sample_str[:3000])
            if len(sample_str) > 3000:
                print(f"[{label} Batch 1] (truncated to first 3000 chars)")

        success = False
        last_error = None
        last_response_text = None

        for attempt in range(1, max_retries + 1):
            try:
                print(
                    f"[{label} Batch {batch_number}] Attempt "
                    f"{attempt}/{max_retries}: POST "
                    f"(payload {len(payload)} bytes)"
                )

                response = requests.post(
                    url,
                    headers=headers,
                    data=payload,
                    timeout=(10, 120),
                )

                print(
                    f"[{label} Batch {batch_number}] "
                    f"Status Code: {response.status_code}"
                )

                response_preview = response.text[:1000]
                print(f"[{label} Batch {batch_number}] Response: {response_preview}")
                if len(response.text) > 1000:
                    print(
                        f"[{label} Batch {batch_number}] "
                        f"(Response truncated; full size: {len(response.text)} bytes)"
                    )

                last_response_text = response.text

                if response.status_code in (200, 201):
                    success = True
                    print(f"[{label} Batch {batch_number}] ✅ Success")
                    break
                else:
                    last_error = (
                        f"HTTP {response.status_code} - {response.text[:300]}"
                    )
                    print(
                        f"[{label} Batch {batch_number}] ❌ Attempt "
                        f"{attempt}: {last_error}"
                    )

            except requests.RequestException as e:
                last_error = f"Request exception: {e}"
                print(
                    f"[{label} Batch {batch_number}] ❌ Attempt "
                    f"{attempt}: {last_error}"
                )

            if attempt < max_retries:
                print(
                    f"[{label} Batch {batch_number}] Sleeping {retry_delay}s "
                    f"before retry..."
                )
                time.sleep(retry_delay)

        if not success:
            print(
                f"[{label} Batch {batch_number}] ❌ FAILED after "
                f"{max_retries} attempts. Last error: {last_error}"
            )
            print(
                f"[{label} Batch {batch_number}] Failed-batch sample record:"
            )
            print(json.dumps(_sample(batch[0]), indent=2))

            failed_batches.append({
                "label":          label,
                "batch_number":   batch_number,
                "batch_size":     len(batch),
                "last_error":     last_error,
                "last_response":  (last_response_text[:500]
                                   if last_response_text else None),
                "sample_id":      batch[0].get(sample_id_field, "unknown"),
            })

    print(f"\n--- {label}: summary ---")
    print(f"  Total batches: {total_batches}")
    print(f"  Successful:    {total_batches - len(failed_batches)}")
    print(f"  Failed:        {len(failed_batches)}")

    return failed_batches


# ==============================================================
# PROCESS MATCH/POTENTIAL BATCHES (FATAL on failure)
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
    """
    Send Match/Potential records to ServiceNow.

    RAISES on any batch failure — these records are the primary
    deliverable of the matching job and partial delivery would
    corrupt downstream state.
    """
    failed_batches = _post_batches(
        data,
        batch_size,
        url,
        max_retries,
        retry_delay,
        access_token,
        label="Match",
        sample_id_field="u_gcp_id",
        redact_samples=True,
    )

    if failed_batches:
        print("Failed Match batches detail:")
        for fb in failed_batches:
            print(
                f"  - Batch {fb['batch_number']} "
                f"(size {fb['batch_size']}, sample pos_id {fb['sample_id']}): "
                f"{fb['last_error']}"
            )
        raise RuntimeError(
            f"ServiceNow update failed: {len(failed_batches)} "
            f"Match batches did not succeed after {max_retries} attempts each."
        )

    print("All Match/Potential batches succeeded ✅")


# ==============================================================
# PROCESS CLOSED-EXISTING BATCHES (NON-FATAL on failure)
# ==============================================================
def process_ce_batches(
    data,
    batch_size,
    url,
    max_retries,
    retry_delay,
    access_token,
):
    """
    Send Closed-Existing lead status updates to ServiceNow.

    Failures here are LOGGED but do NOT raise. Match/Potential records
    are already in ServiceNow by the time this runs, and failed CE
    leads will simply remain in their current status — the next match
    run will re-emit them and re-attempt the update.
    """
    failed_batches = _post_batches(
        data,
        batch_size,
        url,
        max_retries,
        retry_delay,
        access_token,
        label="CE",
        sample_id_field="number",
        redact_samples=False,   # CE payload has no PII
    )

    if failed_batches:
        print("\n" + "!" * 60)
        print(
            f"WARNING: {len(failed_batches)} Closed-Existing batch(es) failed. "
            f"Match/Potential data is already in ServiceNow; failed CE leads "
            f"will retry on next run."
        )
        for fb in failed_batches:
            print(
                f"  - CE Batch {fb['batch_number']} "
                f"(size {fb['batch_size']}, sample lead_id {fb['sample_id']}): "
                f"{fb['last_error']}"
            )
        print("!" * 60)
    else:
        print("All Closed-Existing batches succeeded ✅")


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
        file_path = os.environ.get("FINAL_OUTPUT_PATH", "")

    if file_path == "":
        match_id = os.environ.get("MATCH_ID", "").strip()
        warehouse = os.environ.get("WAREHOUSE", "").strip()
        if match_id:
            candidate_path = build_match_output_uri(
                storage_config,
                match_id=match_id,
                warehouse=warehouse,
            )
            if gcs_blob_exists(candidate_path):
                file_path = candidate_path
                print(f"Resolved exact match output for MATCH_ID {match_id}: {file_path}")

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path, os.environ.get("MATCH_ID", ""))

    # ── ServiceNow config ──
    BATCH_SIZE                  = servicenow_config.insert_batch_size
    url                         = servicenow_config.match_result_update_url
    update_closed_existing_url  = servicenow_config.update_closed_existing_url
    MAX_RETRIES                 = servicenow_config.max_retries
    RETRY_DELAY                 = servicenow_config.retry_delay
    token_url                   = servicenow_config.token_url
    client_id                   = servicenow_config.snow_client_id
    client_secret               = servicenow_config.snow_client_secret

    # ── Get access token ──
    access_token = get_oauth_token(token_url, client_id, client_secret)
    print("Successfully obtained OAuth token")

    # ── Load final output from GCS ──
    final_df = load_file_from_gcs(file_path)

    print(f"\n=== Loaded dataframe from GCS ===")
    print(f"File:        {file_path}")
    print(f"Total rows:  {len(final_df)}")
    print(f"Columns:     {list(final_df.columns)}")

    if not final_df.empty:
        print(f"Sample row (first, redacted):")
        sample = final_df.iloc[0].to_dict()
        sample = {k: (str(v) if v is not None else None) for k, v in sample.items()}
        for pii_col in ("email", "phone", "first_name", "last_name",
                        "address_line_one", "address_line_two",
                        "zip_code", "membership_number"):
            if pii_col in sample and sample[pii_col]:
                v = sample[pii_col]
                sample[pii_col] = (
                    v[:2] + "*" * max(len(v) - 2, 0) if len(v) > 2 else "***"
                )
        print(json.dumps(sample, indent=2, default=str))

    if 'match_result' in final_df.columns:
        print(
            f"Match result distribution: "
            f"{final_df['match_result'].value_counts(dropna=False).to_dict()}"
        )
    if 'closed_existing_flag' in final_df.columns:
        ce_count = final_df['closed_existing_flag'].fillna(False).sum()
        print(f"Closed-Existing leads: {int(ce_count)}")

    # ──────────────────────────────────────────────────────────
    # SPLIT: Match/Potential rows vs Closed-Existing stub rows
    # CE stubs have null match_result + closed_existing_flag=True,
    # so the two slices are disjoint by construction.
    # ──────────────────────────────────────────────────────────
    if 'closed_existing_flag' in final_df.columns:
        is_ce = final_df['closed_existing_flag'].fillna(False).astype(bool)
    else:
        is_ce = pd.Series(False, index=final_df.index)

    match_df = final_df[
        ~is_ce & final_df['match_result'].isin(['Match', 'Potential'])
    ]
    ce_df = final_df[is_ce]

    print(f"\nAfter split:")
    print(f"  Match/Potential rows: {len(match_df)}")
    print(f"  Closed-Existing rows: {len(ce_df)}")

    # ──────────────────────────────────────────────────────────
    # CALL 1: Match/Potential (FATAL on failure)
    # ──────────────────────────────────────────────────────────
    if match_df.empty:
        print("No Match/Potential records to send. Skipping Match update.")
    else:
        total_matched, json_data = generate_post_json(match_df)
        process_batches(
            total_matched,
            json_data,
            BATCH_SIZE,
            url,
            MAX_RETRIES,
            RETRY_DELAY,
            access_token,
        )

    # ──────────────────────────────────────────────────────────
    # CALL 2: Closed-Existing (NON-FATAL on failure)
    # Runs only after Match/Potential succeeds, so we can't lose
    # already-delivered Match data to a CE-side error.
    # ──────────────────────────────────────────────────────────
    if ce_df.empty:
        print("No Closed-Existing leads to send. Skipping CE update.")
    else:
        ce_json = generate_ce_json(ce_df)
        process_ce_batches(
            ce_json,
            BATCH_SIZE,
            update_closed_existing_url,
            MAX_RETRIES,
            RETRY_DELAY,
            access_token,
        )

    print("\nServiceNow update completed")
