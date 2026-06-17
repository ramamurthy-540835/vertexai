"""
Stream-based POS/leads data preprocessing.

Reads Cloud SQL in chunks, applies normalization, streams each chunk to
GCS as CSV. Memory stays bounded regardless of row count. Produces a
single CSV file per type (pos_temp.csv or leads_temp.csv).

Key principle: ORIGINAL columns are preserved untouched for the
ServiceNow payload. NORMALIZED versions (_normalized suffix) are added
for matching only. Passthrough fields (sales_reference_id, bd_industry,
industry_description, shop_type) are stripped only — never lowercased
or transformed.

OMS fields (POS only) are extra variants of company / email / phone /
address used by lead_matching's family-based scoring. Each OMS field
gets the same original + normalized treatment as its primary cousin.

Memory design
─────────────
• normalize_series() stays entirely in pandas (no Python list loop) so
  pandas/numpy manage memory without building intermediate Python lists.
• clean_required_columns() writes normalized columns directly onto the
  working frame — the frame is never duplicated.
• validate_combined_field() builds intermediate Series in a tight scope
  and explicitly frees them before returning.
• CHUNK_SIZE = 50K — fewer iterations, lower per-row Python overhead,
  fewer GCS write calls. Peak per-chunk memory stays well under 1 GB.
• GCS writer uses a fixed 5 MB resumable-upload chunk so the upload
  buffer never accumulates in memory across many to_csv() calls.
• An explicit gc.collect() runs after each chunk is written and deleted.
"""

import gc
import logging

import pandas as pd
from google.cloud import storage
from sqlalchemy import text as sql_text
from unidecode import unidecode

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info

log = logging.getLogger(__name__)


# Rows pulled from Cloud SQL per fetch. 50K balances per-row Python
# overhead (favours larger chunks) against peak memory (favours smaller).
# At 50K, peak per-chunk memory for POS-with-OMS stays under ~500 MB.
CHUNK_SIZE = 50_000

# GCS resumable-upload chunk size. Forces the writer to flush to the
# network every 5 MB instead of buffering all to_csv() output until the
# `with` block closes. Must be a multiple of 256 KB.
GCS_UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024


# Fields that need normalization (lowercase + unidecode + strip special).
# Originals are preserved; a `<col>_normalized` column is added.
FIELDS_TO_NORMALIZE = [
    'business_name',
    'first_name', 'last_name',
    'address_line_one', 'address_line_two',
    'city', 'zip_code', 'email',
    'state',
    'phone',
]

# Fields that pass through untouched (only whitespace-stripped).
PASSTHROUGH_FIELDS = [
    'sales_reference_id',
    'bd_industry',
    'industry_description',
    'shop_type',
]


# ──────────────────────────────────────────────────────────────────────
# OMS FIELDS — POS ONLY
# ──────────────────────────────────────────────────────────────────────
# These are the OMS variants used by lead_matching's family-based
# scoring. Each gets the same original + _normalized treatment as its
# primary cousin. Lead-side data does NOT carry OMS fields.
#
# Keep this list in sync with KEY_FAMILIES / ADDRESS_BUNDLES in
# costco/leadmgmt/components/lead_matching.py — adding/removing an
# OMS variant there means updating it here too.

OMS_TEXT_FIELDS = [
    # Company variants (business_name family)
    'oms_company', 'oms_company_2',

    # Email variants (email family)
    'oms_email_1', 'oms_email_2', 'oms_email_3',

    # Phone / cell variants (phone family)
    'oms_phone_1', 'oms_phone_2', 'oms_phone_3',
    'oms_cell_1', 'oms_cell_2',

    # Address bundles 2 and 3 — line + city + state
    # (zips have their own truncate rule, see OMS_ZIP_FIELDS below)
    'oms_address_line_1',    'oms_city',   'oms_state',
    'oms_address_line_1_v2', 'oms_city_2', 'oms_state_2',
]

# OMS zip fields use 5-char truncate + lowercase (same as primary zip_code)
OMS_ZIP_FIELDS = ['oms_zip', 'oms_zip_2']

# Combined: every OMS column we expect on the POS row
ALL_OMS_FIELDS = OMS_TEXT_FIELDS + OMS_ZIP_FIELDS


# ──────────────────────────────────────────────────────────────────────
# Normalization helpers — fully vectorised, no Python loops
# ──────────────────────────────────────────────────────────────────────

def _unidecode_series(s: pd.Series) -> pd.Series:
    """
    Vectorised unidecode: transliterate non-ASCII characters.
    Operates on an already-stringified, null-filled series so unidecode
    never receives None. Empty strings are passed through unchanged
    (skips the unidecode call for the common empty-field case).
    """
    return s.apply(lambda x: unidecode(x) if x else x)


