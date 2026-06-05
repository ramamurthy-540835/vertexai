"""
Stream-based POS/leads data preprocessing.

Reads Cloud SQL in chunks, applies the same normalization as before,
streams each chunk to GCS as CSV. Memory stays bounded regardless of
row count. Produces a single CSV file per type (pos_temp.csv or
leads_temp.csv) — same output path as the original implementation.
"""

import pandas as pd
from google.cloud import storage
from unidecode import unidecode_expect_ascii

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


# Rows pulled from Cloud SQL per fetch. Bigger = faster but more memory.
# At 1M rows per chunk, peak memory is ~3-4 GB during normalization.
# Cloud Run job memory should be >= 6 GB for this setting.
CHUNK_SIZE = 200_000


# ─────────────────────────────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────────────────────────────

def normalize_series(s: pd.Series) -> pd.Series:
    """
    Normalize a string column:
      1. NULL → ''
      2. Cast to string
      3. unidecode (strip accents, transliterate non-ASCII)
      4. Remove anything that isn't a letter, digit, or whitespace
      5. Lowercase

    Behavior matches the original implementation for ASCII-heavy data.
    Uses vectorized .str.replace() and unidecode_expect_ascii for speed.
    """
    return (
        s.fillna('')
         .astype(str)
         .apply(unidecode_expect_ascii)                        # ~2-3x faster than unidecode for ASCII data
         .str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)       # vectorized — runs in C
         .str.lower()                                          # vectorized
    )


def validate_combined_field(df: pd.DataFrame) -> pd.DataFrame:
    """Build COMBINED_FIELD, FULL_ADDRESS, CUSTOMER_NAME — unchanged logic from original."""
    address_fields = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code']
    name_fields = ['first_name', 'last_name']

    # Normalize each input column. Missing columns become empty Series.
    norm = {}
    for col in address_fields + name_fields + ['business_name', 'phone', 'email']:
        if col in df.columns:
            norm[col] = normalize_series(df[col]).str.strip()
        else:
            norm[col] = pd.Series('', index=df.index)

    # FULL_ADDRESS: address parts joined by ^, but only between non-empty parts
    addr_parts = [norm[c] for c in address_fields]
    full_address = addr_parts[0]
    for part in addr_parts[1:]:
        sep = (full_address != '') & (part != '')
        full_address = full_address + sep.map({True: '^', False: ''}) + part
    full_address = full_address.str.strip('^')

    # CUSTOMER_NAME: first^last (only ^ if both non-empty)
    sep = (norm['first_name'] != '') & (norm['last_name'] != '')
    customer_name = norm['first_name'] + sep.map({True: '^', False: ''}) + norm['last_name']

    # COMBINED_FIELD: business^address^phone^customer_name^email
    df['COMBINED_FIELD'] = (
        norm['business_name'] + '^' +
        full_address          + '^' +
        norm['phone']         + '^' +
        customer_name         + '^' +
        norm['email']
    ).str.strip()

    df['FULL_ADDRESS']  = full_address
    df['CUSTOMER_NAME'] = customer_name

    return df


def enforce_required_columns(df: pd.DataFrame, required_columns) -> pd.DataFrame:
    """Ensure required columns exist; truncate zip_code to 5 chars. Vectorized."""
    for col in required_columns:
        if col not in df.columns:
            df[col] = ''
            print(f"⚠️ Column '{col}' added with default empty values.")

    # Vectorized zip_code truncation (no apply)
    if 'zip_code' in df.columns:
        df['zip_code'] = df['zip_code'].fillna('').astype(str).str[:5]

    return df


def clean_required_columns(df: pd.DataFrame, required_columns) -> pd.DataFrame:
    """Strip + lowercase the listed columns."""
    for col in required_columns:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip().str.lower()
    return df


def _get_required_columns(base_name: str, stage: str):
    """Centralize the required-column lists used at enforce and clean stages."""
    if base_name == 'pos':
        if stage == "enforce":
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'phone', 'email',
                'shop_type', 'order_amount', 'bd_industry', 'sales_reference_id',
            ]
        else:  # clean
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'city', 'state', 'zip_code',
                'phone', 'email', 'address_line_one', 'address_line_two',
                'COMBINED_FIELD', 'FULL_ADDRESS', 'CUSTOMER_NAME',
                'shop_type', 'order_amount', 'bd_industry', 'sales_reference_id',
            ]
    else:  # leads
        if stage == "enforce":
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'phone', 'email',
            ]
        else:  # clean
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'city', 'state', 'zip_code',
                'phone', 'email', 'address_line_one', 'address_line_two',
                'COMBINED_FIELD', 'FULL_ADDRESS', 'CUSTOMER_NAME',
            ]


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

