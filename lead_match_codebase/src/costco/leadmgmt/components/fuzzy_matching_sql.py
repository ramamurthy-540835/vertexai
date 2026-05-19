import pandas as pd
from sqlalchemy import text, bindparam
from datetime import datetime

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs, process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


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
    if result == "Match":
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
        for _, row in match_configuration_df.iterrows():
            if row["min_score"] <= similarity_score <= row["max_score"]:
                return row["match_result"]
        return "No Match"
    return "No Match"


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
    # ----------------------------------------------------------
    classified_df = load_file_from_gcs(file_classified_path)
    classified_df["warehouse_number"] = (
        pd.to_numeric(classified_df["warehouse_number"], errors="coerce")
        .astype("Int64")
    )
    classified_df["pos_id"] = classified_df["pos_id"].astype(str)

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
    # COMPUTE FUZZY SIMILARITY SCORE
    # ----------------------------------------------------------
    master_df["similarity_score"] = (
        (master_df["combined_field_score"]
         + 4 * master_df["full_address_score"]
         + 3 * master_df["business_name_score"]) / 8
    )

    df_fuzzy_result = master_df[master_df["similarity_score"] >= 80].copy()

    # ----------------------------------------------------------
    # JOIN FULL POS TRANSACTION DETAILS ONTO FUZZY RESULTS
    # The fuzzy query (against pos_embeddings) only returns the
    # fields needed for similarity scoring.  All customer detail
    # and transaction fields are fetched here via a single SQL
    # join against the full POS / transaction table so we don't
    # need to duplicate those columns in pos_embeddings.
    # ----------------------------------------------------------
    if not df_fuzzy_result.empty:
        fuzzy_pos_ids = df_fuzzy_result["pos_id"].dropna().unique().tolist()

        pos_details = execute_select_query(
            engine,
            text("""
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
                FROM transaction
                WHERE pos_id IN :pos_id_list
            """).bindparams(bindparam("pos_id_list", expanding=True)),
            {"pos_id_list": fuzzy_pos_ids},
        )

        df_fuzzy_result = df_fuzzy_result.merge(
            pos_details, on="pos_id", how="left"
        )

    # ----------------------------------------------------------
    # MERGE FUZZY RESULTS ONTO EXACT MATCH OUTPUT
    # ----------------------------------------------------------
    merged_df = pd.merge(
        classified_df,
        df_fuzzy_result,
        how="left",
        on="lead_id",
        suffixes=("_primary", "_fuzzy"),
    )

    merged_df["similarity_score_primary"] = merged_df["similarity_score_primary"].astype("float64")
    merged_df["similarity_score_fuzzy"]   = merged_df["similarity_score_fuzzy"].astype("float64")

    # Rows where fuzzy found a better match than exact
    update_mask = (
        (merged_df["similarity_score_primary"] < merged_df["similarity_score_fuzzy"])
        & pd.notna(merged_df["pos_id_fuzzy"])
        & pd.notna(merged_df["similarity_score_fuzzy"])
    )

    # ----------------------------------------------------------
    # UPDATE ALL POS FIELDS WHERE FUZZY BEATS EXACT
    # Every field in the final output schema that comes from the
    # POS side must be updated here so the output row is fully
    # POS-dominant from the winning fuzzy transaction.
    #
    # NOTE: your fuzzy SQL query must SELECT all of these columns.
    # ----------------------------------------------------------

    # Core match
    merged_df.loc[update_mask, "pos_id_primary"]            = merged_df.loc[update_mask, "pos_id_fuzzy"]
    merged_df.loc[update_mask, "similarity_score_primary"]  = merged_df.loc[update_mask, "similarity_score_fuzzy"]
    merged_df.loc[update_mask, "match_type"]                = "Fuzzy"

    # Transaction / fiscal
    merged_df.loc[update_mask, "account_number_primary"]    = merged_df.loc[update_mask, "account_number_fuzzy"].astype("float64")
    merged_df.loc[update_mask, "fiscal_year_transaction"]   = merged_df.loc[update_mask, "fiscal_year"].astype("float64")
    merged_df.loc[update_mask, "fiscal_period_transaction"] = merged_df.loc[update_mask, "fiscal_period"].astype("float64")
    merged_df.loc[update_mask, "week_primary"]              = merged_df.loc[update_mask, "week_fuzzy"].astype("float64")
    merged_df.loc[update_mask, "warehouse_number_primary"]  = merged_df.loc[update_mask, "warehouse_number_fuzzy"].astype("float64")
    merged_df.loc[update_mask, "sales_reference_id"]        = merged_df.loc[update_mask, "sales_reference_id_fuzzy"]
    merged_df.loc[update_mask, "shop_type"]                 = merged_df.loc[update_mask, "shop_type_fuzzy"]
    merged_df.loc[update_mask, "membership_number"]         = merged_df.loc[update_mask, "membership_number_fuzzy"]
    merged_df.loc[update_mask, "order_amount"]              = merged_df.loc[update_mask, "order_amount_fuzzy"]
    merged_df.loc[update_mask, "bd_industry"]               = merged_df.loc[update_mask, "bd_industry_fuzzy"]
    merged_df.loc[update_mask, "industry_description"]      = merged_df.loc[update_mask, "industry_description_fuzzy"]

    # POS business name
    merged_df.loc[update_mask, "business_name_transaction"] = merged_df.loc[update_mask, "business_name_fuzzy"]

    # POS customer details
    merged_df.loc[update_mask, "first_name"]                = merged_df.loc[update_mask, "first_name_fuzzy"]
    merged_df.loc[update_mask, "last_name"]                 = merged_df.loc[update_mask, "last_name_fuzzy"]
    merged_df.loc[update_mask, "address_line_one"]          = merged_df.loc[update_mask, "address_line_one_fuzzy"]
    merged_df.loc[update_mask, "address_line_two"]          = merged_df.loc[update_mask, "address_line_two_fuzzy"]
    merged_df.loc[update_mask, "city"]                      = merged_df.loc[update_mask, "city_fuzzy"]
    merged_df.loc[update_mask, "state"]                     = merged_df.loc[update_mask, "state_fuzzy"]
    merged_df.loc[update_mask, "zip_code"]                  = merged_df.loc[update_mask, "zip_code_fuzzy"]
    merged_df.loc[update_mask, "email"]                     = merged_df.loc[update_mask, "email_fuzzy"]
    merged_df.loc[update_mask, "phone"]                     = merged_df.loc[update_mask, "phone_fuzzy"]

    # After the merge, score columns have _fuzzy suffix on the fuzzy side.
    # Copy them into plain-named columns for the comment builder:
    # - fuzzy rows  -> take from the _fuzzy suffixed column
    # - exact rows  -> set to None (comment builder skips them)
    for score_col in ["combined_field_score", "full_address_score", "business_name_score"]:
        fuzzy_col = f"{score_col}_fuzzy"
        merged_df[score_col] = None
        if fuzzy_col in merged_df.columns:
            merged_df.loc[update_mask, score_col] = merged_df.loc[update_mask, fuzzy_col]

    # ----------------------------------------------------------
    # DROP ALL _fuzzy COLUMNS + NORMALISE _primary NAMES
    # ----------------------------------------------------------
    fuzzy_cols = [c for c in merged_df.columns if c.endswith("_fuzzy")]
    merged_df.drop(
        columns=fuzzy_cols + ["fiscal_year", "fiscal_period"],
        inplace=True,
        errors="ignore",
    )
    merged_df.columns = merged_df.columns.str.replace(r"_primary$", "", regex=True)

    # ----------------------------------------------------------
    # RE-APPLY MATCH RESULT FROM CONFIG TABLE
    # Covers both exact rows passed through and fuzzy-updated rows
    # ----------------------------------------------------------
    match_configuration_df = execute_select_query(
        engine, text(query_match_configuration)
    )
    merged_df["match_result"] = merged_df["similarity_score"].apply(
        lambda x: get_confidence_level(x, match_configuration_df)
    )

    # ----------------------------------------------------------
    # PRIMARY TRANSACTION LOGIC
    # Earliest Match transaction per lead is flagged primary
    # ----------------------------------------------------------
    merged_df = merged_df.sort_values(
        by=["lead_id", "fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    merged_df["primary_transaction"] = False
    merged_df["rank"] = merged_df.groupby("lead_id").cumcount() + 1

    high_conf_df = merged_df[merged_df["match_result"] == "Match"].copy()
    high_conf_df["primary_transaction"] = high_conf_df["rank"] == 1
    high_conf_leads = high_conf_df["lead_id"].unique()

    non_high_df = merged_df[~merged_df["lead_id"].isin(high_conf_leads)]
    merged_df   = pd.concat([high_conf_df, non_high_df], ignore_index=True)
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
    merged_df.loc[merged_df["match_result"] == "No Match", "pos_id"] = ""
    merged_df["account_number"] = merged_df["account_number"].fillna(0)

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