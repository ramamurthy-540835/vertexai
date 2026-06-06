import gc
import logging
from datetime import datetime

import pandas as pd
import sqlalchemy
from google.cloud import storage
from sqlalchemy import insert, MetaData, Table

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs

log = logging.getLogger(__name__)

# ==============================================================
# SCORING CONSTANTS
# ==============================================================
# Matching is done on *_normalized columns (created in preprocess).
# Originals are preserved in the dataframe for the ServiceNow payload.
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
    Human-readable comment explaining match drivers and classification.
    Field names are shown without the _normalized suffix for clarity.
    """
    score        = row["similarity_score"]
    result       = row["match_result"]
    matched_keys = row.get("matched_key_fields", [])
    matched_supp = row.get("matched_supp_fields", [])

    parts = []

    if result == "Match":
        parts.append(
            f"Exact match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"sufficient key and supplementary fields aligned."
        )
    elif result == "Potential":
        parts.append(
            f"Potential match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"partial field alignment; Marketer review recommended."
        )
    else:
        parts.append(f"No match (score {score}/{MAX_POSSIBLE_SCORE}).")

    if matched_keys:
        friendly_keys = [_friendly(f) for f in matched_keys]
        parts.append(f"Key fields matched: {', '.join(friendly_keys)}.")
    else:
        parts.append(
            "No individual key fields matched exactly; "
            "match qualified via supplementary fields only."
        )

    if matched_supp:
        friendly_supp = [_friendly(f) for f in matched_supp]
        parts.append(f"Supplementary fields matched: {', '.join(friendly_supp)}.")

    return " ".join(parts)


# ==============================================================
# PREPROCESS
# ==============================================================
def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize warehouse_number and clean *_normalized matching columns.
    Original (display) columns are NOT touched here.
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
    # column is missing (shouldn't happen given preprocess.py), fall back
    # to creating it from the original.
    for col in ALL_FIELDS:
        if col not in df.columns:
            original_col = _friendly(col)
            if original_col in df.columns:
                df[col] = df[original_col].astype(str).str.strip().str.lower()
            else:
                df[col] = pd.NA
                continue

        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .replace({"nan": pd.NA, "": pd.NA})
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

    # ----------------------------------------------------------
    # WAREHOUSE PRE-FILTER
    # ----------------------------------------------------------
    lead_warehouses = leads["warehouse_number"].dropna().unique()
    sales_before = len(sales)
    sales = sales[sales["warehouse_number"].isin(lead_warehouses)].copy()
    log.info(
        "Warehouse pre-filter: %d → %d POS rows (%d dropped)",
        sales_before, len(sales), sales_before - len(sales),
    )

    if sales.empty:
        log.warning("No POS rows share a warehouse with any lead.")
        no_match_df = file_leads.copy()
        no_match_df["match_result"]      = "No Match"
        no_match_df["similarity_score"]  = 0
        no_match_df["pos_id"]            = "NA"
        no_match_df["match_type"]        = "Exact"
        no_match_df["matching_comments"] = ""
        return no_match_df

    # Slim frames for the per-field merges (matching columns only)
    leads_small = leads[["lead_id", "warehouse_number"] + ALL_FIELDS].copy()
    sales_small = sales[["pos_id", "warehouse_number"] + ALL_FIELDS].copy()

    # ==========================================================
    # PHASE 1 — KEY FIELD MATCHING (vectorised merge)
    # ==========================================================
    score_frames     = []
    key_score_frames = {}

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

    del leads_small, sales_small
    gc.collect()

    if not score_frames:
        log.warning("No candidate pairs found after Phase 1.")
        no_match_df = file_leads.copy()
        no_match_df["match_result"]      = "No Match"
        no_match_df["similarity_score"]  = 0
        no_match_df["pos_id"]            = "NA"
        no_match_df["match_type"]        = "Exact"
        no_match_df["matching_comments"] = ""
        return no_match_df

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

    supp_score_frames = {}

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
        .sort_values("similarity_score", ascending=False)
        .drop_duplicates(subset=["pos_id"])
    )

    qualified = pair_scores[pair_scores["similarity_score"] >= MINIMUM_SCORE].copy()

    # ==========================================================
    # VECTORIZED MATCHED FIELD TRACKING
    # ==========================================================
    if key_score_frames:
        key_field_df = pd.concat(
            [frame.assign(field=f) for f, frame in key_score_frames.items()],
            ignore_index=True,
        )
        key_field_map = (
            key_field_df.groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_key_fields")
        )
        qualified = qualified.merge(key_field_map, on=["lead_id", "pos_id"], how="left")
        qualified["matched_key_fields"] = qualified["matched_key_fields"].apply(
            lambda x: x if isinstance(x, list) else []
        )
    else:
        qualified["matched_key_fields"] = [[]] * len(qualified)

    if supp_score_frames:
        supp_field_df = pd.concat(
            [frame.assign(field=f) for f, frame in supp_score_frames.items()],
            ignore_index=True,
        )
        supp_field_map = (
            supp_field_df.groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_supp_fields")
        )
        qualified = qualified.merge(supp_field_map, on=["lead_id", "pos_id"], how="left")
        qualified["matched_supp_fields"] = qualified["matched_supp_fields"].apply(
            lambda x: x if isinstance(x, list) else []
        )
    else:
        qualified["matched_supp_fields"] = [[]] * len(qualified)

    # ==========================================================
    # POS-DOMINANT SUBSETS FOR FINAL MERGE
    # ==========================================================
    # Pull ORIGINAL columns for the ServiceNow payload.
    # Normalized columns are not needed downstream.
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
            "industry_description",        # passthrough
        ]]
    )

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

    # ==========================================================
    # ASSIGN MATCH RESULT
    # ==========================================================
    def assign_confidence(score):
        if score >= COMPLETE_SCORE:
            return "Match"
        elif score >= MINIMUM_SCORE:
            return "Potential"
        else:
            return "No Match"

    matched_df["match_result"] = matched_df["similarity_score"].apply(assign_confidence)
    matched_df["match_type"]   = "Exact"
    matched_df["matched_by"]   = "System"

    # ==========================================================
    # MATCHING COMMENTS
    # ==========================================================
    matched_df["matching_comments"] = matched_df.apply(
        build_matching_comment, axis=1
    )

    # Drop field-tracking columns — internal use only
    matched_df.drop(
        columns=["matched_key_fields", "matched_supp_fields"],
        errors="ignore",
        inplace=True,
    )

    # ==========================================================
    # NO MATCH RECORDS
    # ==========================================================
    matched_ids = set(qualified["lead_id"])
    no_match_df = file_leads[~file_leads["lead_id"].isin(matched_ids)].copy()
    no_match_df["match_result"]      = "No Match"
    no_match_df["similarity_score"]  = 0
    no_match_df["pos_id"]            = "NA"
    no_match_df["match_type"]        = "Exact"
    no_match_df["matched_by"]        = "System"
    no_match_df["matching_comments"] = ""

    final_df = pd.concat([matched_df, no_match_df], ignore_index=True)

    log.info(
        "Final records — matched: %d | no match: %d",
        len(matched_df), len(no_match_df),
    )
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
    Orchestrates the exact-match leads-to-POS pipeline.
    Output is passed downstream to fuzzy_matching, which produces
    the final record sent to ServiceNow.
    """
    job_config     = JobConfig(config_file_path)
    storage_config = job_config.storage_config
    db_config      = job_config.db_config

    if file_a_path == "":
        file_a_path = storage_config.temp_leads_path
    if file_b_path == "":
        file_b_path = storage_config.temp_pos_path

    preprocessed_folder        = storage_config.temporary_folder
    output_bucket              = storage_config.output_bucket_name
    leads_classified_file_name = storage_config.leads_classified_file_name

    schema     = db_config.schema_name
    table_name = db_config.audit_table_name
    engine     = db_config.get_engine()
    metadata   = MetaData()

    storage_client = storage.Client()

    log.info("Loading leads file: %s", file_a_path)
    file_a = load_file_from_gcs(file_a_path)

    log.info("Loading POS file: %s", file_b_path)
    file_b = load_file_from_gcs(file_b_path)

    log.info("Lead count: %d | POS count: %d", len(file_a), len(file_b))

    user_table_obj = Table(table_name, metadata, autoload_with=engine, schema=schema)
    stmt = insert(user_table_obj).values(
        match_id=match_id,
        lead_count=len(file_a),
        pos_count=len(file_b),
        status="InProgress",
    )
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()

    df = classify_matches(file_a, file_b)

    output_file = f"{preprocessed_folder}/{leads_classified_file_name}"
    bucket      = storage_client.get_bucket(output_bucket)
    output_blob = bucket.blob(output_file)
    output_blob.upload_from_string(df.to_csv(index=False), "text/csv")

    uri = f"gs://{bucket.name}/{output_file}"
    log.info("Exact match output written to: %s", uri)

    del file_a, file_b, df
    gc.collect()

    return uri