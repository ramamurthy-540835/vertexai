import sqlalchemy
from google.cloud import storage
import pandas as pd
from sqlalchemy import insert,MetaData,Table
from vertexai.agent_engines import update
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
import gc


# Function to clean data before merging (removes empty or NaN values)
def filter_for_merge(df, columns):
    for col in columns:
        df[col] = df[col].astype(str)
        df[col] = df[col].replace('nan', pd.NA)
    # Filter out rows where any of the specified columns are empty or NaN
    return df.dropna(subset=columns).loc[~df[columns].apply(lambda x: x.str.strip() == '', axis=1).any(axis=1)]


def format_df(df):
    df.columns = df.columns.str.replace(r'_leads$', '', regex=True)
    df = df.rename(columns={"business_name_sales":"business_name_transaction"})
    df = df[[col for col in df.columns if not col.endswith('_sales')]]
    df = df[['lead_id','membership_number' ,'warehouse_number' ,'updated_date' ,
             'fiscal_year_lead','fiscal_period_lead','business_name' ,'address_line_one' ,
             'address_line_two' ,'city' ,'state' ,'zip_code' ,'phone' ,'first_name' ,
             'last_name' ,'email' ,'COMBINED_FIELD' ,'FULL_ADDRESS' ,'CUSTOMER_NAME' ,
             'pos_id','account_number','fiscal_year_transaction','fiscal_period_transaction','week',
             'business_name_transaction']]
    return df


def classify_matches(file_leads, file_sales):

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

    all_fields = list(KEY_FIELDS.keys()) + list(SUPPLEMENTARY_FIELDS.keys())

    def preprocess(df):
        df = df.copy()
        df = df.dropna(subset=["warehouse_number"])
        df["warehouse_number"] = (
            pd.to_numeric(df["warehouse_number"], errors="coerce")
            .astype("Int64")
            .astype(str)
        )
        df = df[df["warehouse_number"] != ""]
        for col in all_fields:
            df[col] = df[col].astype(str).str.strip().replace({'nan': pd.NA, '': pd.NA})
        return df

    leads = preprocess(file_leads)
    sales = preprocess(file_sales)

    leads_small = leads[["lead_id", "warehouse_number"] + all_fields]
    sales_small = sales[["pos_id", "warehouse_number"] + all_fields]

    # -------------------------------
    # PHASE 1: Key fields only
    # -------------------------------
    score_frames = []

    for field, score in KEY_FIELDS.items():
        sales_valid = sales_small.dropna(subset=[field])
        leads_valid = leads_small.dropna(subset=[field])

        if sales_valid.empty or leads_valid.empty:
            continue

        sales_grouped = (
            sales_valid
            .groupby(["warehouse_number", field])["pos_id"]
            .apply(list)
            .to_dict()
        )

        matches = []
        for _, row in leads_valid.iterrows():
            key = (row["warehouse_number"], row[field])
            if key in sales_grouped:
                for pos_id in sales_grouped[key]:
                    matches.append((row["lead_id"], pos_id))

        if not matches:
            continue

        merged = pd.DataFrame(matches, columns=["lead_id", "pos_id"]).drop_duplicates()
        merged["score"] = score
        print(f"{field}: {len(merged)} matches")
        score_frames.append(merged)

    # -------------------------------
    # Handle no matches early
    # -------------------------------
    if not score_frames:
        no_match_df = file_leads.copy()
        no_match_df["match_result"] = "No Match"
        no_match_df["similarity_score"] = 0
        no_match_df["pos_id"] = "NA"
        no_match_df["match_type"] = "Exact"
        return no_match_df

    # Get candidate pairs from Phase 1
    candidate_pairs = (
        pd.concat(score_frames, ignore_index=True)
        [["lead_id", "pos_id"]]
        .drop_duplicates()
    )

    print(f"Total candidate pairs: {len(candidate_pairs)}")

    # -------------------------------
    # PHASE 2: Supplementary fields
    # only on candidate pairs
    # -------------------------------
    leads_supp = leads[["lead_id"] + list(SUPPLEMENTARY_FIELDS.keys())]
    sales_supp = sales[["pos_id"] + list(SUPPLEMENTARY_FIELDS.keys())]

    leads_supp = leads[["lead_id"] + list(SUPPLEMENTARY_FIELDS.keys())].drop_duplicates(subset=["lead_id"])
    sales_supp = sales[["pos_id"] + list(SUPPLEMENTARY_FIELDS.keys())].drop_duplicates(subset=["pos_id"])

    candidates_with_data = (
        candidate_pairs
        .merge(leads_supp, on="lead_id", how="left")
        .merge(sales_supp, on="pos_id", how="left", suffixes=("_lead", "_sale"))
    )

    for field, score in SUPPLEMENTARY_FIELDS.items():
        lead_col = f"{field}_lead"
        sale_col = f"{field}_sale"

        mask = (
            candidates_with_data[lead_col].notna() &
            candidates_with_data[sale_col].notna() &
            (candidates_with_data[lead_col] == candidates_with_data[sale_col])
        )

        if mask.any():
            supp_frame = candidates_with_data.loc[mask, ["lead_id", "pos_id"]].copy()
            supp_frame["score"] = score
            print(f"{field}: {mask.sum()} supplementary matches")
            score_frames.append(supp_frame)

    # -------------------------------
    # Aggregate scores
    # -------------------------------
    all_scores = pd.concat(score_frames, ignore_index=True)

    pair_scores = (
        all_scores
        .groupby(["lead_id", "pos_id"], as_index=False)["score"]
        .sum()
        .rename(columns={"score": "similarity_score"})
        .sort_values("similarity_score", ascending=False)
        .drop_duplicates(subset=["pos_id"])
    )

    # -------------------------------
    # Filter qualified matches
    # -------------------------------
    qualified = pair_scores[pair_scores["similarity_score"] >= MINIMUM_SCORE]

    # -------------------------------
    # Join back full data
    # -------------------------------
    sales = sales.rename(columns={"business_name":"business_name_transaction"})
    sales_subset = sales[["pos_id", "fiscal_year_transaction", "fiscal_period_transaction","week",
    "account_number","business_name_transaction"]]

    matched_df = (
        qualified
        .merge(leads, on="lead_id", how="inner")
        .merge(sales_subset, on="pos_id", how="inner")
    )

    # -------------------------------
    # Apply fiscal filter
    # -------------------------------
    matched_df = matched_df[
        (matched_df["fiscal_year_lead"] < matched_df["fiscal_year_transaction"]) |
        (
            (matched_df["fiscal_year_lead"] == matched_df["fiscal_year_transaction"]) &
            (matched_df["fiscal_period_lead"] <= matched_df["fiscal_period_transaction"])
        )
    ]

    # -------------------------------
    # Assign match result
    # -------------------------------
    def assign_confidence(score):
        if score >= 100:
            return "Complete"
        elif score >= 80:
            return "Potential"
        else:
            return "No Match"

    matched_df["match_result"] = matched_df["similarity_score"].apply(assign_confidence)
    matched_df["match_type"] = "Exact"

    # -------------------------------
    # No match records
    # -------------------------------
    matched_ids = set(qualified["lead_id"])
    no_match_df = file_leads[~file_leads["lead_id"].isin(matched_ids)].copy()
    no_match_df["match_result"] = "No Match"
    no_match_df["similarity_score"] = 0
    no_match_df["pos_id"] = "NA"
    no_match_df["match_type"] = "Exact"

    # -------------------------------
    # Final output
    # -------------------------------
    return pd.concat([matched_df, no_match_df], ignore_index=True)