def normalize_series(s: pd.Series) -> pd.Series:
    """
    Normalize a string column for matching — fully vectorised.

    Pipeline:
      1. fillna('') + astype(str)      — null safety
      2. _unidecode_series()           — transliterate non-ASCII
      3. str.replace non-alphanumeric  — strip special chars (& @ . - ')
      4. str.replace whitespace runs   — collapse to single space
      5. str.strip + str.lower         — tidy + lowercase

    No Python list is constructed; every step stays in pandas/numpy
    memory management so peak usage is ~1× the series size (vs ~2× for
    a list-comprehension approach that materialises a Python list and
    then rebuilds a Series).
    """
    return (
        s.fillna('').astype(str)
         .pipe(_unidecode_series)
         .str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)
         .str.replace(r'\s+', ' ', regex=True)
         .str.strip()
         .str.lower()
    )


def normalize_zip_series(s: pd.Series) -> pd.Series:
    """Zip normalization: strip + truncate to 5 chars + lowercase."""
    return s.fillna('').astype(str).str.strip().str[:5].str.lower()


# ──────────────────────────────────────────────────────────────────────
# Derived fields
# ──────────────────────────────────────────────────────────────────────

def validate_combined_field(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build COMBINED_FIELD, FULL_ADDRESS, CUSTOMER_NAME from the
    *_normalized columns (matching artifacts, not display fields).
    Originals are not touched. OMS variants are NOT used here —
    COMBINED_FIELD remains the primary-side composite for backward
    compatibility with anything that consumes it.

    Intermediate Series are explicitly freed before returning so peak
    memory during this stage stays low.
    """
    address_fields = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code']

    def _get(col):
        norm_col = f"{col}_normalized"
        if norm_col in df.columns:
            return df[norm_col].fillna('').astype(str).str.strip()
        if col in df.columns:
            return normalize_series(df[col]).str.strip()
        return pd.Series('', index=df.index, dtype=str)

    # FULL_ADDRESS: address parts joined by ^, only between non-empty parts
    addr_parts   = [_get(c) for c in address_fields]
    full_address = addr_parts[0].copy()
    for part in addr_parts[1:]:
        sep = (full_address != '') & (part != '')
        full_address = full_address + sep.map({True: '^', False: ''}) + part
    full_address = full_address.str.strip('^')
    del addr_parts

    # CUSTOMER_NAME: first^last (^ only if both non-empty)
    fn, ln        = _get('first_name'), _get('last_name')
    sep           = (fn != '') & (ln != '')
    customer_name = fn + sep.map({True: '^', False: ''}) + ln
    del fn, ln, sep

    # COMBINED_FIELD: business^address^phone^customer_name^email
    df['COMBINED_FIELD'] = (
        _get('business_name') + '^' +
        full_address          + '^' +
        _get('phone')         + '^' +
        customer_name         + '^' +
        _get('email')
    ).str.strip()

    df['FULL_ADDRESS']  = full_address
    df['CUSTOMER_NAME'] = customer_name

    del full_address, customer_name
    return df


# ──────────────────────────────────────────────────────────────────────
# Column enforcement
# ──────────────────────────────────────────────────────────────────────

def enforce_required_columns(df: pd.DataFrame, required_columns) -> pd.DataFrame:
    """
    Ensure all required columns exist. Missing columns — most commonly
    OMS fields that are NULL for all rows in older data — are added as
    empty strings. The matching code handles empty OMS values gracefully.
    """
    for col in required_columns:
        if col not in df.columns:
            df[col] = ''
            log.warning("Column '%s' missing from SQL result; backfilled with ''", col)
    return df


# ──────────────────────────────────────────────────────────────────────
# Normalization pipeline — tight column lifecycle
# ──────────────────────────────────────────────────────────────────────

def clean_required_columns(df: pd.DataFrame, base_name: str) -> pd.DataFrame:
    """
    Add *_normalized columns for matching. Originals are PRESERVED
    untouched. Passthrough fields are only whitespace-stripped.

    Memory discipline: normalized columns are written directly onto df
    (no intermediate DataFrame copies). The working frame is never
    duplicated.

    POS base_name additionally normalizes OMS variant columns.
    """
    # ── Primary normalized versions (for matching) ──
    for col in FIELDS_TO_NORMALIZE:
        if col not in df.columns:
            continue
        if col == 'zip_code':
            df['zip_code_normalized'] = normalize_zip_series(df['zip_code'])
        else:
            df[f"{col}_normalized"] = normalize_series(df[col])

    # ── OMS normalized versions (POS only) ──
    if base_name == 'pos':
        for col in OMS_TEXT_FIELDS:
            if col in df.columns:
                df[f"{col}_normalized"] = normalize_series(df[col])
        for col in OMS_ZIP_FIELDS:
            if col in df.columns:
                df[f"{col}_normalized"] = normalize_zip_series(df[col])

    # ── Passthrough fields: only strip whitespace ──
    for col in PASSTHROUGH_FIELDS:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    # ── Primary originals: ensure string + NaN → '' (no case/char change) ──
    for col in FIELDS_TO_NORMALIZE:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    # ── OMS originals (POS only): same treatment as primary originals ──
    if base_name == 'pos':
        for col in ALL_OMS_FIELDS:
            if col in df.columns:
                df[col] = df[col].fillna('').astype(str).str.strip()

    return df


# ──────────────────────────────────────────────────────────────────────
# Column schema
# ──────────────────────────────────────────────────────────────────────

def _get_required_columns(base_name: str, stage: str):
    """
    Required column lists for enforce and clean stages.
    `clean` stage returns the FULL set of columns that should exist in
    the output CSV: originals + normalized + derived + passthrough.

    POS clean output includes OMS originals and OMS *_normalized cols.
    """
    if base_name == 'pos':
        if stage == "enforce":
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'phone', 'email',
                'shop_type', 'order_amount', 'bd_industry', 'sales_reference_id',
                'industry_description', 'account_number',
                'fiscal_year_transaction', 'fiscal_period_transaction', 'week',
                'updated_date', 'pos_id',
                # OMS variants
                *ALL_OMS_FIELDS,
            ]
        else:  # clean — full output column set
            return [
                # Identifiers / IDs (passthrough)
                'pos_id', 'account_number', 'membership_number', 'warehouse_number',

                # Originals — preserved for ServiceNow payload
                'business_name', 'first_name', 'last_name',
                'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'email', 'phone',

                # Primary normalized versions — for matching
                'business_name_normalized', 'first_name_normalized', 'last_name_normalized',
                'address_line_one_normalized', 'address_line_two_normalized',
                'city_normalized', 'state_normalized',
                'zip_code_normalized', 'email_normalized', 'phone_normalized',

                # Derived matching fields
                'COMBINED_FIELD', 'FULL_ADDRESS', 'CUSTOMER_NAME',

                # OMS originals (POS only) — kept alongside primaries
                'oms_company', 'oms_company_2',
                'oms_email_1', 'oms_email_2', 'oms_email_3',
                'oms_phone_1', 'oms_phone_2', 'oms_phone_3',
                'oms_cell_1', 'oms_cell_2',
                'oms_address_line_1',    'oms_zip',   'oms_city',   'oms_state',
                'oms_address_line_1_v2', 'oms_zip_2', 'oms_city_2', 'oms_state_2',

                # OMS normalized — for family-based matching
                'oms_company_normalized', 'oms_company_2_normalized',
                'oms_email_1_normalized', 'oms_email_2_normalized', 'oms_email_3_normalized',
                'oms_phone_1_normalized', 'oms_phone_2_normalized', 'oms_phone_3_normalized',
                'oms_cell_1_normalized',  'oms_cell_2_normalized',
                'oms_address_line_1_normalized',    'oms_zip_normalized',
                'oms_city_normalized',              'oms_state_normalized',
                'oms_address_line_1_v2_normalized', 'oms_zip_2_normalized',
                'oms_city_2_normalized',            'oms_state_2_normalized',

                # Passthrough — untransformed, only stripped
                'shop_type', 'sales_reference_id', 'bd_industry', 'industry_description',
                'order_amount',

                # Transaction metadata
                'fiscal_year_transaction', 'fiscal_period_transaction', 'week',
                'updated_date',
            ]
    else:  # leads — unchanged, no OMS columns on lead side
        if stage == "enforce":
            return [
                'warehouse_number', 'membership_number', 'business_name',
                'first_name', 'last_name', 'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'phone', 'email',
                'lead_id', 'updated_date',
                'fiscal_year_lead', 'fiscal_period_lead','week'
            ]
        else:  # clean
            return [
                # Identifiers
                'lead_id', 'membership_number', 'warehouse_number',

                # Originals — preserved
                'business_name', 'first_name', 'last_name',
                'address_line_one', 'address_line_two',
                'city', 'state', 'zip_code', 'email', 'phone',

                # Normalized — for matching
                'business_name_normalized', 'first_name_normalized', 'last_name_normalized',
                'address_line_one_normalized', 'address_line_two_normalized',
                'city_normalized', 'state_normalized',
                'zip_code_normalized', 'email_normalized', 'phone_normalized',

                # Derived
                'COMBINED_FIELD', 'FULL_ADDRESS', 'CUSTOMER_NAME',

                # Lead metadata
                'updated_date', 'fiscal_year_lead', 'fiscal_period_lead','week'
            ]


# ──────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────

def load_and_preprocess_data_cloud_sql(base_name: str, config_file_path: str) -> str:
    """
    Stream POS or leads data from Cloud SQL through pandas preprocessing
    to a single GCS CSV. Output preserves original column values and
    adds *_normalized columns for downstream matching.

    For POS data, OMS variant columns (oms_company, oms_email_1, etc.)
    are also preserved and normalized so the family-based matching in
    lead_matching.py can score against them.

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
    job_config     = JobConfig(config_file_path)
    db_config      = job_config.db_config
    query_config   = job_config.match_query
    storage_config = job_config.storage_config

    engine         = db_config.get_engine()
    storage_client = storage.Client()
    fiscal_info    = get_costco_fiscal_info()

    # ── Build query ──
    if base_name == "pos":
        query_input              = f'''{query_config.query_pos} = {fiscal_info["fiscal_year"]}'''
        source_folder_input      = storage_config.source_folder_input_pos
        destination_folder_input = storage_config.destination_folder_input_pos
    else:  # leads
        query_input              = f'''{query_config.query_leads} >= {fiscal_info["fiscal_year"] - 1}'''
        source_folder_input      = storage_config.source_folder_input_leads
        destination_folder_input = storage_config.destination_folder_input_leads

    # ── Setup output GCS blob ──
    output_bucket           = storage_config.output_bucket_name
    preprocessed_folder     = storage_config.temporary_folder
    source_bucket_name      = storage_config.source_bucket_name
    destination_bucket_name = storage_config.destination_bucket_name

    new_file_name = f"{base_name}_temp.csv"
    output_file   = f"{preprocessed_folder}/{new_file_name}"
    bucket        = storage_client.get_bucket(output_bucket)
    output_blob   = bucket.blob(output_file)

    # Force the resumable upload to flush every 5 MB so the writer buffer
    # never grows unbounded across many to_csv() calls.
    output_blob.chunk_size = GCS_UPLOAD_CHUNK_SIZE

    # ── Required-column lists ──
    enforce_cols = _get_required_columns(base_name, "enforce")
    output_cols  = _get_required_columns(base_name, "clean")

    # ── Archive source files ──
    archive_uri = process_and_archive_files(
        source_bucket_name,
        source_folder_input,
        destination_bucket_name,
        destination_folder_input,
        None,
        base_name,
    )
    log.info("Archived source files: %s", archive_uri)

    # ── Stream Cloud SQL → pandas chunks → GCS CSV ──
    log.info("Reading %s data in chunks of %d rows", base_name, CHUNK_SIZE)

    total_rows  = 0
    chunk_count = 0

    # Use a SERVER-SIDE (streaming) cursor. Without stream_results=True,
    # pg8000 buffers the ENTIRE result set before pandas sees row one —
    # at 25M rows that is a hard OOM before the first chunk is processed.
    #
    # stream_results=True makes Postgres hand rows over incrementally so
    # the first chunk materializes almost immediately. yield_per caps how
    # many raw rows the driver holds in its own buffer at once (belt-and-
    # suspenders with chunksize, important at 25M-row scale).
    streaming_conn = engine.connect().execution_options(
        stream_results=True,
        yield_per=CHUNK_SIZE,
    )
    try:

        with output_blob.open("w") as gcs_writer:

            for chunk_df in pd.read_sql(
                query_input, streaming_conn, chunksize=CHUNK_SIZE
            ):
                chunk_count += 1
                chunk_rows   = len(chunk_df)

                # ── Null safety first ──
                chunk_df = chunk_df.fillna("")

                # ── Ensure all expected columns exist ──
                chunk_df = enforce_required_columns(chunk_df, enforce_cols)

                # ── Add *_normalized columns (in-place on chunk_df) ──
                chunk_df = clean_required_columns(chunk_df, base_name)

                # ── Add derived composite columns ──
                chunk_df = validate_combined_field(chunk_df)

                # ── Trim to output schema and write ──
                cols_to_write = [c for c in output_cols if c in chunk_df.columns]
                chunk_df      = chunk_df[cols_to_write]

                chunk_df.to_csv(
                    gcs_writer,
                    index=False,
                    header=(chunk_count == 1),
                )

                total_rows += chunk_rows
                log.info(
                    "Chunk %d: %d rows (running total: %d)",
                    chunk_count, chunk_rows, total_rows,
                )

                # ── Explicitly free chunk memory before fetching the next ──
                del chunk_df
                gc.collect()
    finally:
        streaming_conn.close()

    output_uri = f"gs://{bucket.name}/{output_file}"
    log.info("Wrote %d rows to %s in %d chunks", total_rows, output_uri, chunk_count)

    return output_uri