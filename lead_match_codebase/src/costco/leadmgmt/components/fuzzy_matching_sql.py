import pandas as pd
from sqlalchemy import text, bindparam
from datetime import datetime

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs, process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info

SEMANTIC_QUALIFY_MIN_SCORE = 78
EXACT_QUALIFY_MIN_SCORE = 80
AMBIGUITY_DELTA = 3
HIGH_CONFIDENCE_RESULTS = {"Complete", "Match", "Closed - Match"}


# ==============================================================
# MATCHING COMMENT BUILDER — FUZZY
# ==============================================================
def build_fuzzy_matching_comment(row: pd.Series) -> str:
    """
    Constructs a human-readable comment for fuzzy-matched records,
    describing the embedding similarity scores that drove the match.
    Exact match records that were not overridden keep their original comment.
    """
    match_type       = row.get("match_type", "")
    result           = row.get("match_result", "")
    similarity_score = round(float(row.get("similarity_score", 0)), 2)
    combined_score   = round(float(row.get("combined_field_score", 0) or 0), 2)
    address_score    = round(float(row.get("full_address_score", 0) or 0), 2)
    name_score       = round(float(row.get("business_name_score", 0) or 0), 2)

    # Exact rows — preserve the comment written by primary_match
    if match_type == "Exact":
        return row.get("matching_comments", "")

    parts = []

    # -- Result classification --------------------------------
    if result in HIGH_CONFIDENCE_RESULTS:
        parts.append(
            f"Fuzzy match — complete confidence "
            f"(similarity score: {similarity_score})."
        )
    elif result == "Potential":
        parts.append(
            f"Fuzzy match — potential confidence "
            f"(similarity score: {similarity_score}); "
            f"Marketer review recommended."
        )
    else:
        parts.append(
            f"Fuzzy match — no confident result "
            f"(similarity score: {similarity_score})."
        )

    # -- Component scores -------------------------------------
    parts.append(
        f"Embedding scores — combined field: {combined_score}, "
        f"full address: {address_score}, "
        f"business name: {name_score}."
    )

    # -- Dominant signal --------------------------------------
    dominant = max(
        [
            ("combined field", combined_score),
            ("full address",   address_score),
            ("business name",  name_score),
        ],
        key=lambda x: x[1],
    )
    parts.append(f"Strongest signal: {dominant[0]} ({dominant[1]}).")

    return " ".join(parts)


# ==============================================================
# HELPERS
# ==============================================================
def execute_select_query(engine, query, params=None):
    with engine.connect() as connection:
        result = connection.execute(query, params or {})
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
    return df


def get_confidence_level(similarity_score, match_configuration_df):
    if pd.notna(similarity_score):
        if similarity_score >= 92:
            return "Closed - Match"
        if similarity_score >= 85:
            return "Potential"
        if similarity_score >= SEMANTIC_QUALIFY_MIN_SCORE:
            return "Potential"
        return "No Match"
    return "No Match"


def is_high_confidence(match_result):
    return match_result in HIGH_CONFIDENCE_RESULTS