def primary_classification(match_id: str, config_file_path: str, file_a_path: str = "", file_b_path: str = "") -> str:

    """
    This Kubeflow pipeline component performs primary classification of leads data by matching it with sales data.

    Steps:
    1. Downloads input CSV files from Google Cloud Storage (GCS).
    2. Applies exact matching rules:
    - Score-based matching using warehouse-scoped field merges.
        Points: business_name=40, address fields=60, email=30, phone=20
        Warehouse match is mandatory. No null warehouse rows considered.
    3. Assigns confidence levels and similarity scores to each match.
    4. Filters out matches where the lead load date is after the order date.
    5. Writes match audit in cloudsql database
    6. Saves the classified DataFrame back to GCS and returns its URI.

    Parameters:
    - file_a_path: GCS path to the leads CSV file
    - file_b_path: GCS path to the sales CSV file
    - preprocessed_folder: Output folder path in GCS for processed files
    - output_bucket: Destination bucket for processed files
    - leads_classified_file_name: Name of the output file (CSV)
    - user_table: PostgreSQL target table for logging (format: schema.table)
    - connection_string: Cloud SQL connection info
    - secret_user_name / secret_password: Secret Manager keys for DB credentials
    - database_name: Target database name
    - project_id: GCP project ID
    - match_id: Unique identifier for this classification run

"""
    #initialization
    job_config = JobConfig(config_file_path)
    storage_config = job_config.storage_config
    db_config = job_config.db_config

    if file_a_path == "":
        file_a_path = storage_config.temp_leads_path
    
    if file_b_path == "":
        file_b_path = storage_config.temp_pos_path

    #storage
    preprocessed_folder = storage_config.temporary_folder
    output_bucket = storage_config.output_bucket_name
    leads_classified_file_name = storage_config.leads_classified_file_name

    #database details
    schema=db_config.schema_name
    table_name=db_config.audit_table_name


    # Initialize GCS client
    storage_client = storage.Client()
    metadata = MetaData()

    #engine creation
    engine =job_config.db_config.get_engine()

    # Load both files A and B from GCS
    file_a = load_file_from_gcs(file_a_path)
    file_b = load_file_from_gcs(file_b_path)


    user_table_obj = Table(table_name, metadata, autoload_with=engine, schema=schema)
    stmt = insert(user_table_obj).values(match_id=match_id,lead_count=len(file_a),pos_count=len(file_b),status="InProgress")

    with engine.connect() as conn:
        result = conn.execute(stmt)
        conn.commit()

    df = classify_matches(file_a, file_b)

    new_file_name = leads_classified_file_name  # Append "_temp" before the file extension

    # Save the preprocessed data to the "Temporary Files" folder in GCS
    output_file = f"{preprocessed_folder}/{new_file_name}"
    bucket = storage_client.get_bucket(output_bucket)
    output_blob = bucket.blob(output_file)

    # Convert DataFrame to CSV and upload to GCS
    output_blob.upload_from_string(df.to_csv(index=False), 'text/csv')
    output_bucket_name = bucket.name

    uri = f"gs://{output_bucket_name}/{output_file}"
    return uri