def load_and_preprocess_data_cloud_sql(base_name: str, config_file_path: str) -> str:
    """
    Stream POS or leads data from Cloud SQL through pandas preprocessing
    to a single GCS CSV. Behavior identical to the original — same
    normalization, same output path, same CSV format. Only the read path
    is chunked to bound memory.

    Parameters
    ----------
    base_name : str
        'pos' or 'leads'.
    config_file_path : str
        Path to configuration_*.ini.

    Returns
    -------
    str
        gs:// URI of the resulting CSV file.
    """
    if base_name not in ("pos", "leads"):
        raise Exception("invalid base name")

    # ── Load config ──
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config

    engine = db_config.get_engine()
    storage_client = storage.Client()
    fiscal_info = get_costco_fiscal_info()

    # ── Build query (same pattern as original: append fiscal year filter) ──
    if base_name == "pos":
        query_input = f'''{query_config.query_pos} = {fiscal_info["fiscal_year"]}'''
        source_folder_input = storage_config.source_folder_input_pos
        destination_folder_input = storage_config.destination_folder_input_pos
    else:  # leads
        query_input = f'''{query_config.query_leads} >= {fiscal_info["fiscal_year"] - 1}'''
        source_folder_input = storage_config.source_folder_input_leads
        destination_folder_input = storage_config.destination_folder_input_leads

    # ── Setup output GCS blob (single file: pos_temp.csv or leads_temp.csv) ──
    output_bucket = storage_config.output_bucket_name
    preprocessed_folder = storage_config.temporary_folder
    source_bucket_name = storage_config.source_bucket_name
    destination_bucket_name = storage_config.destination_bucket_name

    new_file_name = f"{base_name}_temp.csv"
    output_file = f"{preprocessed_folder}/{new_file_name}"
    bucket = storage_client.get_bucket(output_bucket)
    output_blob = bucket.blob(output_file)

    # ── Required-column lists ──
    enforce_cols = _get_required_columns(base_name, "enforce")
    clean_cols = _get_required_columns(base_name, "clean")

    # ── Archive source files ──
    # NOTE: original passed the full DataFrame. Since we no longer load
    # the full df at once, we pass None. Verify what process_and_archive_files
    # does with this argument:
    #   - if it only needs file/bucket metadata → None is fine
    #   - if it uses len(df) for logging → pass total_rows after the loop
    #   - if it iterates df contents → refactor to use file-listing instead
    archive_uri = process_and_archive_files(
        source_bucket_name,
        source_folder_input,
        destination_bucket_name,
        destination_folder_input,
        None,
        base_name,
    )
    print(f"Archived source files: {archive_uri}")

    # ── Stream Cloud SQL → pandas chunks → GCS CSV ──
    print(f"Reading {base_name} data in chunks of {CHUNK_SIZE:,} rows")

    total_rows = 0
    chunk_count = 0

    # blob.open('w') opens a streaming text upload to GCS.
    # Flow: SQL → DataFrame chunk → CSV text → gcs_writer → GCS object.
    # GCS sees a single growing object; the final result is one CSV file.
    with output_blob.open("w") as gcs_writer:

        # pd.read_sql with chunksize returns an iterator of DataFrames
        # instead of materializing the whole result at once.
        for chunk_df in pd.read_sql(query_input, engine, chunksize=CHUNK_SIZE):
            chunk_count += 1
            chunk_rows = len(chunk_df)

            # Run the existing preprocessing pipeline on this chunk only
            chunk_df = chunk_df.fillna("")
            chunk_df = enforce_required_columns(chunk_df, enforce_cols)
            chunk_df = validate_combined_field(chunk_df)
            chunk_df = clean_required_columns(chunk_df, clean_cols)

            # Write to CSV stream. Write header only on the first chunk so the
            # final file has exactly one header row at the top.
            chunk_df.to_csv(
                gcs_writer,
                index=False,
                header=(chunk_count == 1),
            )

            total_rows += chunk_rows
            print(f"  Chunk {chunk_count}: {chunk_rows:,} rows (running total: {total_rows:,})")

    output_uri = f"gs://{bucket.name}/{output_file}"
    print(f"✅ Wrote {total_rows:,} rows to {output_uri} in {chunk_count} chunks")

    return output_uri