# ==============================================================
# FUZZY MATCHING
# ==============================================================
def fuzzy_matching(file_classified_path: str, config_file_path: str) -> str:

    # ----------------------------------------------------------
    # INITIALIZATION
    # ----------------------------------------------------------
    job_config     = JobConfig(config_file_path)
    db_config      = job_config.db_config
    storage_config = job_config.storage_config
    query_config   = job_config.match_query

    source_bucket_name        = storage_config.source_bucket_name
    source_folder             = storage_config.source_folder_output
    destination_bucket_name   = storage_config.destination_bucket_name
    destination_folder        = storage_config.destination_folder_output

    query_fuzzy_wh            = query_config.query_fuzzy_wh
    query_fuzzy_null_wh       = query_config.query_fuzzy_null_wh
    query_match_configuration = query_config.query_match_configuration

    engine      = db_config.get_engine()
    fiscal_info = get_costco_fiscal_info()

    # ----------------------------------------------------------
    # LOAD EXACT MATCH OUTPUT
    # classified_df already has all POS columns from primary_match.
    # Fuzzy will only override values in place — no new columns
    # are added from the fuzzy side except scores (for comments).
    # ----------------------------------------------------------
    classified_df = load_file_from_gcs(file_classified_path)
    classified_df["warehouse_number"] = (
        pd.to_numeric(classified_df["warehouse_number"], errors="coerce")
        .astype("Int64")
    )
    classified_df["pos_id"] = classified_df["pos_id"].astype(str)
    classified_df["similarity_score"] = pd.to_numeric(
        classified_df.get("similarity_score", 0), errors="coerce"
    ).fillna(0.0)
    exact_qualified_leads = set(
        classified_df.loc[
            classified_df["similarity_score"] >= EXACT_QUALIFY_MIN_SCORE,
            "lead_id",
        ].dropna().astype(str)
    )

    # ----------------------------------------------------------
    # RUN FUZZY QUERY — leads with a warehouse number
    # ----------------------------------------------------------
    non_empty_wh_leads = (
        classified_df[classified_df["warehouse_number"].notna()]["lead_id"]
        .drop_duplicates()
        .tolist()
    )

    batch_size = 10000
    master_df  = pd.DataFrame()

    for i in range(0, len(non_empty_wh_leads), batch_size):
        leads_id_batch = non_empty_wh_leads[i : i + batch_size]
        if not leads_id_batch:
            continue

        params = {
            "fiscal_year_sales": fiscal_info["fiscal_year"],
            "leads_id_batch":    leads_id_batch,
        }

        query     = text(query_fuzzy_wh).bindparams(
            bindparam("leads_id_batch", expanding=True)
        )
        df_batch  = execute_select_query(engine, query, params)
        master_df = pd.concat([master_df, df_batch], ignore_index=True)

    # ----------------------------------------------------------
    # RUN FUZZY QUERY — leads without a warehouse number
    # ----------------------------------------------------------
    empty_wh_leads = (
        classified_df[classified_df["warehouse_number"].isna()]["lead_id"]
        .drop_duplicates()
        .tolist()
    )

    for i in range(0, len(empty_wh_leads), batch_size):
        leads_id_batch = empty_wh_leads[i : i + batch_size]
        if not leads_id_batch:
            continue

        params = {
            "fiscal_year_sales": fiscal_info["fiscal_year"],
            "leads_id_batch":    leads_id_batch,
        }

        query = text(query_fuzzy_null_wh).bindparams(
            bindparam("leads_id_batch", expanding=True)
        )
        df_batch = execute_select_query(engine, query, params)
        master_df = pd.concat([master_df, df_batch], ignore_index=True)

    if master_df.empty:
        df_fuzzy_result = pd.DataFrame(
            columns=[
                "lead_id", "pos_id", "similarity_score", "combined_field_score",
                "full_address_score", "business_name_score", "account_number",
                "fiscal_year", "fiscal_period", "week", "warehouse_number",
                "business_name",
            ]
        )
    else:
        # ----------------------------------------------------------
        # COMPUTE FUZZY SIMILARITY SCORE
        # ----------------------------------------------------------
        master_df["similarity_score"] = (
            (4 * master_df["full_address_score"]
             + 3 * master_df["business_name_score"]) / 7
        )

        df_fuzzy_result = master_df[
            master_df["similarity_score"] >= SEMANTIC_QUALIFY_MIN_SCORE
        ].copy()
        df_fuzzy_result = df_fuzzy_result[
            ~df_fuzzy_result["lead_id"].astype(str).isin(exact_qualified_leads)
        ]

        # Enforce POS-to-lead one-to-one. A lead may keep multiple POS rows.
        # Near-tie POS rows stay assigned to the strongest candidate but are routed to review.
        df_fuzzy_result = (
            df_fuzzy_result
            .sort_values(["pos_id", "similarity_score"], ascending=[True, False])
        )
        df_fuzzy_result["next_pos_score"] = (
            df_fuzzy_result.groupby("pos_id")["similarity_score"].shift(-1)
        )
        df_fuzzy_result["manual_review_reason"] = None
        ambiguous_mask = (
            df_fuzzy_result["next_pos_score"].notna()
            & ((df_fuzzy_result["similarity_score"] - df_fuzzy_result["next_pos_score"]) <= AMBIGUITY_DELTA)
        )
        df_fuzzy_result.loc[ambiguous_mask, "manual_review_reason"] = "ambiguous_pos_candidate"
        df_fuzzy_result = (
            df_fuzzy_result
            .drop_duplicates(subset=["pos_id"], keep="first")
        )

    # ----------------------------------------------------------
    # KEEP FUZZY RESULT SLIM — only scoring + core match fields.
    # classified_df already carries all POS customer detail cols.
    # Customer details for fuzzy-updated rows are fetched separately
    # after the mask is applied (see below) to avoid duplicate cols.
    # ----------------------------------------------------------
    df_fuzzy_result = df_fuzzy_result[[
        "lead_id",
        "pos_id",
        "similarity_score",
        "combined_field_score",
        "full_address_score",
        "business_name_score",
        "account_number",
        "fiscal_year",
        "fiscal_period",
        "week",
        "warehouse_number",
        "business_name",
        "manual_review_reason",
    ]].copy()

    # ----------------------------------------------------------
    # MERGE FUZZY RESULTS ONTO EXACT MATCH OUTPUT
    # Only slim fuzzy cols come in — no customer detail duplication.
    # After this merge, score cols will be suffixed:
    #   combined_field_score_primary, combined_field_score_fuzzy etc.
    # ----------------------------------------------------------
    merged_df = pd.merge(
        classified_df,
        df_fuzzy_result,
        how="left",
        on="lead_id",
        suffixes=("_primary", "_fuzzy"),
    )

    merged_df["similarity_score_primary"] = (
        pd.to_numeric(merged_df["similarity_score_primary"], errors="coerce")
        .fillna(0.0)
    )
    merged_df["similarity_score_fuzzy"] = pd.to_numeric(
        merged_df["similarity_score_fuzzy"], errors="coerce"
    )

    # Exact qualified rows stand. Semantic only recovers exact misses.
    exact_qualified_mask = merged_df["similarity_score_primary"] >= EXACT_QUALIFY_MIN_SCORE
    update_mask = (
        ~exact_qualified_mask
        & pd.notna(merged_df["pos_id_fuzzy"])
        & pd.notna(merged_df["similarity_score_fuzzy"])
        & (merged_df["similarity_score_fuzzy"] >= SEMANTIC_QUALIFY_MIN_SCORE)
    )
    merged_df["_fuzzy_update"] = update_mask

    print("=== COLUMNS AFTER MERGE ===")
    print(merged_df.columns.tolist())

    print("\n=== DTYPES AFTER MERGE ===")
    print(merged_df.dtypes)

    print("\n=== update_mask count ===")
    print(f"Rows to update: {update_mask.sum()}")

    # ----------------------------------------------------------
    # UPDATE CORE MATCH FIELDS WHERE FUZZY BEATS EXACT
    # ----------------------------------------------------------
    merged_df.loc[update_mask, "pos_id_primary"]            = merged_df.loc[update_mask, "pos_id_fuzzy"]
    merged_df.loc[update_mask, "similarity_score_primary"]  = merged_df.loc[update_mask, "similarity_score_fuzzy"]
    merged_df.loc[update_mask, "match_type"]                = "Fuzzy"
    if "manual_review_reason" in merged_df.columns:
        review_mask = update_mask & merged_df["manual_review_reason"].notna()
        merged_df.loc[review_mask, "match_type"] = "Manual Review"
        merged_df.loc[review_mask, "match_result"] = "Potential"
    merged_df.loc[update_mask, "account_number_primary"]    = pd.to_numeric(merged_df.loc[update_mask, "account_number_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "fiscal_year_transaction"]   = pd.to_numeric(merged_df.loc[update_mask, "fiscal_year"], errors="coerce")
    merged_df.loc[update_mask, "fiscal_period_transaction"] = pd.to_numeric(merged_df.loc[update_mask, "fiscal_period"], errors="coerce")
    merged_df.loc[update_mask, "week_primary"]              = pd.to_numeric(merged_df.loc[update_mask, "week_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "warehouse_number_primary"]  = pd.to_numeric(merged_df.loc[update_mask, "warehouse_number_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "business_name_transaction"] = merged_df.loc[update_mask, "business_name_fuzzy"]

    # ----------------------------------------------------------
    # FIX SCORE COLUMNS FOR COMMENT BUILDER
    # After the merge, score cols exist as _fuzzy suffixed float64
    # columns. Rename them to plain names and clear non-fuzzy rows.
    # This avoids creating new columns with dtype conflicts.
    # ----------------------------------------------------------
    rename_scores = {
        "combined_field_score_fuzzy": "combined_field_score",
        "full_address_score_fuzzy":   "full_address_score",
        "business_name_score_fuzzy":  "business_name_score",
    }
    merged_df.rename(columns=rename_scores, inplace=True)

    # Drop the _primary score cols — not needed
    merged_df.drop(
        columns=[
            "combined_field_score_primary",
            "full_address_score_primary",
            "business_name_score_primary",
        ],
        inplace=True,
        errors="ignore",
    )

    # Clear scores on non-fuzzy rows so comment builder skips them
    for score_col in ["combined_field_score", "full_address_score", "business_name_score"]:
        if score_col in merged_df.columns:
            merged_df.loc[~update_mask, score_col] = None

    # ----------------------------------------------------------
    # DROP ALL REMAINING _fuzzy COLUMNS + NORMALISE _primary NAMES
    # ----------------------------------------------------------
    fuzzy_cols = [c for c in merged_df.columns if c.endswith("_fuzzy")]
    merged_df.drop(
        columns=fuzzy_cols + ["fiscal_year", "fiscal_period"],
        inplace=True,
        errors="ignore",
    )
    merged_df.columns = merged_df.columns.str.replace(r"_primary$", "", regex=True)
    update_mask = merged_df["_fuzzy_update"].fillna(False)

    # ----------------------------------------------------------
    # FETCH CUSTOMER DETAIL FIELDS FOR FUZZY-UPDATED ROWS ONLY
    # Now that pos_id is normalised, join transaction table just
    # for the rows that were overridden by fuzzy — no duplicates.
    # ----------------------------------------------------------
    fuzzy_updated_pos_ids = (
        merged_df.loc[update_mask, "pos_id"]
        .dropna()
        .unique()
        .tolist()
    )

    if fuzzy_updated_pos_ids:
        pos_details = execute_select_query(
            engine,
            text(f"""
                SELECT
                    pos_id,
                    membership_number,
                    shop_type,
                    sales_reference_id,
                    order_amount,
                    bd_industry,
                    industry_description,
                    first_name,
                    last_name,
                    address_line_one,
                    address_line_two,
                    city,
                    state,
                    zip_code,
                    email,
                    phone
                FROM {db_config.schema_name}.transaction
                WHERE pos_id IN :pos_id_list
            """).bindparams(bindparam("pos_id_list", expanding=True)),
            {"pos_id_list": fuzzy_updated_pos_ids},
        )

        # Merge pos_details with _new suffix to avoid collision
        merged_df = merged_df.merge(
            pos_details, on="pos_id", how="left", suffixes=("", "_new")
        )
        update_mask = merged_df["_fuzzy_update"].fillna(False)

        # Update customer detail cols only on fuzzy-overridden rows
        detail_cols = [
            "membership_number",
            "shop_type",
            "sales_reference_id",
            "order_amount",
            "bd_industry",
            "industry_description",
            "first_name",
            "last_name",
            "address_line_one",
            "address_line_two",
            "city",
            "state",
            "zip_code",
            "email",
            "phone",
        ]
        for col in detail_cols:
            new_col = f"{col}_new"
            if new_col in merged_df.columns:
                merged_df.loc[update_mask, col] = merged_df.loc[update_mask, new_col]
                merged_df.drop(columns=[new_col], inplace=True)

    # ----------------------------------------------------------
    # RE-APPLY MATCH RESULT FROM CONFIG TABLE
    # Covers both exact rows passed through and fuzzy-updated rows
    # ----------------------------------------------------------
    match_configuration_df = execute_select_query(
        engine, text(query_match_configuration)
    )
    merged_df.loc[update_mask, "match_result"] = merged_df.loc[
        update_mask, "similarity_score"
    ].apply(lambda x: get_confidence_level(x, match_configuration_df))

    # ----------------------------------------------------------
    # PRIMARY TRANSACTION LOGIC
    # Earliest Complete transaction per lead is flagged primary
    # ----------------------------------------------------------
    merged_df = merged_df.sort_values(
        by=["lead_id", "fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    merged_df["primary_transaction"] = False
    merged_df["rank"] = merged_df.groupby("lead_id").cumcount() + 1

    high_conf_mask = merged_df["match_result"].apply(is_high_confidence)
    merged_df.loc[high_conf_mask, "primary_transaction"] = (
        merged_df[high_conf_mask].groupby("lead_id").cumcount() == 0
    )
    merged_df.drop(columns=["rank"], inplace=True)

    # ----------------------------------------------------------
    # MATCHING COMMENTS
    # Fuzzy rows get a descriptive comment with component scores.
    # Exact rows keep the comment written by primary_match.
    # ----------------------------------------------------------
    merged_df["matching_comments"] = merged_df.apply(
        build_fuzzy_matching_comment, axis=1
    )

    # Score columns no longer needed after comments are built
    merged_df.drop(
        columns=["combined_field_score", "full_address_score", "business_name_score"],
        inplace=True,
        errors="ignore",
    )

    # ----------------------------------------------------------
    # HANDLE No Match rows
    # ----------------------------------------------------------
    no_match_mask = merged_df["match_result"] == "No Match"
    merged_df.loc[no_match_mask, "pos_id"] = ""
    no_match_detail_cols = [
        "membership_number",
        "shop_type",
        "sales_reference_id",
        "order_amount",
        "bd_industry",
        "industry_description",
        "first_name",
        "last_name",
        "address_line_one",
        "address_line_two",
        "city",
        "state",
        "zip_code",
        "email",
        "phone",
        "business_name_transaction",
    ]
    for col in no_match_detail_cols:
        if col in merged_df.columns:
            merged_df.loc[no_match_mask, col] = None
    merged_df["account_number"] = merged_df["account_number"].fillna(0)
    merged_df.drop(columns=["_fuzzy_update"], inplace=True, errors="ignore")

    # ----------------------------------------------------------
    # FINAL OUTPUT SCHEMA — identical to what ServiceNow expects
    # ----------------------------------------------------------
    output_cols = [
        # Matching
        "lead_id",
        "pos_id",
        "match_result",
        "similarity_score",
        "match_type",
        "primary_transaction",
        "matched_by",
        "matching_comments",

        # POS dominant — transaction
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

        # POS dominant — customer details
        "first_name",
        "last_name",
        "address_line_one",
        "address_line_two",
        "city",
        "state",
        "zip_code",
        "email",
        "phone",

        "updated_date",
    ]

    # Guard against any column missing due to edge cases
    output_cols = [c for c in output_cols if c in merged_df.columns]
    final_df    = merged_df[output_cols].copy()
    final_df    = final_df.drop_duplicates(subset=["lead_id", "pos_id"])
    final_df["updated_date"] = pd.to_datetime(datetime.now())

    # ----------------------------------------------------------
    # WRITE TO GCS
    # ----------------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"final_update_dataframe_{timestamp}"

    uri = process_and_archive_files(
        source_bucket_name,
        source_folder,
        destination_bucket_name,
        destination_folder,
        final_df,
        base_name,
    )

    print(f"Fuzzy match output written to: {uri}")
    return uri
