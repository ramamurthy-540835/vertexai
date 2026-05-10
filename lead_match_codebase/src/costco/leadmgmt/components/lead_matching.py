import gc
from datetime import datetime

import pandas as pd
from sqlalchemy import MetaData, Table, insert

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import (
    load_file_from_gcs,
    process_and_archive_files
)


def classify_matches(
        file_leads: pd.DataFrame,
        file_sales: pd.DataFrame
) -> pd.DataFrame:

    KEY_FIELDS = {
        "business_name": 40,
        "address_line_one": 40,
        "email": 30,
        "phone": 20,
    }

    SUPPLEMENTARY_FIELDS = {
        "state": 5,
        "city": 5,
        "zip_code": 10,
    }

    MINIMUM_SCORE = 80

    ALL_FIELDS = (
        list(KEY_FIELDS.keys()) +
        list(SUPPLEMENTARY_FIELDS.keys())
    )

    # ==========================================================
    # PREPROCESS
    # ==========================================================
    def preprocess(df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy()

        df = df.dropna(subset=["warehouse_number"])

        df["warehouse_number"] = (
            pd.to_numeric(
                df["warehouse_number"],
                errors="coerce"
            )
            .astype("Int64")
            .astype(str)
        )

        df = df[df["warehouse_number"] != ""]

        for col in ALL_FIELDS:

            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .replace({
                    "nan": pd.NA,
                    "": pd.NA
                })
            )

        return df

    leads = preprocess(file_leads)
    sales = preprocess(file_sales)

    # ==========================================================
    # MINIMAL MATCHING DATAFRAMES
    # ==========================================================
    leads_small = leads[
        ["lead_id", "warehouse_number"] + ALL_FIELDS
    ]

    sales_small = sales[
        ["pos_id", "warehouse_number"] + ALL_FIELDS
    ]

    # ==========================================================
    # PHASE 1 - KEY FIELD MATCHING
    # ==========================================================
    score_frames = []

    for field, score in KEY_FIELDS.items():

        print(f"Processing key field: {field}")

        sales_valid = sales_small.dropna(
            subset=["warehouse_number", field]
        )

        leads_valid = leads_small.dropna(
            subset=["warehouse_number", field]
        )

        if sales_valid.empty or leads_valid.empty:
            continue

        matched = leads_valid.merge(
            sales_valid,
            on=["warehouse_number", field],
            how="inner",
            suffixes=("_lead", "_sale")
        )[["lead_id", "pos_id"]].drop_duplicates()

        if matched.empty:
            continue

        matched["score"] = score

        print(f"{field}: {len(matched)} matches")

        score_frames.append(matched)

    # ==========================================================
    # HANDLE NO MATCH
    # ==========================================================
    if not score_frames:

        return pd.DataFrame(columns=[
            "lead_id",
            "pos_id",
            "match_result",
            "similarity_score"
        ])

    # ==========================================================
    # CANDIDATE PAIRS
    # ==========================================================
    candidate_pairs = (
        pd.concat(score_frames, ignore_index=True)
        [["lead_id", "pos_id"]]
        .drop_duplicates()
    )

    print(f"Total candidate pairs: {len(candidate_pairs)}")

    # ==========================================================
    # PHASE 2 - SUPPLEMENTARY MATCHING
    # ==========================================================
    leads_supp = (
        leads[
            ["lead_id"] +
            list(SUPPLEMENTARY_FIELDS.keys())
        ]
        .drop_duplicates(subset=["lead_id"])
    )

    sales_supp = (
        sales[
            ["pos_id"] +
            list(SUPPLEMENTARY_FIELDS.keys())
        ]
        .drop_duplicates(subset=["pos_id"])
    )

    candidates_with_data = (
        candidate_pairs
        .merge(
            leads_supp,
            on="lead_id",
            how="left"
        )
        .merge(
            sales_supp,
            on="pos_id",
            how="left",
            suffixes=("_lead", "_sale")
        )
    )

    for field, score in SUPPLEMENTARY_FIELDS.items():

        lead_col = f"{field}_lead"
        sale_col = f"{field}_sale"

        supp_matches = candidates_with_data[
            (
                candidates_with_data[lead_col].notna()
            )
            &
            (
                candidates_with_data[sale_col].notna()
            )
            &
            (
                candidates_with_data[lead_col] ==
                candidates_with_data[sale_col]
            )
        ][["lead_id", "pos_id"]].copy()

        if supp_matches.empty:
            continue

        supp_matches["score"] = score

        print(
            f"{field}: "
            f"{len(supp_matches)} supplementary matches"
        )

        score_frames.append(supp_matches)

    # ==========================================================
    # AGGREGATE SCORES
    # ==========================================================
    all_scores = pd.concat(
        score_frames,
        ignore_index=True
    )

    pair_scores = (
        all_scores
        .groupby(
            ["lead_id", "pos_id"],
            as_index=False
        )["score"]
        .sum()
        .rename(
            columns={"score": "similarity_score"}
        )
    )

    # ==========================================================
    # FILTER QUALIFIED MATCHES
    # ==========================================================
    qualified = pair_scores[
        pair_scores["similarity_score"] >= MINIMUM_SCORE
    ]

    if qualified.empty:

        return pd.DataFrame(columns=[
            "lead_id",
            "pos_id",
            "match_result",
            "similarity_score"
        ])

    # ==========================================================
    # LEAD SUBSET
    # ==========================================================
    lead_subset = leads[
        [
            "lead_id",
            "updated_date",
            "fiscal_year_lead",
            "fiscal_period_lead"
        ]
    ]

    # ==========================================================
    # SALES SUBSET (POS DOMINANT)
    # ==========================================================
    sales = sales.rename(
        columns={
            "business_name": "business_name_transaction"
        }
    )

    sales_subset = sales[
        [
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

            # POS CUSTOMER DETAILS
            "first_name",
            "last_name",
            "address_line_one",
            "address_line_two",
            "city",
            "state",
            "zip_code",
            "email",
            "phone"
        ]
    ]

    # ==========================================================
    # BUILD MATCHED DATAFRAME
    # ==========================================================
    matched_df = (
        qualified
        .merge(
            lead_subset,
            on="lead_id",
            how="inner"
        )
        .merge(
            sales_subset,
            on="pos_id",
            how="inner"
        )
    )

    # ==========================================================
    # APPLY FISCAL FILTER
    # ==========================================================
    matched_df = matched_df[
        (
            matched_df["fiscal_year_lead"] <
            matched_df["fiscal_year_transaction"]
        )
        |
        (
            (
                matched_df["fiscal_year_lead"] ==
                matched_df["fiscal_year_transaction"]
            )
            &
            (
                matched_df["fiscal_period_lead"] <=
                matched_df["fiscal_period_transaction"]
            )
        )
    ]

    # ==========================================================
    # ASSIGN MATCH RESULT
    # ==========================================================
    matched_df["match_result"] = matched_df[
        "similarity_score"
    ].apply(
        lambda x:
        "Complete"
        if x >= 100
        else "Potential"
    )

    matched_df["match_type"] = "Exact"

    matched_df["matched_by"] = "System"

    matched_df["matching_comments"] = ""

    # ==========================================================
    # PRIMARY TRANSACTION LOGIC
    # ==========================================================
    matched_df = matched_df.sort_values(
        by=[
            "lead_id",
            "fiscal_year_transaction",
            "fiscal_period_transaction",
            "week"
        ],
        ascending=[True, True, True, True]
    )

    matched_df["rank"] = (
        matched_df
        .groupby("lead_id")
        .cumcount() + 1
    )

    matched_df["primary_transaction"] = (
        matched_df["rank"] == 1
    )

    matched_df.drop(
        columns=["rank"],
        inplace=True
    )

    # ==========================================================
    # FINAL OUTPUT
    # ==========================================================
    final_df = matched_df[

        [

            # MATCHING
            "lead_id",
            "pos_id",
            "match_result",
            "similarity_score",
            "match_type",
            "primary_transaction",
            "matched_by",
            "matching_comments",

            # POS DOMINANT
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

            # POS CUSTOMER DETAILS
            "first_name",
            "last_name",
            "address_line_one",
            "address_line_two",
            "city",
            "state",
            "zip_code",
            "email",
            "phone"

        ]

    ].copy()

    # ==========================================================
    # SERVICENOW MAPPINGS
    # ==========================================================
    final_df["u_matched_lead_number"] = final_df["lead_id"]

    final_df["u_order_amount"] = final_df["order_amount"]

    final_df["u_order_amount_rounded"] = (
        pd.to_numeric(
            final_df["order_amount"],
            errors="coerce"
        )
        .round(2)
    )

    # ==========================================================
    # UPDATED DATE
    # ==========================================================
    final_df["updated_date"] = pd.to_datetime(
        datetime.now()
    )

    # ==========================================================
    # DEDUPLICATION
    # ==========================================================
    final_df = final_df.drop_duplicates(
        subset=["lead_id", "pos_id"]
    )

    print(
        f"Final matched records: {len(final_df)}"
    )

    return final_df


