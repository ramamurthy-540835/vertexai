import pandas as pd
from google.cloud import storage
from unidecode import unidecode
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.database.DBUtil import load_data_from_cloudsql
from costco.leadmgmt.util.apputil import process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info


def normalize_string(s):
    """Normalize text for comparison (remove special chars, lowercase)."""
    if pd.isna(s):
        return ''

    return ''.join(e if e.isalnum() or e.isspace() else '' for e in unidecode(str(s))).lower()

def create_combined_field(row):
    """Combine CUSTOMER_NAME and ADDRESS fields into a single composite string."""
    address_fields = ['address_line_one','address_line_two', 'city','state','zip_code']
    CUSTOMER_NAME = ['first_name', 'last_name']
    business_name_field = 'business_name'

    full_address = '^'.join([
        normalize_string(row[col]).strip()
        for col in address_fields
        if col in row and pd.notna(row[col])
    ])

    if set(full_address.split('^')) == {''}:
        full_address = ''

    customer_name = '^'.join([
        normalize_string(row[col]).strip()
        for col in CUSTOMER_NAME
        if col in row and pd.notna(row[col])
    ])


    business_name = normalize_string(row[business_name_field]).strip() if business_name_field in row and pd.notna(row[business_name_field]) else ''

    combined_field = f"{business_name}^{full_address}^{row['phone']}^{customer_name}^{row['email']}".strip()

    return combined_field,full_address,customer_name

def validate_combined_field(df):

    if 'COMBINED_FIELD' not in df.columns or 'FULL_ADDRESS' not in df.columns or 'CUSTOMER_NAME' not in df.columns:
        df[['COMBINED_FIELD','FULL_ADDRESS','CUSTOMER_NAME']] = df.apply(
            lambda row: pd.Series(create_combined_field(row)), axis=1
        )
    return df

def enforce_required_columns(df, required_columns):
    """Ensure required columns exist in the DataFrame."""
    for col in required_columns:
        if col not in df.columns:
            df[col] = ''  # Default value for missing columns
            print(f"⚠️ Column '{col}' added with default empty values.")

    # Now, handle zip_code to make sure only the first 5 digits are used
    if 'zip_code' in df.columns:
        df['zip_code'] = df['zip_code'].apply(lambda x: str(x)[:5] if pd.notna(x) else '')  # Ensure first 5 digits of zip_code
    return df

def clean_required_columns(df, required_columns):
    """Clean required columns by stripping, replacing spaces, and converting to lowercase."""
    for col in required_columns:
        if col in df.columns:
            df[col] = df[col].astype(str).apply(lambda x: x.strip().lower())
    return df


def load_and_preprocess_data_cloud_sql(base_name: str, config_file_path:str) -> str:

    """
    This component loads data from a Cloud SQL instance using a query,
    processes and archives the source files in GCS, performs cleaning,
    generates combined address/customer fields, and uploads the processed
    file to a specified bucket/folder in GCS.

    Parameters:
    - connection_string: Cloud SQL connection info
    - secret_user_name / secret_password: Secret Manager keys
    - base_name: Base name for the output file
    - output_bucket: Destination bucket for processed files
    - source/destination folders: For file archiving

    """
    #initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config

    # engine creation
    engine = db_config.get_engine()
    storage_client = storage.Client()

    fiscal_info = get_costco_fiscal_info()

    query_input = None
    if base_name == "pos":
        #query
        #query_input = f'''{query_config.query_pos} = {fiscal_info["fiscal_year"]}'''
        query_input = f'''{query_config.query_pos} = 2026'''
        # storage
        source_folder_input = storage_config.source_folder_input_pos
        destination_folder_input = storage_config.destination_folder_input_pos
    elif base_name == "leads":
        #query
        #query_input = f'''{query_config.query_leads} >= {fiscal_info["fiscal_year"] - 1}'''
        query_input = f'''{query_config.query_leads} = 2026'''
        # storage
        source_folder_input = storage_config.source_folder_input_leads
        destination_folder_input = storage_config.destination_folder_input_leads
    else:
        raise Exception("invalid base name ")

    #storage
    output_bucket = storage_config.output_bucket_name
    preprocessed_folder = storage_config.temporary_folder
    source_bucket_name = storage_config.source_bucket_name
    destination_bucket_name = storage_config.destination_bucket_name


    input_data_df = load_data_from_cloudsql(engine, query_input)

    #Archive the input file received
    uri = process_and_archive_files(source_bucket_name, source_folder_input, destination_bucket_name,
                              destination_folder_input, input_data_df, base_name)


    input_data_df = input_data_df.fillna("")

    # Ensure required columns
    required_columns = ['warehouse_number', 'membership_number', 'business_name', 'first_name', 'last_name',
                        'address_line_one', 'address_line_two', 'city', 'state', 'zip_code', 'phone', 'email']

    input_data_df = enforce_required_columns(input_data_df, required_columns)

    # Validate and create COMBINED_FIELD
    input_data_df = validate_combined_field(input_data_df)

    required_columns = ['warehouse_number', 'membership_number', 'business_name', 'first_name', 'last_name', 'city',
                        'state', 'zip_code', 'phone', 'email', 'address_line_one', 'address_line_two', 'COMBINED_FIELD',
                        'FULL_ADDRESS', 'CUSTOMER_NAME']

    input_data_df = clean_required_columns(input_data_df, required_columns)

    # Generate the new file name by adding "_temp" before the extension
    base_name = base_name  # name as input parameter from pipeline
    name_without_extension = base_name.rsplit('.', 1)[0]  # Remove the file extension
    new_file_name = f"{name_without_extension}_temp.csv"  # Append "_temp" before the file extension

    # Save the preprocessed data to the "Temporary Files" folder in GCS
    output_file = f"{preprocessed_folder}/{new_file_name}"
    bucket = storage_client.get_bucket(output_bucket)
    output_blob = bucket.blob(output_file)

    # Convert DataFrame to CSV and upload to GCS
    output_blob.upload_from_string(input_data_df.to_csv(index=False), 'text/csv')
    output_bucket_name = bucket.name

    uri = f"gs://{output_bucket_name}/{output_file}"

    return uri





