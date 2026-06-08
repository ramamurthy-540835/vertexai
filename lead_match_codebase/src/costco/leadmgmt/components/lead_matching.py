import gc
import logging
from datetime import datetime

import pandas as pd
from sqlalchemy import MetaData, Table, insert

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import (
    load_file_from_gcs,
    process_and_archive_files,
)

log = logging.getLogger(__name__)

# ==============================================================
# SCORING CONSTANTS
# ==============================================================
# Matching is done on *_normalized columns (created in preprocess).
# Originals (e.g. business_name, address_line_one) are preserved in
# the dataframe and used for the ServiceNow payload.
KEY_FIELDS = {
    "business_name_normalized":    40,
    "address_line_one_normalized": 40,
    "email_normalized":            30,
    "phone_normalized":            20,
}

SUPPLEMENTARY_FIELDS = {
    "zip_code_normalized": 10,
    "state_normalized":     5,
    "city_normalized":      5,
}

MINIMUM_SCORE  = 80
COMPLETE_SCORE = 100

MAX_POSSIBLE_SCORE = (
    sum(KEY_FIELDS.values()) +
    sum(SUPPLEMENTARY_FIELDS.values())
)

ALL_FIELDS = (
    list(KEY_FIELDS.keys()) +
    list(SUPPLEMENTARY_FIELDS.keys())
)


# Friendly names for matching comments (strip the _normalized suffix)
def _friendly(field: str) -> str:
    return field.replace("_normalized", "")


# ==============================================================
# MATCHING COMMENT BUILDER
# ==============================================================
def build_matching_comment(row: pd.Series) -> str:
    """
    Constructs a human-readable comment explaining which fields
    drove the match and what the result classification means.

    Called once per matched (lead_id, pos_id) pair after scores
    are aggregated.

    Architecture note: GCP owns matching logic; ServiceNow receives
    this comment as part of the confirmed match record payload so
    agents can understand why the system matched a lead to a POS
    transaction without re-running the algorithm. Field names are
    shown without the _normalized suffix for human readability.
    """
    score        = row["similarity_score"]
    result       = row["match_result"]
    matched_keys = row.get("matched_key_fields", [])
    matched_supp = row.get("matched_supp_fields", [])

    parts = []

    # -- Result classification --------------------------------
    if result == "Match":
        parts.append(
            f"Complete match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"sufficient key and supplementary fields aligned."
        )
    else:
        parts.append(
            f"Potential match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"partial field alignment; Marketer review recommended."
        )

    # -- Key fields that matched ------------------------------
    if matched_keys:
        friendly_keys = [_friendly(f) for f in matched_keys]
        parts.append(f"Key fields matched: {', '.join(friendly_keys)}.")
    else:
        parts.append(
            "No individual key fields matched exactly; "
            "match qualified via supplementary fields only."
        )

    # -- Supplementary fields that matched --------------------
    if matched_supp:
        friendly_supp = [_friendly(f) for f in matched_supp]
        parts.append(f"Supplementary fields matched: {', '.join(friendly_supp)}.")

    # -- Primary transaction note ----------------------------
    if row.get("primary_transaction"):
        parts.append(
            "Designated as primary transaction "
            "(earliest fiscal period for this lead)."
        )

    return " ".join(parts)


