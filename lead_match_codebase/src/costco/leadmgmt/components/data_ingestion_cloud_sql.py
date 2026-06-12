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
"""

import pandas as pd
from google.cloud import storage
from unidecode import unidecode
import gc

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


# Rows pulled from Cloud SQL per fetch. Bigger = faster but more memory.
CHUNK_SIZE = 5_000


# Fields that need normalization (lowercase + unidecode + strip non-alphanumeric).
# Originals are preserved; a `<col>_normalized` column is added for matching.
FIELDS_TO_NORMALIZE = [
    'business_name',
    'first_name', 'last_name',
    'address_line_one', 'address_line_two',
    'city', 'zip_code', 'email',
    'state',
    'phone',
]

# Fields that pass through untouched (only whitespace-stripped).
# These go directly into the ServiceNow payload as-is.
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

# OMS string fields normalized with full normalize_series()
# (unidecode + strip non-alphanumeric + lowercase).
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
# Normalization helpers
# ──────────────────────────────────────────────────────────────────────

def normalize_series(s: pd.Series) -> pd.Series:
    """
    Normalize a string column for matching:
      1. NULL → ''
      2. Cast to string
      3. unidecode (strip accents, transliterate non-ASCII)
      4. Remove anything that isn't a letter, digit, or whitespace
      5. Collapse multiple whitespace to single space
      6. Strip leading/trailing whitespace
      7. Lowercase
    Special characters (&, @, ., -, ', etc.) are REMOVED.
    """
    decoded = [unidecode(str(x)) if x else '' for x in s.fillna('')]
    return (
        pd.Series(decoded, index=s.index)
          .str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)
          .str.replace(r'\s+', ' ', regex=True)
          .str.strip()
          .str.lower()
    )


def normalize_zip_series(s: pd.Series) -> pd.Series:
    """Zip normalization: strip + truncate to 5 chars + lowercase."""
    return s.fillna('').astype(str).str.strip().str[:5].str.lower()


def validate_combined_field(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build COMBINED_FIELD, FULL_ADDRESS, CUSTOMER_NAME from the
    *_normalized columns (matching artifacts, not display fields).
    Originals are not touched. OMS variants are NOT used here —
    COMBINED_FIELD remains the primary-side composite for backward
    compatibility with anything that consumes it.
    """
    address_fields = ['address_line_one', 'address_line_two', 'city', 'state', 'zip_code']
    name_fields = ['first_name', 'last_name']
    other_fields = ['business_name', 'phone', 'email']

    # Pull from *_normalized columns (created by clean_required_columns)
    norm = {}
    for col in address_fields + name_fields + other_fields:
        norm_col = f"{col}_normalized"
        if norm_col in df.columns:
            norm[col] = df[norm_col].fillna('').astype(str).str.strip()
        elif col in df.columns:
            # Fallback: normalize on the fly from original
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
    """
    Ensure required columns exist with default empty values.
    Missing OMS columns on older data are filled with '' and a warning
    is printed (matching code handles empty OMS values gracefully).
    """
    for col in required_columns:
        if col not in df.columns:
            df[col] = ''
            print(f"⚠️ Column '{col}' added with default empty values.")

    # NOTE: zip_code (and OMS zips) originals are left intact. The
    # 5-char truncation is applied later in clean_required_columns as
    # zip_code_normalized / oms_zip_normalized / oms_zip_2_normalized.
    return df


def clean_required_columns(df: pd.DataFrame, base_name: str) -> pd.DataFrame:
    """
    Create *_normalized columns for matching fields. Originals are
    PRESERVED untouched. Passthrough fields are only whitespace-stripped
    (no case change, no character stripping).

    POS base_name additionally normalizes OMS variant columns.
    """
    # ── Primary normalized versions (for matching) ──
    for col in FIELDS_TO_NORMALIZE:
        if col not in df.columns:
            continue

        if col == 'zip_code':
            df['zip_code_normalized'] = normalize_zip_series(df['zip_code'])
        else:
            df[f"{col}_normalized"] = normalize_series(df[col]).str.strip()

    # ── OMS normalized versions (POS only) ──
    if base_name == 'pos':
        for col in OMS_TEXT_FIELDS:
            if col in df.columns:
                df[f"{col}_normalized"] = normalize_series(df[col]).str.strip()

        for col in OMS_ZIP_FIELDS:
            if col in df.columns:
                df[f"{col}_normalized"] = normalize_zip_series(df[col])

    # ── Passthrough fields: only strip whitespace ──
    for col in PASSTHROUGH_FIELDS:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    # ── Primary originals: ensure string + NaN → '' (no case/char changes) ──
    for col in FIELDS_TO_NORMALIZE:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    # ── OMS originals (POS only): same treatment as primary originals ──
    if base_name == 'pos':
        for col in ALL_OMS_FIELDS:
            if col in df.columns:
                df[col] = df[col].fillna('').astype(str).str.strip()

    return df


def _get_required_columns(base_name: str, stage: str):
    """
    Required column lists for enforce and clean stages.
    `clean` stage returns the FULL set of columns that should exist
    in the output CSV: originals + normalized + derived + passthrough.

    POS clean output includes OMS originals and OMS *_normalized cols.
    """
    if base_name == 'pos':
        if stage == "enforce":
            # Columns expected to exist on input (from SQL).
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
                'fiscal_year_lead', 'fiscal_period_lead',
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
                'updated_date', 'fiscal_year_lead', 'fiscal_period_lead',
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
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config

    engine = db_config.get_engine()
    storage_client = storage.Client()
    fiscal_info = get_costco_fiscal_info()

    # ── Build query ──
    if base_name == "pos":
        query_input = f'''{query_config.query_pos} = {fiscal_info["fiscal_year"]}'''
        source_folder_input = storage_config.source_folder_input_pos
        destination_folder_input = storage_config.destination_folder_input_pos
    else:  # leads
        query_input = f'''{query_config.query_leads} >= {fiscal_info["fiscal_year"] - 1}'''
        source_folder_input = storage_config.source_folder_input_leads
        destination_folder_input = storage_config.destination_folder_input_leads

    # ── Setup output GCS blob ──
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
    output_cols = _get_required_columns(base_name, "clean")

    # ── Archive source files ──
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

    with output_blob.open("w") as gcs_writer:

        for chunk_df in pd.read_sql(query_input, engine, chunksize=CHUNK_SIZE):
            chunk_count += 1
            chunk_rows = len(chunk_df)

            # Pipeline (originals preserved throughout)
            chunk_df = chunk_df.fillna("")
            chunk_df = enforce_required_columns(chunk_df, enforce_cols)
            chunk_df = clean_required_columns(chunk_df, base_name)   # creates *_normalized + OMS
            chunk_df = validate_combined_field(chunk_df)             # uses *_normalized

            # Restrict to the output column set (in defined order),
            # but only include columns that actually exist in the chunk.
            cols_to_write = [c for c in output_cols if c in chunk_df.columns]
            chunk_df = chunk_df[cols_to_write]

            # Write header only on first chunk
            chunk_df.to_csv(
                gcs_writer,
                index=False,
                header=(chunk_count == 1),
            )

            total_rows += chunk_rows
            print(f"  Chunk {chunk_count}: {chunk_rows:,} rows (running total: {total_rows:,})")
            del chunk_df
            gc.collect()

    output_uri = f"gs://{bucket.name}/{output_file}"
    print(f"✅ Wrote {total_rows:,} rows to {output_uri} in {chunk_count} chunks")

    return output_uri