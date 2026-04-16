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
    df = df[[col for col in df.columns if not col.endswith('_sales')]]
    df = df[['lead_id','membership_number' ,'warehouse_number' ,'updated_date' ,
             'fiscal_year_lead','fiscal_period_lead','business_name' ,'address_line_one' ,
             'address_line_two' ,'city' ,'state' ,'zip_code' ,'phone' ,'first_name' ,
             'last_name' ,'email' ,'COMBINED_FIELD' ,'FULL_ADDRESS' ,'CUSTOMER_NAME' ,
             'pos_id','account_number','fiscal_year_transaction','fiscal_period_transaction','week']]
    return df


def classify_matches(file_leads, file_sales):

    SCORE_CONFIG = {
        "business_name": 40,
        "address_line_one":  40,
        "state" : 5,
        "city" : 5,
        "zip_code" : 10,
        "email":         30,
        "phone":         20,
    }

    MINIMUM_SCORE = 80

    # --- 1. Drop rows where warehouse is null in either dataset ---
    leads = file_leads.dropna(subset=["warehouse_number"]).copy()
    sales = file_sales.dropna(subset=["warehouse_number"]).copy()

    leads["warehouse_number"] = leads["warehouse_number"].astype(str).str.strip()
    sales["warehouse_number"] = sales["warehouse_number"].astype(str).str.strip()

    leads = leads[leads["warehouse_number"] != ""]
    sales = sales[sales["warehouse_number"] != ""]

    # --- 2. For each scoring field, merge on (warehouse_number + field) ---
    scored_pairs = []

    for field, score in SCORE_CONFIG.items():
        leads_f = filter_for_merge(leads.copy(), [field])
        sales_f = filter_for_merge(sales.copy(), [field])

        if leads_f.empty or sales_f.empty:
            continue

        merged = leads_f[["lead_id", "warehouse_number", field]].merge(
            sales_f[["pos_id", "warehouse_number", field]],
            on=["warehouse_number", field],
            how="inner"
        )

        merged = merged[["lead_id", "pos_id"]].drop_duplicates()
        merged["score_contribution"] = score
        scored_pairs.append(merged)

        del leads_f, sales_f, merged
        gc.collect()

    if not scored_pairs:
        no_match_df = file_leads.copy()
        no_match_df["match_result"] = "No Match"
        no_match_df["similarity_score"] = 0
        no_match_df["pos_id"] = "NA"
        no_match_df["match_type"] = "Exact"
        return no_match_df

    # --- 3. Aggregate scores per (lead_id, pos_id) pair ---
    all_pairs = pd.concat(scored_pairs, ignore_index=True)
    pair_scores = (
        all_pairs
        .groupby(["lead_id", "pos_id"], as_index=False)["score_contribution"]
        .sum()
        .rename(columns={"score_contribution": "similarity_score"})
    )

    del all_pairs, scored_pairs
    gc.collect()

    # --- 4. Keep all pairs with similarity score >= 80 ---
    qualified_matches = pair_scores[pair_scores["similarity_score"] >= MINIMUM_SCORE]

    # --- 5. Join qualified matches back to full lead + sales data ---
    matched_df = qualified_matches.merge(
        leads,
        on="lead_id",
        how="inner"
    ).merge(
        sales.add_suffix("_sales").rename(columns={"pos_id_sales": "pos_id"}),
        on="pos_id",
        how="inner"
    )

    # --- 6. Apply fiscal year/period filter ---
    matched_df = matched_df[
        (matched_df["fiscal_year_lead"] < matched_df["fiscal_year_transaction"]) |
        (
            (matched_df["fiscal_year_lead"] == matched_df["fiscal_year_transaction"]) &
            (matched_df["fiscal_period_lead"] <= matched_df["fiscal_period_transaction"])
        )
    ]

    # --- 7. Assign confidence level based on score ---
    def assign_confidence(score):
        if score >= 100:
            return "Complete"
        elif score >= 80:
            return "Potential"
        else:
            return "No Match"

    matched_df["match_result"] = matched_df["similarity_score"].apply(assign_confidence)
    matched_df["match_type"] = "Exact"

    # --- 8. No-match: leads that had zero pairs scoring >= 80 ---
    matched_lead_ids = set(qualified_matches["lead_id"])
    no_match_df = file_leads[~file_leads["lead_id"].isin(matched_lead_ids)].copy()
    no_match_df["confidence_level"] = "No Match"
    no_match_df["similarity_score"] = 0
    no_match_df["pos_id"] = "NA"
    no_match_df["match_type"] = "Exact"

    # --- 9. Combine and return ---
    final_df = pd.concat([matched_df, no_match_df], ignore_index=True)
    return final_df


def primary_classification(file_a_path: str, file_b_path: str,match_id: str,config_file_path:str)-> str:

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