# ==============================================================
# PREPROCESS
# ==============================================================
def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize warehouse_number and clean *_normalized matching columns.
    Original (display) columns are NOT touched here — they flow through
    to the ServiceNow payload with their original casing intact.
    """
    df = df.copy()
    df = df.dropna(subset=["warehouse_number"])
    df["warehouse_number"] = (
        pd.to_numeric(df["warehouse_number"], errors="coerce")
        .astype("Int64")
        .astype(str)
    )
    df = df[df["warehouse_number"] != ""]

    # Only operate on the *_normalized matching columns. If a normalized
    # column is missing (shouldn't happen given preprocess.py output),
    # fall back to creating it from the original.
    for col in ALL_FIELDS:
        if col not in df.columns:
            original_col = _friendly(col)
            if original_col in df.columns:
                df[col] = df[original_col].astype(str).str.strip().str.lower()
            else:
                df[col] = pd.NA
                continue

        # If pandas inferred this column as float during CSV read
        # (e.g. zip '98272' read as 98272.0), convert via nullable Int64
        # first so stringification produces '98272' not '98272.0'.
        if pd.api.types.is_float_dtype(df[col]):
            df[col] = (
                pd.to_numeric(df[col], errors="coerce")
                .astype("Int64")
                .astype(str)
            )

        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .replace({"nan": pd.NA, "<NA>": pd.NA, "": pd.NA})
        )
    return df

# ==============================================================
# CLASSIFY MATCHES
# ==============================================================
def classify_matches(
    file_leads: pd.DataFrame,
    file_sales: pd.DataFrame,
) -> pd.DataFrame:

    leads = preprocess(file_leads)
    sales = preprocess(file_sales)
    
    # sales data prefilter
    lead_warehouses = leads["warehouse_number"].dropna().unique()
    sales_before = len(sales)
    sales = sales[sales["warehouse_number"].isin(lead_warehouses)].copy()
    log.info(
        "Warehouse pre-filter: %d → %d POS rows (%d dropped)",
        sales_before, len(sales), sales_before - len(sales),
    )

    if sales.empty:
        log.warning("No POS rows share a warehouse with any lead.")
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "match_result",
            "similarity_score", "matching_comments",
        ])

    leads_small = leads[
        ["lead_id", "warehouse_number"] + ALL_FIELDS
    ].copy()

    sales_small = sales[
        ["pos_id", "warehouse_number"] + ALL_FIELDS
    ].copy()

    # ==========================================================
    # PHASE 1 — KEY FIELD MATCHING
    # ==========================================================
    score_frames     = []
    key_score_frames = {}   # field -> DataFrame(lead_id, pos_id)

    for field, score in KEY_FIELDS.items():
        log.info("Processing key field: %s", field)

        sales_valid = sales_small.dropna(subset=["warehouse_number", field])
        leads_valid = leads_small.dropna(subset=["warehouse_number", field])

        if sales_valid.empty or leads_valid.empty:
            continue

        matched = leads_valid.merge(
            sales_valid,
            on=["warehouse_number", field],
            how="inner",
            suffixes=("_lead", "_sale"),
        )[["lead_id", "pos_id"]].drop_duplicates()

        if matched.empty:
            continue

        matched["score"] = score
        score_frames.append(matched)
        key_score_frames[field] = matched[["lead_id", "pos_id"]].copy()

        log.info("%s: %d matches", field, len(matched))

    # -- Free slim copies early
    del leads_small, sales_small
    gc.collect()

    if not score_frames:
        log.warning("No candidate pairs found after Phase 1.")
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "match_result",
            "similarity_score", "matching_comments",
        ])

    # ==========================================================
    # CANDIDATE PAIRS
    # ==========================================================
    candidate_pairs = (
        pd.concat(score_frames, ignore_index=True)
        [["lead_id", "pos_id"]]
        .drop_duplicates()
    )
    log.info("Total candidate pairs: %d", len(candidate_pairs))

    # ==========================================================
    # PHASE 2 — SUPPLEMENTARY MATCHING
    # ==========================================================
    leads_supp = (
        leads[["lead_id"] + list(SUPPLEMENTARY_FIELDS.keys())]
        .drop_duplicates(subset=["lead_id"])
    )
    sales_supp = (
        sales[["pos_id"] + list(SUPPLEMENTARY_FIELDS.keys())]
        .drop_duplicates(subset=["pos_id"])
    )

    candidates_with_data = (
        candidate_pairs
        .merge(leads_supp, on="lead_id", how="left")
        .merge(sales_supp, on="pos_id", how="left", suffixes=("_lead", "_sale"))
    )

    supp_score_frames = {}  # field -> DataFrame(lead_id, pos_id)

    for field, score in SUPPLEMENTARY_FIELDS.items():
        lead_col = f"{field}_lead"
        sale_col = f"{field}_sale"

        mask = (
            candidates_with_data[lead_col].notna()
            & candidates_with_data[sale_col].notna()
            & (candidates_with_data[lead_col] == candidates_with_data[sale_col])
        )
        supp_matches = candidates_with_data.loc[mask, ["lead_id", "pos_id"]].copy()

        if supp_matches.empty:
            continue

        supp_matches["score"] = score
        score_frames.append(supp_matches)
        supp_score_frames[field] = supp_matches[["lead_id", "pos_id"]].copy()

        log.info("%s: %d supplementary matches", field, len(supp_matches))

    # ==========================================================
    # AGGREGATE SCORES
    # ==========================================================
    pair_scores = (
        pd.concat(score_frames, ignore_index=True)
        .groupby(["lead_id", "pos_id"], as_index=False)["score"]
        .sum()
        .rename(columns={"score": "similarity_score"})
    )

    # ==========================================================
    # FILTER QUALIFIED MATCHES
    # ==========================================================
    qualified = (
        pair_scores[pair_scores["similarity_score"] >= MINIMUM_SCORE]
        .drop_duplicates(subset=["lead_id", "pos_id"])
        .copy()
    )

    if qualified.empty:
        log.warning("No pairs met MINIMUM_SCORE=%d.", MINIMUM_SCORE)
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "match_result",
            "similarity_score", "matching_comments",
        ])

    # ==========================================================
    # VECTORIZED MATCHED FIELD TRACKING
    # Build one tall DataFrame per field group, then groupby to
    # get a list of matched fields per (lead_id, pos_id) pair.
    # ==========================================================

    # Key fields
    if key_score_frames:
        key_field_df = pd.concat(
            [
                frame.assign(field=field_name)
                for field_name, frame in key_score_frames.items()
            ],
            ignore_index=True,
        )
        key_field_map = (
            key_field_df
            .groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_key_fields")
        )
        qualified = qualified.merge(
            key_field_map, on=["lead_id", "pos_id"], how="left"
        )
        qualified["matched_key_fields"] = qualified["matched_key_fields"].apply(
            lambda x: x if isinstance(x, list) else []
        )
    else:
        qualified["matched_key_fields"] = [[]] * len(qualified)

    # Supplementary fields
    if supp_score_frames:
        supp_field_df = pd.concat(
            [
                frame.assign(field=field_name)
                for field_name, frame in supp_score_frames.items()
            ],
            ignore_index=True,
        )
        supp_field_map = (
            supp_field_df
            .groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_supp_fields")
        )
        qualified = qualified.merge(
            supp_field_map, on=["lead_id", "pos_id"], how="left"
        )
        qualified["matched_supp_fields"] = qualified["matched_supp_fields"].apply(
            lambda x: x if isinstance(x, list) else []
        )
    else:
        qualified["matched_supp_fields"] = [[]] * len(qualified)

    # ==========================================================
    # BUILD SUBSETS FOR FINAL MERGE
    # Pull ORIGINAL columns (not normalized) so the ServiceNow
    # payload carries original casing and special characters.
    # Rename only inside subset — original sales df untouched.
    # ==========================================================
    lead_subset = leads[[
        "lead_id",
        "updated_date",
        "fiscal_year_lead",
        "fiscal_period_lead",
    ]]

    sales_subset = (
        sales
        .rename(columns={"business_name": "business_name_transaction"})
        [[
            "pos_id",
            "account_number",
            "business_name_transaction",   # original (renamed)
            "membership_number",
            "warehouse_number",
            "fiscal_year_transaction",
            "fiscal_period_transaction",
            "week",
            "shop_type",                   # passthrough
            "sales_reference_id",          # passthrough
            "order_amount",
            "bd_industry",                 # passthrough
            "first_name",                  # original
            "last_name",                   # original
            "address_line_one",            # original
            "address_line_two",            # original
            "city",                        # original
            "state",                       # original
            "zip_code",                    # original
            "email",                       # original
            "phone",                       # original
            "industry_description"         # passthrough
        ]]
    )

    # ==========================================================
    # BUILD MATCHED DATAFRAME
    # ==========================================================
    matched_df = (
        qualified
        .merge(lead_subset, on="lead_id", how="inner")
        .merge(sales_subset, on="pos_id", how="inner")
    )

    # ==========================================================
    # FISCAL FILTER
    # ==========================================================
    later_year = (
        matched_df["fiscal_year_lead"] < matched_df["fiscal_year_transaction"]
    )
    same_year_later_period = (
        (matched_df["fiscal_year_lead"] == matched_df["fiscal_year_transaction"])
        & (matched_df["fiscal_period_lead"] <= matched_df["fiscal_period_transaction"])
    )
    matched_df = matched_df[later_year | same_year_later_period].copy()

    if matched_df.empty:
        log.warning("No matches survived the fiscal filter.")
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "match_result",
            "similarity_score", "matching_comments",
        ])

    # ==========================================================
    # ASSIGN MATCH RESULT
    # ==========================================================
    matched_df["match_result"] = matched_df["similarity_score"].apply(
        lambda x: "Match" if x >= COMPLETE_SCORE else "Potential"
    )
    matched_df["match_type"] = "Exact"
    matched_df["matched_by"] = "System"

    # ==========================================================
    # PRIMARY TRANSACTION LOGIC
    # ==========================================================
    # Default everyone to False
    matched_df["primary_transaction"] = False

    # Only rank Match rows
    match_only = matched_df[matched_df["match_result"] == "Match"].copy()
    match_only = match_only.sort_values(
        by=["lead_id", "fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    match_only["rank"] = match_only.groupby("lead_id").cumcount() + 1

    # Mark primary on the earliest Match row, using its index
    primary_idx = match_only[match_only["rank"] == 1].index
    matched_df.loc[primary_idx, "primary_transaction"] = True

    # ==========================================================
    # MATCHING COMMENTS
    # Built after primary_transaction is assigned so the comment
    # can reference it.
    # ==========================================================
    matched_df["matching_comments"] = matched_df.apply(
        build_matching_comment, axis=1
    )

    # ==========================================================
    # SERVICENOW MAPPINGS
    # ==========================================================
    matched_df["u_matched_lead_number"] = matched_df["lead_id"]
    matched_df["u_order_amount"]        = matched_df["order_amount"]
    matched_df["u_order_amount_rounded"] = (
        pd.to_numeric(matched_df["order_amount"], errors="coerce").round(2)
    )
    matched_df["updated_date"] = pd.to_datetime(datetime.now())

    # ==========================================================
    # FINAL OUTPUT
    # ==========================================================
    final_df = matched_df[[
        # Matching
        "lead_id",
        "pos_id",
        "match_result",
        "similarity_score",
        "match_type",
        "primary_transaction",
        "matched_by",
        "matching_comments",

        # POS dominant
        "account_number",
        "business_name_transaction",
        "membership_number",
        "warehouse_number",
        "sales_reference_id",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "shop_type",
        "bd_industry",
        "order_amount",
        "industry_description",

        # POS customer details (originals — for ServiceNow payload)
        "first_name",
        "last_name",
        "address_line_one",
        "address_line_two",
        "city",
        "state",
        "zip_code",
        "email",
        "phone",

        # ServiceNow
        "u_matched_lead_number",
        "u_order_amount",
        "u_order_amount_rounded",
        "updated_date",
    ]].copy()

    final_df = (
    final_df
    .sort_values(
        by=["similarity_score", "primary_transaction", "match_result"],
        ascending=[False, False, True],   # score↓, primary↓, "Match" < "Potential" alphabetically
    )
    .drop_duplicates(subset=["lead_id", "pos_id"], keep="first")
)
    log.info("Final matched records: %d", len(final_df))
    return final_df


# ==============================================================
# PRIMARY CLASSIFICATION (orchestrator)
# ==============================================================
def primary_classification(
    match_id: str,
    config_file_path: str,
    file_a_path: str = "",
    file_b_path: str = "",
) -> str:
    """
    Orchestrates the end-to-end leads-to-POS matching pipeline for a
    single match run.

    Workflow:
        1. Loads job configuration (storage + DB) from the provided
           config file path.
        2. Reads the leads and POS Parquet files from GCS (falls back
           to paths defined in config if not explicitly provided).
        3. Inserts an audit record into the DB with status "InProgress"
           before classification begins.
        4. Delegates matching to ``classify_matches()``, which runs
           Phase 1 (key field scoring), Phase 2 (supplementary scoring),
           fiscal filtering, and primary transaction assignment.
        5. Writes the final matched DataFrame to GCS via
           ``process_and_archive_files()`` and returns the output URI.

    Args:
        match_id (str):
            Unique identifier for this match run. Used in the audit
            table insert and the output file name.
        config_file_path (str):
            GCS or local path to the YAML/JSON job configuration file.
            Must contain valid ``storage_config`` and ``db_config``
            sections.
        file_a_path (str, optional):
            GCS path to the leads Parquet file. Defaults to
            ``storage_config.temp_leads_path`` if not provided.
        file_b_path (str, optional):
            GCS path to the POS Parquet file. Defaults to
            ``storage_config.temp_pos_path`` if not provided.

    Returns:
        str: GCS URI of the written output file (e.g.
             ``gs://bucket/folder/primary_match_output_<match_id>_<ts>.parquet``).

    Raises:
        Any exception propagated from ``load_file_from_gcs()``,
        ``classify_matches()``, or ``process_and_archive_files()``
        will bubble up uncaught — the caller (Cloud Workflows / Cloud
        Run Job) is expected to handle retries and dead-letter routing.
    """

    job_config     = JobConfig(config_file_path)
    storage_config = job_config.storage_config
    db_config      = job_config.db_config

    if file_a_path == "":
        file_a_path = storage_config.temp_leads_path
    if file_b_path == "":
        file_b_path = storage_config.temp_pos_path

    source_bucket_name      = storage_config.source_bucket_name
    source_folder           = storage_config.source_folder_output
    destination_bucket_name = storage_config.destination_bucket_name
    destination_folder      = storage_config.destination_folder_output

    schema     = db_config.schema_name
    table_name = db_config.audit_table_name
    engine     = db_config.get_engine()
    metadata   = MetaData()

    STRING_COLS = {
    "zip_code": str,
    "zip_code_normalized": str,
    "phone": str,
    "phone_normalized": str,
    "warehouse_number": str,
    "membership_number": str,
    "account_number": str,
}

    log.info("Loading leads file: %s", file_a_path)
    file_a = load_file_from_gcs(file_a_path,dtype=STRING_COLS)

    log.info("Loading POS file: %s", file_b_path)
    file_b = load_file_from_gcs(file_b_path,dtype=STRING_COLS)

    log.info("Lead count: %d | POS count: %d", len(file_a), len(file_b))

    user_table_obj = Table(
        table_name, metadata,
        autoload_with=engine,
        schema=schema,
    )
    stmt = insert(user_table_obj).values(
        match_id=match_id,
        lead_count=len(file_a),
        pos_count=len(file_b),
        status="InProgress",
    )
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()

    final_df = classify_matches(file_a, file_b)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"primary_match_output_{match_id}_{timestamp}"

    uri = process_and_archive_files(
        source_bucket_name,
        source_folder,
        destination_bucket_name,
        destination_folder,
        final_df,
        base_name,
    )

    log.info("Final output written to: %s", uri)

    del file_a, file_b, final_df
    gc.collect()

    return uri