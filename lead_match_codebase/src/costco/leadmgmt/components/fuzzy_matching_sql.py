import pandas as pd
from sqlalchemy import text, bindparam
from datetime import datetime
import json
import os

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs, process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


# ==============================================================
# RULE SET LOADER (IMPORTS FROM SHARED BUSINESS RULES)
# ==============================================================
from lead_match_runtime.business_rules import (
    load_business_rules,
    closed_existing_lifecycle_state as _closed_existing_state,
    exact_lifecycle_state as _exact_lifecycle_state,
    exact_match_types as _exact_match_types,
    fuzzy_lifecycle_state_label as _fuzzy_lifecycle_label,
    no_match_lifecycle_state as _no_match_state,
)


# ==============================================================
# MATCHING COMMENT BUILDER — FUZZY
# ==============================================================
def build_fuzzy_matching_comment(row: pd.Series, rules: dict) -> str:
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
    if match_type in _exact_match_types(rules, lower=False):
        return row.get("matching_comments", "")

    parts = []

    # -- Result classification --------------------------------
    exact_state = _exact_lifecycle_state(rules)
    fuzzy_state = _fuzzy_lifecycle_label(rules)
    ce_state = _closed_existing_state(rules)

    if result in [exact_state, "Complete", "Match"]:
        parts.append(
            f"Fuzzy match — complete confidence "
            f"(similarity score: {similarity_score})."
        )
    elif result in [fuzzy_state, "Review"]:
        parts.append(
            f"Fuzzy match — potential confidence "
            f"(similarity score: {similarity_score}); "
            f"Marketer review recommended."
        )
    elif result == ce_state:
        parts.append(
            f"Fuzzy match — pre-existing transacting business "
            f"(similarity score: {similarity_score})."
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


def classify_row_match(row: pd.Series, rules: dict) -> pd.Series:
    """
    Processes a lead-to-POS candidate pair to apply the dynamic
    confidence bands and Costco fiscal year/period/week attribution rules.
    """
    score = row.get("similarity_score")
    if pd.isna(score) or score is None:
        score = 0.0
    score = float(score)

    match_type = row.get("match_type", "")

    # 1. Resolve qualification thresholds from JSON config
    dr = rules.get("decision_rules", {})
    qualify_min_score = float(dr["fuzzy_qualify_min_score"])
    exact_min_score = float(dr["exact_score"])
    no_match_state = _no_match_state(rules)
    fuzzy_lifecycle = _fuzzy_lifecycle_label(rules)
    exact_types = list(_exact_match_types(rules, lower=False))

    is_exact = (match_type in exact_types)
    min_score = exact_min_score if is_exact else qualify_min_score

    if score < min_score:
        row["match_result"] = no_match_state
        row["confidence_band"] = no_match_state
        row["closed_existing_flag"] = False
        return row

    # 2. Map score to confidence band using JSON subtiers (display-only, all lifecycle = Potential)
    subtiers = dr.get("optional_confidence_subtiers", {}).get("subtiers", [])
    band_name = fuzzy_lifecycle
    band_state = fuzzy_lifecycle
    for s in sorted(subtiers, key=lambda x: float(x.get("min_score", 0)), reverse=True):
        if float(s["min_score"]) <= score <= float(s["max_score"]):
            band_name = s["name"]
            break

    # 3. Retrieve Lead Fiscal values
    l_yr = row.get("fiscal_year_primary")
    if pd.isna(l_yr) or l_yr is None:
        l_yr = row.get("fiscal_year_lead")
    if pd.isna(l_yr) or l_yr is None:
        l_yr = row.get("fiscal_year")

    l_per = row.get("fiscal_period_primary")
    if pd.isna(l_per) or l_per is None:
        l_per = row.get("fiscal_period_lead")
    if pd.isna(l_per) or l_per is None:
        l_per = row.get("fiscal_period")

    l_wk = row.get("week_primary")
    if pd.isna(l_wk) or l_wk is None:
        l_wk = row.get("week_lead")
    if pd.isna(l_wk) or l_wk is None:
        l_wk = row.get("week")

    # 4. Retrieve POS Transaction Fiscal values
    t_yr = row.get("fiscal_year_transaction")
    if pd.isna(t_yr) or t_yr is None:
        t_yr = row.get("fiscal_year_fuzzy")
    if pd.isna(t_yr) or t_yr is None:
        t_yr = row.get("fiscal_year")

    t_per = row.get("fiscal_period_transaction")
    if pd.isna(t_per) or t_per is None:
        t_per = row.get("fiscal_period_fuzzy")
    if pd.isna(t_per) or t_per is None:
        t_per = row.get("fiscal_period")

    t_wk = row.get("week_fuzzy")
    if pd.isna(t_wk) or t_wk is None:
        t_wk = row.get("week")

    # Convert safely to integer values
    try:
        l_yr = int(float(l_yr)) if pd.notna(l_yr) else 0
        l_per = int(float(l_per)) if pd.notna(l_per) else 0
        l_wk = int(float(l_wk)) if pd.notna(l_wk) else 1
    except Exception:
        l_yr, l_per, l_wk = 0, 0, 1

    try:
        t_yr = int(float(t_yr)) if pd.notna(t_yr) else 0
        t_per = int(float(t_per)) if pd.notna(t_per) else 0
        t_wk = int(float(t_wk)) if pd.notna(t_wk) else 1
    except Exception:
        t_yr, t_per, t_wk = 0, 0, 1

    # 5. Check if POS transaction predates the lead: fiscal_year -> fiscal_period -> week
    is_predating = False
    if t_yr < l_yr:
        is_predating = True
    elif t_yr == l_yr:
        if t_per < l_per:
            is_predating = True
        elif t_per == l_per:
            if t_wk < l_wk:
                is_predating = True

    # 6. Set results based on fiscal ordering
    ce_state = _closed_existing_state(rules)
    if is_predating:
        row["match_result"] = ce_state
        row["confidence_band"] = band_name
        row["closed_existing_flag"] = True
    else:
        row["closed_existing_flag"] = False
        row["match_result"] = band_state
        row["confidence_band"] = band_name

    return row


def is_qualifying_match(match_result, rules):
    qualifying = {
        _exact_lifecycle_state(rules),
        _closed_existing_state(rules),
        _fuzzy_lifecycle_label(rules),
    }
    return match_result in qualifying


# ==============================================================
# FUZZY MATCHING MAIN PIPELINE
# ==============================================================
def fuzzy_matching(file_classified_path: str, config_file_path: str) -> str:

    # ----------------------------------------------------------
    # INITIALIZATION & RULES LOADING
    # ----------------------------------------------------------
    rules = load_business_rules()
    dr = rules["decision_rules"]
    qualify_min_score = float(dr["fuzzy_qualify_min_score"])
    exact_min_score = float(dr["exact_score"])
    ambiguity_delta = float(rules["resolution"]["ambiguity_delta"])
    match_type_semantic = str(rules["override_policy"]["semantic_match_type"])

    job_config     = JobConfig(config_file_path)
    db_config      = job_config.db_config
    storage_config = job_config.storage_config
    query_config   = job_config.match_query

    source_bucket_name        = storage_config.source_bucket_name
    source_folder             = storage_config.source_folder_output
    destination_bucket_name   = storage_config.destination_bucket_name
    destination_folder        = storage_config.destination_folder_output

    query_fuzzy_wh            = query_config.query_fuzzy_wh
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
    classified_df["similarity_score"] = pd.to_numeric(
        classified_df.get("similarity_score", 0), errors="coerce"
    ).fillna(0.0)

    exact_qualified_leads = set(
        classified_df.loc[
            classified_df["similarity_score"] >= exact_min_score,
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

    # Leads without a warehouse are intentionally not sent through semantic matching.
    # Same-warehouse blocking is mandatory before scoring: lead.warehouse_number = pos.warehouse_number.

    if master_df.empty:
        df_fuzzy_result = pd.DataFrame(
            columns=[
                "lead_id", "pos_id", "similarity_score", "combined_field_score",
                "full_address_score", "business_name_score", "account_number",
                "fiscal_year", "fiscal_period", "week", "warehouse_number",
                "business_name", "manual_review_reason",
            ]
        )
    else:
        # ----------------------------------------------------------
        # COMPUTE FUZZY SIMILARITY SCORE (PRECISION SCORE FORMULA)
        # ----------------------------------------------------------
        # Load weights from JSON config (address_variant / name_variant)
        embeddings_cfg = rules["embeddings"]["fields"]
        addr_weight = float(embeddings_cfg["address_variant"]["weight"])
        name_weight = float(embeddings_cfg["name_variant"]["weight"])
        total_weight = addr_weight + name_weight

        # (addr_weight * full_address_score + name_weight * business_name_score) / total_weight
        master_df["similarity_score"] = (
            (addr_weight * master_df["full_address_score"]
             + name_weight * master_df["business_name_score"]) / total_weight
        )

        df_fuzzy_result = master_df[
            master_df["similarity_score"] >= qualify_min_score
        ].copy()
        df_fuzzy_result = df_fuzzy_result[
            ~df_fuzzy_result["lead_id"].astype(str).isin(exact_qualified_leads)
        ]

        # POS-to-lead one-to-one assignment and near-tie manual review routing
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
            & ((df_fuzzy_result["similarity_score"] - df_fuzzy_result["next_pos_score"]) <= ambiguity_delta)
        )
        df_fuzzy_result.loc[ambiguous_mask, "manual_review_reason"] = "ambiguous_pos_candidate"
        df_fuzzy_result = (
            df_fuzzy_result
            .drop_duplicates(subset=["pos_id"], keep="first")
        )

    # ----------------------------------------------------------
    # KEEP FUZZY RESULT SLIM
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

    # Exact qualified rows stand authoritative. Semantic only recovers misses.
    exact_qualified_mask = merged_df["similarity_score_primary"] >= exact_min_score
    update_mask = (
        ~exact_qualified_mask
        & pd.notna(merged_df["pos_id_fuzzy"])
        & pd.notna(merged_df["similarity_score_fuzzy"])
        & (merged_df["similarity_score_fuzzy"] >= qualify_min_score)
    )
    merged_df["_fuzzy_update"] = update_mask

    print("=== COLUMNS AFTER MERGE ===")
    print(merged_df.columns.tolist())
    print(f"Rows to update with semantic match: {update_mask.sum()}")

    # ----------------------------------------------------------
    # UPDATE CORE MATCH FIELDS WHERE FUZZY BEATS EXACT
    # ----------------------------------------------------------
    merged_df.loc[update_mask, "pos_id_primary"]            = merged_df.loc[update_mask, "pos_id_fuzzy"]
    merged_df.loc[update_mask, "similarity_score_primary"]  = merged_df.loc[update_mask, "similarity_score_fuzzy"]
    merged_df.loc[update_mask, "match_type"]                = match_type_semantic

    if "manual_review_reason" in merged_df.columns:
        review_mask = update_mask & merged_df["manual_review_reason"].notna()
        merged_df.loc[review_mask, "match_type"] = str(rules["resolution"]["ambiguity_match_type"])
        merged_df.loc[review_mask, "match_result"] = _fuzzy_lifecycle_label(rules)

    merged_df.loc[update_mask, "account_number_primary"]    = pd.to_numeric(merged_df.loc[update_mask, "account_number_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "fiscal_year_transaction"]   = pd.to_numeric(merged_df.loc[update_mask, "fiscal_year"], errors="coerce")
    merged_df.loc[update_mask, "fiscal_period_transaction"] = pd.to_numeric(merged_df.loc[update_mask, "fiscal_period"], errors="coerce")
    merged_df.loc[update_mask, "week_primary"]              = pd.to_numeric(merged_df.loc[update_mask, "week_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "warehouse_number_primary"]  = pd.to_numeric(merged_df.loc[update_mask, "warehouse_number_fuzzy"], errors="coerce")
    merged_df.loc[update_mask, "business_name_transaction"] = merged_df.loc[update_mask, "business_name_fuzzy"]

    # ----------------------------------------------------------
    # FIX SCORE COLUMNS FOR COMMENT BUILDER
    # ----------------------------------------------------------
    rename_scores = {
        "combined_field_score_fuzzy": "combined_field_score",
        "full_address_score_fuzzy":   "full_address_score",
        "business_name_score_fuzzy":  "business_name_score",
    }
    merged_df.rename(columns=rename_scores, inplace=True)

    # Drop the _primary score cols
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
    # DROP REMAINING _fuzzy COLUMNS + NORMALISE NAMES
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
    # FETCH CUSTOMER DETAIL FIELDS FOR FUZZY-UPDATED ROWS
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

        merged_df = merged_df.merge(
            pos_details, on="pos_id", how="left", suffixes=("", "_new")
        )
        update_mask = merged_df["_fuzzy_update"].fillna(False)

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
    # RE-APPLY MATCH RESULT (CONFIDENCE BANDS & FISCAL ATTRIBUTION)
    # Applied to all rows (exact passed through + fuzzy-updated)
    # to enforce correct same-warehouse, fiscal window attribution.
    # ----------------------------------------------------------
    print("[INFO] Classifying all matches against fiscal attribution rules...")
    merged_df = merged_df.apply(lambda row: classify_row_match(row, rules), axis=1)

    # ----------------------------------------------------------
    # PRIMARY TRANSACTION LOGIC
    # ----------------------------------------------------------
    # Earliest qualifying transaction per lead is flagged primary.
    # Sorted by: lead_id, fiscal_year_transaction, fiscal_period_transaction, week.
    merged_df = merged_df.sort_values(
        by=["lead_id", "fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    merged_df["primary_transaction"] = False

    qualifying_mask = merged_df["match_result"].apply(lambda r: is_qualifying_match(r, rules))
    if qualifying_mask.any():
        merged_df.loc[qualifying_mask, "primary_transaction"] = (
            merged_df[qualifying_mask].groupby("lead_id").cumcount() == 0
        )

    # ----------------------------------------------------------
    # MATCHING COMMENTS
    # ----------------------------------------------------------
    merged_df["matching_comments"] = merged_df.apply(
        lambda row: build_fuzzy_matching_comment(row, rules), axis=1
    )

    # Drop intermediate scores
    merged_df.drop(
        columns=["combined_field_score", "full_address_score", "business_name_score"],
        inplace=True,
        errors="ignore",
    )

    # ----------------------------------------------------------
    # HANDLE NO MATCH ROWS
    # ----------------------------------------------------------
    no_match_label = _no_match_state(rules)
    no_match_mask = merged_df["match_result"] == no_match_label
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
    # FINAL OUTPUT SCHEMA
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
        "confidence_band",
        "closed_existing_flag",

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

    print(f"Fuzzy match output successfully written to: {uri}")
    return uri