def primary_classification(match_id: str, config_file_path: str, file_a_path: str = "", file_b_path: str = "") -> str:

    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    job_config = JobConfig(config_file_path)

    storage_config = job_config.storage_config
    db_config = job_config.db_config

    # ==========================================================
    # DEFAULT INPUT FILES
    # ==========================================================
    if file_a_path == "":
        file_a_path = storage_config.temp_leads_path

    if file_b_path == "":
        file_b_path = storage_config.temp_pos_path

    # ==========================================================
    # STORAGE CONFIG
    # ==========================================================
    source_bucket_name = (
        storage_config.source_bucket_name
    )

    source_folder = (
        storage_config.source_folder_output
    )

    destination_bucket_name = (
        storage_config.destination_bucket_name
    )

    destination_folder = (
        storage_config.destination_folder_output
    )

    # ==========================================================
    # DATABASE CONFIG
    # ==========================================================
    schema = db_config.schema_name

    table_name = db_config.audit_table_name

    # ==========================================================
    # ENGINE
    # ==========================================================
    engine = db_config.get_engine()

    metadata = MetaData()

    # ==========================================================
    # LOAD FILES
    # ==========================================================
    print(f"Loading leads file: {file_a_path}")

    file_a = load_file_from_gcs(file_a_path)

    print(f"Loading pos file: {file_b_path}")

    file_b = load_file_from_gcs(file_b_path)

    print(f"Lead count: {len(file_a)}")

    print(f"POS count: {len(file_b)}")

    # ==========================================================
    # AUDIT INSERT
    # ==========================================================
    user_table_obj = Table(
        table_name,
        metadata,
        autoload_with=engine,
        schema=schema
    )

    stmt = insert(user_table_obj).values(
        match_id=match_id,
        lead_count=len(file_a),
        pos_count=len(file_b),
        status="InProgress"
    )

    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()

    # ==========================================================
    # CLASSIFICATION
    # ==========================================================
    final_df = classify_matches(
        file_a,
        file_b
    )

    # ==========================================================
    # OUTPUT FILE NAME
    # ==========================================================
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    base_name = (
        f"primary_match_output_"
        f"{match_id}_"
        f"{timestamp}"
    )

    # ==========================================================
    # PROCESS + ARCHIVE
    # ==========================================================
    uri = process_and_archive_files(
        source_bucket_name,
        source_folder,
        destination_bucket_name,
        destination_folder,
        final_df,
        base_name
    )

    print(f"Final output written to: {uri}")

    # ==========================================================
    # CLEANUP
    # ==========================================================
    del file_a
    del file_b
    del final_df

    gc.collect()

    return uri