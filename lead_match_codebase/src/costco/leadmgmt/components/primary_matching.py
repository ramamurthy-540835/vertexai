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
    # Step 1: High Confidence Match (Exclude records where business_name or FULL_ADDRESS are empty or NaN)
    high_confidence_leads = filter_for_merge(file_leads, ["business_name", "FULL_ADDRESS"])
    high_confidence_sales = filter_for_merge(file_sales, ["business_name", "FULL_ADDRESS"])

    # Create index on required fields for sales and leads datasets
    high_confidence_leads.set_index(["business_name", "FULL_ADDRESS"], inplace=True)
    high_confidence_sales.set_index(["business_name", "FULL_ADDRESS"], inplace=True)

    high_confidence_df = high_confidence_leads.merge(high_confidence_sales,
                                                     on=["business_name", "FULL_ADDRESS"],
                                                     suffixes=('_leads', '_sales'),
                                                     how='inner').reset_index()

    high_confidence_df = high_confidence_df[(high_confidence_df['warehouse_number_leads'].isna()) |
                                            (high_confidence_df['warehouse_number_leads']
                                             == high_confidence_df['warehouse_number_sales'])].reset_index()

    high_confidence_df = format_df(high_confidence_df)

    del high_confidence_leads,high_confidence_sales
    gc.collect()

    # Exclude already matched leads
    matched_leads = set(high_confidence_df['lead_id'])

    # **********End of high match****************

    # Medium confidence1

    # Filter out leads that are not in high_confidence_df
    filtered_file_leads_1 = file_leads[~file_leads['lead_id'].isin(matched_leads)]

    # Step 3: Medium Confidence Match 1 (Exclude records where FULL_ADDRESS is empty or NaN)
    medium_confidence_leads_1 = filter_for_merge(filtered_file_leads_1, ["FULL_ADDRESS"])
    medium_confidence_sales_1 = filter_for_merge(file_sales, ["FULL_ADDRESS"])

    medium_confidence_leads_1.set_index(["FULL_ADDRESS"], inplace=True)
    medium_confidence_sales_1.set_index(["FULL_ADDRESS"], inplace=True)

    medium_confidence_df_1 = medium_confidence_leads_1.merge(medium_confidence_sales_1,
                                                             on=["FULL_ADDRESS"],
                                                             suffixes=('_leads', '_sales'),
                                                             how='inner').reset_index()

    medium_confidence_df_1 = medium_confidence_df_1[(medium_confidence_df_1['warehouse_number_leads'].isna()) |
                                                    (medium_confidence_df_1['warehouse_number_leads']
                                                     == medium_confidence_df_1[
                                                         'warehouse_number_sales'])].reset_index()

    medium_confidence_df_1 = format_df(medium_confidence_df_1)

    matched_leads.update(medium_confidence_df_1['lead_id'])

    # ******* medium_confidence_2******** #

    filtered_file_leads_2 = file_leads[~file_leads['lead_id'].isin(matched_leads)]

    medium_confidence_leads_2 = filter_for_merge(filtered_file_leads_2, ["phone"])
    medium_confidence_sales_2 = filter_for_merge(file_sales, ["phone"])

    medium_confidence_leads_2.set_index(["phone"], inplace=True)
    medium_confidence_sales_2.set_index(["phone"], inplace=True)

    medium_confidence_df_2 = medium_confidence_leads_2.merge(medium_confidence_sales_2,
                                                             on=["phone"],
                                                             suffixes=('_leads', '_sales'),
                                                             how='inner').reset_index()

    medium_confidence_df_2 = medium_confidence_df_2[(medium_confidence_df_2['warehouse_number_leads'].isna()) |
                                                    (medium_confidence_df_2['warehouse_number_leads']
                                                     == medium_confidence_df_2[
                                                         'warehouse_number_sales'])].reset_index()

    medium_confidence_df_2 = format_df(medium_confidence_df_2)

    # Combine the two medium confidence dataframes
    medium_confidence_df = pd.concat([medium_confidence_df_1, medium_confidence_df_2],
                                     ignore_index=True).drop_duplicates(subset='lead_id')

    matched_leads.update(medium_confidence_df_2['lead_id'])

    del medium_confidence_df_1, medium_confidence_sales_1, medium_confidence_leads_1, medium_confidence_df_2, medium_confidence_sales_2, medium_confidence_leads_2
    gc.collect()

    # **********End of medium match****************

    # Step 5: Filter out leads that are not in high_confidence_df or medium_confidence_df
    filtered_file_leads_3 = file_leads[~file_leads['lead_id'].isin(matched_leads)]

    # Low Confidence Match 1 (Exclude records where membership_number is empty or NaN)
    low_confidence_leads_1 = filter_for_merge(filtered_file_leads_3, ["membership_number"])
    low_confidence_sales_1 = filter_for_merge(file_sales, ["membership_number"])

    low_confidence_leads_1.set_index(["membership_number"], inplace=True)
    low_confidence_sales_1.set_index(["membership_number"], inplace=True)

    low_confidence_df_1 = low_confidence_leads_1.merge(low_confidence_sales_1,
                                                       on=["membership_number"],
                                                       suffixes=('_leads', '_sales'),
                                                       how='inner').reset_index()

    low_confidence_df_1 = low_confidence_df_1[(low_confidence_df_1['warehouse_number_leads'].isna()) |
                                              (low_confidence_df_1['warehouse_number_leads']
                                               == low_confidence_df_1['warehouse_number_sales'])].reset_index()

    low_confidence_df_1 = format_df(low_confidence_df_1)

    matched_leads.update(low_confidence_df_1['lead_id'])

    filtered_file_leads_4 = file_leads[~file_leads['lead_id'].isin(matched_leads)]

    # Low Confidence Match 2 (Exclude records where membership_number is empty or NaN)
    low_confidence_leads_2 = filter_for_merge(filtered_file_leads_4, ["business_name"])
    low_confidence_sales_2 = filter_for_merge(file_sales, ["business_name"])

    low_confidence_leads_2.set_index(["business_name"], inplace=True)
    low_confidence_sales_2.set_index(["business_name"], inplace=True)

    low_confidence_df_2 = low_confidence_leads_2.merge(low_confidence_sales_2,
                                                       on=["business_name"],
                                                       suffixes=('_leads', '_sales'),
                                                       how='inner').reset_index()

    low_confidence_df_2 = low_confidence_df_2[(low_confidence_df_2['warehouse_number_leads'].isna()) |
                                              (low_confidence_df_2['warehouse_number_leads']
                                               == low_confidence_df_2['warehouse_number_sales'])].reset_index()

    low_confidence_df_2 = format_df(low_confidence_df_2)

    matched_leads.update(low_confidence_df_2['lead_id'])

    # Combine the two low confidence dataframes
    low_confidence_df = pd.concat([low_confidence_df_1, low_confidence_df_2], ignore_index=True).drop_duplicates(
        subset='lead_id')

    del low_confidence_df_1, low_confidence_sales_1, low_confidence_leads_1, low_confidence_df_2, low_confidence_sales_2, low_confidence_leads_2
    gc.collect()

    # Fiscal year and period comparison

    high_confidence_df = high_confidence_df[
    (high_confidence_df['fiscal_year_lead'] < high_confidence_df['fiscal_year_transaction']) |
    (
        (high_confidence_df['fiscal_year_lead'] == high_confidence_df['fiscal_year_transaction']) &
        (high_confidence_df['fiscal_period_lead'] <= high_confidence_df['fiscal_period_transaction'])
    )
    ]
    medium_confidence_df = medium_confidence_df[
        (medium_confidence_df['fiscal_year_lead'] < medium_confidence_df['fiscal_year_transaction']) |
        (
                (medium_confidence_df['fiscal_year_lead'] == medium_confidence_df['fiscal_year_transaction']) &
                (medium_confidence_df['fiscal_period_lead'] <= medium_confidence_df['fiscal_period_transaction'])
        )
        ]

    low_confidence_df = low_confidence_df[
        (low_confidence_df['fiscal_year_lead'] < low_confidence_df['fiscal_year_transaction']) |
        (
                (low_confidence_df['fiscal_year_lead'] == low_confidence_df['fiscal_year_transaction']) &
                (low_confidence_df['fiscal_period_lead'] <= low_confidence_df['fiscal_period_transaction'])
        )
        ]
    # Step 8: Filter out leads that are not in high, medium, or low confidence DataFrames
    no_match_df = file_leads[~file_leads['lead_id'].isin(matched_leads)].reset_index()

    # Step 9: Assign Match Confidence and Similarity Score to each DataFrame
    high_confidence_df['confidence_level'] = 'High'
    high_confidence_df['similarity_score'] = 100

    medium_confidence_df['confidence_level'] = 'Medium'
    medium_confidence_df['similarity_score'] = 85

    low_confidence_df['confidence_level'] = 'Low'
    low_confidence_df['similarity_score'] = 80

    no_match_df['confidence_level'] = 'No Match'
    no_match_df['pos_id'] = 'NA'
    no_match_df['similarity_score'] = 0
    all_dataframe = [high_confidence_df, medium_confidence_df, low_confidence_df, no_match_df]
    valid_dataframe = []

    for df in all_dataframe:
        if len(df) > 0:
            valid_dataframe.append(df)

    # Step 10: Combine all the DataFrames (High, Medium, Low, and No Match)
    final_df = pd.concat(valid_dataframe)
    final_df['match_type'] = 'Exact'

    # Return the final combined DataFrame
    return final_df


def primary_classification(file_a_path: str, file_b_path: str,match_id: str,config_file_path:str)-> str:

    """
    This Kubeflow pipeline component performs primary classification of leads data by matching it with sales data.

    Steps:
    1. Downloads input CSV files from Google Cloud Storage (GCS).
    2. Applies exact matching rules:
    - High: Matches on business name and full address.(with warehouse optionally)
    - Medium: Matches on address or phone.(with warehouse optionally)
    - Low: Matches on membership number or business name.(with warehouse optionally)
    - No Match: Remaining unmatched records.(with warehouse optionally)
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
