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
# Originals (e.g. business_name, address_line_one) are preserved
# in the dataframe and used for the ServiceNow payload.
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


def _friendly(field: str) -> str:
    """Strip the _normalized suffix for human-readable output."""
    return field.replace("_normalized", "")


# ==============================================================
# MATCHING COMMENT BUILDER
# ==============================================================
def build_matching_comment(row: pd.Series) -> str:
    """
    Constructs a human-readable comment explaining which fields
    drove the match and what the result classification means.

    Architecture note: GCP owns matching logic; ServiceNow receives
    this comment as part of the confirmed match record payload so
    agents can understand why the system matched a lead to a POS
    transaction without re-running the algorithm.
    """
    score        = row["similarity_score"]
    result       = row["match_result"]
    matched_keys = row.get("matched_key_fields", [])
    matched_supp = row.get("matched_supp_fields", [])

    parts = []

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
# SCORE ONE GROUP
# Runs Phase 1 + Phase 2 for a single POS slice against the
# currently active (unresolved) leads.
# ==============================================================
def _score_group(
    sales_slice: pd.DataFrame,     # POS rows for this fiscal group
    active_leads: pd.DataFrame,    # leads not yet resolved as CE
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Runs Phase 1 (key field) and Phase 2 (supplementary field)
    scoring between a single chronological POS group and the
    currently active leads.

    Returns:
        qualified     — DataFrame[lead_id, pos_id, similarity_score]
                        with score >= MINIMUM_SCORE
        key_matches   — dict[field -> DataFrame[lead_id, pos_id]]
                        for matched-field tracking
        supp_matches  — dict[field -> DataFrame[lead_id, pos_id]]
    """
    leads_small = active_leads[
        ["lead_id", "warehouse_number"] + ALL_FIELDS
    ].copy()

    sales_small = sales_slice[
        ["pos_id", "warehouse_number"] + ALL_FIELDS
    ].copy()

    score_frames     = []
    key_match_frames = {}

    # -- Phase 1: key fields ----------------------------------
    for field, score in KEY_FIELDS.items():
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
        key_match_frames[field] = matched[["lead_id", "pos_id"]].copy()

    if not score_frames:
        empty = pd.DataFrame(columns=["lead_id", "pos_id", "similarity_score"])
        return empty, {}, {}

    candidate_pairs = (
        pd.concat(score_frames, ignore_index=True)
        [["lead_id", "pos_id"]]
        .drop_duplicates()
    )

    # -- Phase 2: supplementary fields ------------------------
    leads_supp = (
        active_leads[["lead_id"] + list(SUPPLEMENTARY_FIELDS.keys())]
        .drop_duplicates(subset=["lead_id"])
    )
    sales_supp = (
        sales_slice[["pos_id"] + list(SUPPLEMENTARY_FIELDS.keys())]
        .drop_duplicates(subset=["pos_id"])
    )

    candidates_with_data = (
        candidate_pairs
        .merge(leads_supp, on="lead_id", how="left")
        .merge(sales_supp, on="pos_id", how="left", suffixes=("_lead", "_sale"))
    )

    supp_match_frames = {}

    for field, score in SUPPLEMENTARY_FIELDS.items():
        lead_col = f"{field}_lead"
        sale_col = f"{field}_sale"

        mask = (
            candidates_with_data[lead_col].notna()
            & candidates_with_data[sale_col].notna()
            & (candidates_with_data[lead_col] == candidates_with_data[sale_col])
        )
        supp = candidates_with_data.loc[mask, ["lead_id", "pos_id"]].copy()

        if supp.empty:
            continue

        supp["score"] = score
        score_frames.append(supp)
        supp_match_frames[field] = supp[["lead_id", "pos_id"]].copy()

    # -- Aggregate & threshold --------------------------------
    pair_scores = (
        pd.concat(score_frames, ignore_index=True)
        .groupby(["lead_id", "pos_id"], as_index=False)["score"]
        .sum()
        .rename(columns={"score": "similarity_score"})
    )

    qualified = (
        pair_scores[pair_scores["similarity_score"] >= MINIMUM_SCORE]
        .drop_duplicates(subset=["lead_id", "pos_id"])
        .copy()
    )

    return qualified, key_match_frames, supp_match_frames


# ==============================================================
# OUTPUT COLUMNS
# ==============================================================
def _output_columns() -> list[str]:
    return [
        # Matching / classification
        "lead_id",
        "pos_id",
        "match_result",
        "similarity_score",
        "match_type",
        "primary_transaction",
        "matched_by",
        "matching_comments",
        "closed_existing_flag",

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
    ]


# ==============================================================
# CLASSIFY MATCHES
# ==============================================================
def classify_matches(
    file_leads: pd.DataFrame,
    file_sales: pd.DataFrame,
) -> pd.DataFrame:
    """
    Chronological matching with Closed-Existing detection.

    For each (fiscal_year, fiscal_period, week) POS group in
    chronological order:
      1. Run Phase 1 + Phase 2 scoring against currently active
         (unresolved) leads.
      2. For each qualified pair, check if the transaction is
         strictly before the lead's fiscal year/period.
      3. If yes → mark the lead Closed-Existing, remove it from
         the active set, generate a stub row (lead_id +
         closed_existing_flag only), and skip it forever.
      4. Otherwise, the pair survives to the normal Match /
         Potential path.

    Because CE detection happens chronologically inside the loop,
    no separate fiscal filter is needed downstream — any
    prior-period match would have triggered CE before reaching
    the normal path.
    """
    leads = preprocess(file_leads)
    sales = preprocess(file_sales)

    # Warehouse pre-filter
    lead_warehouses = leads["warehouse_number"].dropna().unique()
    sales_before = len(sales)
    sales = sales[sales["warehouse_number"].isin(lead_warehouses)].copy()
    log.info(
        "Warehouse pre-filter: %d → %d POS rows (%d dropped)",
        sales_before, len(sales), sales_before - len(sales),
    )

    if sales.empty:
        log.warning("No POS rows share a warehouse with any lead.")
        return pd.DataFrame(columns=_output_columns())

    # Lead fiscal lookup — used for CE check
    lead_fiscal = (
        leads[["lead_id", "fiscal_year_lead", "fiscal_period_lead"]]
        .drop_duplicates(subset=["lead_id"])
        .set_index("lead_id")
    )

    # Sort POS into chronological groups
    sales_sorted = sales.sort_values(
        by=["fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    groups = sales_sorted.groupby(
        ["fiscal_year_transaction", "fiscal_period_transaction", "week"],
        sort=False,   # already sorted above
    )

    group_keys = list(groups.groups.keys())
    log.info("Processing %d chronological POS groups", len(group_keys))

    # State tracking across groups
    ce_lead_ids        = set()   # resolved as Closed-Existing
    normal_pair_frames = []      # qualified pairs surviving for Match/Potential
    key_frames_all     = {}      # field -> list of DataFrames (normal path only)
    supp_frames_all    = {}      # field -> list of DataFrames (normal path only)

    # Active leads shrinks as leads get resolved
    active_lead_ids = set(leads["lead_id"].unique())

    for gkey in group_keys:
        fy, fp, wk = gkey

        if not active_lead_ids:
            log.info(
                "All leads resolved — stopping early at group %s/%s/wk%s",
                fy, fp, wk,
            )
            break

        sales_slice  = groups.get_group(gkey)
        active_leads = leads[leads["lead_id"].isin(active_lead_ids)].copy()

        qualified, key_matches, supp_matches = _score_group(
            sales_slice, active_leads
        )

        if qualified.empty:
            log.debug(
                "Group %s/%s/wk%s — no qualified pairs", fy, fp, wk
            )
            continue

        # Attach transaction fiscal columns for CE check
        qualified["fiscal_year_transaction"]   = fy
        qualified["fiscal_period_transaction"] = fp
        qualified["week"]                      = wk

        # CE check: is this transaction prior to the lead?
        qualified = qualified.join(lead_fiscal, on="lead_id", how="left")

        prior_mask = (
            (qualified["fiscal_year_transaction"] < qualified["fiscal_year_lead"])
            | (
                (qualified["fiscal_year_transaction"] == qualified["fiscal_year_lead"])
                & (qualified["fiscal_period_transaction"] < qualified["fiscal_period_lead"])
            )
        )

        # Leads matched to a prior-period transaction in this group
        new_ce = set(qualified.loc[prior_mask, "lead_id"].unique())

        if new_ce:
            ce_lead_ids.update(new_ce)
            active_lead_ids -= new_ce
            log.info(
                "Group %s/%s/wk%s — %d new CE lead(s): %s",
                fy, fp, wk, len(new_ce),
                list(new_ce)[:10],   # cap log length
            )

        # Keep only pairs where the lead is NOT CE
        # (a group can have both CE and non-CE pairs simultaneously)
        surviving = qualified[~qualified["lead_id"].isin(new_ce)].copy()

        if surviving.empty:
            continue

        normal_pair_frames.append(
            surviving[[
                "lead_id", "pos_id", "similarity_score",
                "fiscal_year_transaction", "fiscal_period_transaction", "week",
            ]].copy()
        )

        # Accumulate matched-field frames, restricted to surviving leads
        surviving_ids = set(surviving["lead_id"].unique())

        for field, frame in key_matches.items():
            kept = frame[frame["lead_id"].isin(surviving_ids)]
            if not kept.empty:
                key_frames_all.setdefault(field, []).append(kept)

        for field, frame in supp_matches.items():
            kept = frame[frame["lead_id"].isin(surviving_ids)]
            if not kept.empty:
                supp_frames_all.setdefault(field, []).append(kept)

    log.info(
        "Chronological pass complete — CE leads: %d | normal pair batches: %d",
        len(ce_lead_ids), len(normal_pair_frames),
    )

    # ==========================================================
    # BUILD CE STUB ROWS
    # One row per CE lead — lead_id + flag only, everything else null.
    # ==========================================================
    ce_stubs = (
        pd.DataFrame({"lead_id": list(ce_lead_ids), "closed_existing_flag": True})
        if ce_lead_ids
        else pd.DataFrame(columns=["lead_id", "closed_existing_flag"])
    )

    # ==========================================================
    # NORMAL PATH
    # ==========================================================
    out_cols = _output_columns()

    if not normal_pair_frames:
        log.warning("No normal pairs to process.")
        final_df = ce_stubs.reindex(columns=out_cols)
        log.info("Final output — CE stubs only: %d", len(final_df))
        return final_df

    normal_qualified = (
        pd.concat(normal_pair_frames, ignore_index=True)
        .drop_duplicates(subset=["lead_id", "pos_id"])
    )

    # Re-attach lead fiscal columns needed downstream (matching comments,
    # primary transaction sort). No fiscal filter applied here — the
    # chronological group loop already guaranteed that any lead whose
    # first matched transaction was prior-period was resolved as CE and
    # removed from active_lead_ids before reaching this path.
    normal_qualified = normal_qualified.join(lead_fiscal, on="lead_id", how="inner")

    log.info("Normal pairs carried forward: %d", len(normal_qualified))

    # ==========================================================
    # MATCHED FIELD TRACKING
    # ==========================================================
    def _consolidate_field_frames(frames_dict):
        result = {}
        for field, frame_list in frames_dict.items():
            combined = (
                pd.concat(frame_list, ignore_index=True)
                .drop_duplicates(subset=["lead_id", "pos_id"])
            )
            # Restrict to pairs that survived
            combined = combined.merge(
                normal_qualified[["lead_id", "pos_id"]],
                on=["lead_id", "pos_id"],
                how="inner",
            )
            if not combined.empty:
                result[field] = combined
        return result

    key_frames_final  = _consolidate_field_frames(key_frames_all)
    supp_frames_final = _consolidate_field_frames(supp_frames_all)

    if key_frames_final:
        key_field_map = (
            pd.concat(
                [f.assign(field=fn) for fn, f in key_frames_final.items()],
                ignore_index=True,
            )
            .groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_key_fields")
        )
        normal_qualified = normal_qualified.merge(
            key_field_map, on=["lead_id", "pos_id"], how="left"
        )
    else:
        normal_qualified["matched_key_fields"] = [[]] * len(normal_qualified)

    normal_qualified["matched_key_fields"] = normal_qualified[
        "matched_key_fields"
    ].apply(lambda x: x if isinstance(x, list) else [])

    if supp_frames_final:
        supp_field_map = (
            pd.concat(
                [f.assign(field=fn) for fn, f in supp_frames_final.items()],
                ignore_index=True,
            )
            .groupby(["lead_id", "pos_id"])["field"]
            .apply(list)
            .rename("matched_supp_fields")
        )
        normal_qualified = normal_qualified.merge(
            supp_field_map, on=["lead_id", "pos_id"], how="left"
        )
    else:
        normal_qualified["matched_supp_fields"] = [[]] * len(normal_qualified)

    normal_qualified["matched_supp_fields"] = normal_qualified[
        "matched_supp_fields"
    ].apply(lambda x: x if isinstance(x, list) else [])

    # ==========================================================
    # BUILD SUBSETS FOR FINAL MERGE
    # Pull ORIGINAL columns (not normalized) so the ServiceNow
    # payload carries original casing and special characters.
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
            "business_name_transaction",
            "membership_number",
            "warehouse_number",
            "fiscal_year_transaction",
            "fiscal_period_transaction",
            "week",
            "shop_type",
            "sales_reference_id",
            "order_amount",
            "bd_industry",
            "first_name",
            "last_name",
            "address_line_one",
            "address_line_two",
            "city",
            "state",
            "zip_code",
            "email",
            "phone",
            "industry_description",
        ]]
    )

    matched_df = (
        normal_qualified
        .merge(lead_subset,  on="lead_id", how="inner")
        .merge(sales_subset, on="pos_id",  how="inner")
    )

    # ==========================================================
    # ASSIGN MATCH RESULT
    # ==========================================================
    matched_df["match_result"] = matched_df["similarity_score"].apply(
        lambda x: "Match" if x >= COMPLETE_SCORE else "Potential"
    )
    matched_df["match_type"]           = "Exact"
    matched_df["matched_by"]           = "System"
    matched_df["primary_transaction"]  = False
    matched_df["closed_existing_flag"] = False

    # ==========================================================
    # PRIMARY TRANSACTION LOGIC
    # ==========================================================
    match_only = matched_df[matched_df["match_result"] == "Match"].copy()
    match_only = match_only.sort_values(
        by=["lead_id", "fiscal_year_transaction",
            "fiscal_period_transaction", "week"],
        ascending=True,
    )
    match_only["rank"] = match_only.groupby("lead_id").cumcount() + 1
    primary_idx = match_only[match_only["rank"] == 1].index
    matched_df.loc[primary_idx, "primary_transaction"] = True

    # ==========================================================
    # MATCHING COMMENTS
    # ==========================================================
    matched_df["matching_comments"] = matched_df.apply(
        build_matching_comment, axis=1
    )

    # ==========================================================
    # SERVICENOW MAPPINGS
    # ==========================================================
    matched_df["u_matched_lead_number"]  = matched_df["lead_id"]
    matched_df["u_order_amount"]         = matched_df["order_amount"]
    matched_df["u_order_amount_rounded"] = (
        pd.to_numeric(matched_df["order_amount"], errors="coerce").round(2)
    )
    matched_df["updated_date"] = pd.to_datetime(datetime.now())

    # ==========================================================
    # ASSEMBLE FINAL OUTPUT
    # Normal matched rows + CE stub rows (with all POS/SN cols null)
    # ==========================================================
    for col in out_cols:
        if col not in matched_df.columns:
            matched_df[col] = None
    matched_df = matched_df[out_cols].copy()

    ce_stubs = ce_stubs.reindex(columns=out_cols)

    final_df = pd.concat(
        [f for f in [matched_df, ce_stubs] if not f.empty],
        ignore_index=True,
    )

    # Sort: CE stubs (NaN score) sink to the bottom via na_position
    final_df = (
        final_df
        .sort_values(
            by=["similarity_score", "primary_transaction", "match_result"],
            ascending=[False, False, True],
            na_position="last",
        )
        .drop_duplicates(subset=["lead_id", "pos_id"], keep="first")
    )

    log.info(
        "Final output — normal rows: %d | CE stubs: %d | total: %d",
        (~final_df["closed_existing_flag"].fillna(False)).sum(),
        final_df["closed_existing_flag"].fillna(False).sum(),
        len(final_df),
    )

    del matched_df, ce_stubs
    gc.collect()

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
        4. Delegates matching to ``classify_matches()``, which runs the
           chronological group-by-group scoring pass with Closed-Existing
           detection, followed by Match/Potential classification and
           primary transaction assignment.
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
        "zip_code":            str,
        "zip_code_normalized": str,
        "phone":               str,
        "phone_normalized":    str,
        "warehouse_number":    str,
        "membership_number":   str,
        "account_number":      str,
    }

    log.info("Loading leads file: %s", file_a_path)
    file_a = load_file_from_gcs(file_a_path, dtype=STRING_COLS)

    log.info("Loading POS file: %s", file_b_path)
    file_b = load_file_from_gcs(file_b_path, dtype=STRING_COLS)

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