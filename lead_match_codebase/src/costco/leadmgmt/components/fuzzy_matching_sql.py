import pandas as pd
import sqlalchemy
from sqlalchemy import text,bindparam
from datetime import datetime
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs, process_and_archive_files
from costco.leadmgmt.util.fiscal_year import get_costco_fiscal_info



def execute_select_query(engine, query, params=None):
    # Execute the query and fetch the result into a DataFrame
    with engine.connect() as connection:
        result = connection.execute(query, params or {})
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
    return df


def get_confidence_level(similarity_score, match_configuration_df):
    if pd.notna(similarity_score):
        # Loop through the rows in the confidence table to find the matching level
        for _, row in match_configuration_df.iterrows():
            if row['min_score'] <= similarity_score <= row['max_score']:
                return row['match_result']
        return 'No Match'  # Default if no match is found
    return 'No Match'


def fuzzy_matching(file_classified_path: str, config_file_path: str) -> str:
    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    storage_config = job_config.storage_config
    query_config = job_config.match_query

    # storage details
    source_bucket_name = storage_config.source_bucket_name
    source_folder = storage_config.source_folder_output
    destination_bucket_name = storage_config.destination_bucket_name
    destination_folder = storage_config.destination_folder_output

    # query
    query_fuzzy_wh = query_config.query_fuzzy_wh
    query_fuzzy_null_wh=query_config.query_fuzzy_null_wh
    query_match_configuration = query_config.query_match_configuration

    # engine
    engine = db_config.get_engine()

    # fiscal information
    fiscal_info = get_costco_fiscal_info()


    classified_df = load_file_from_gcs(file_classified_path)

    classified_df['warehouse_number'] = pd.to_numeric(classified_df['warehouse_number'], errors='coerce').astype('Int64')
    classified_df['pos_id'] = classified_df['pos_id'].astype(str)

    # null_wh_leads = (
    #     classified_df[classified_df['warehouse_number'].isna()]['lead_id']
    #     .drop_duplicates()
    #     .tolist()
    # )

    non_empty_wh_leads = (
        classified_df[classified_df['warehouse_number'].notna()]['lead_id']
        .drop_duplicates()
        .tolist()
    )


    batch_size = 10000

    master_df = pd.DataFrame()

    # Loop through lead id list in chunks of 10,000
    for i in range(0, len(non_empty_wh_leads), batch_size):
        # Create the current batch of lead IDs
        leads_id_batch = non_empty_wh_leads[i:(i + batch_size)]

        # Skip empty batch (sometimes tuple of one can cause SQL issues)
        if not leads_id_batch:
            continue

        params = {

            "fiscal_year_sales": fiscal_info["fiscal_year"],
            "leads_id_batch": leads_id_batch
        }

        query = text(query_fuzzy_wh).bindparams(
            bindparam("leads_id_batch", expanding=True)  # necessary for list expansion
        )
        # print(query)

        # Execute the query with the updated query and parameters
        df_batch_result = execute_select_query(engine, query, params)

        # Append the result to the master DataFrame
        master_df = pd.concat([master_df, df_batch_result], ignore_index=True)

    #master_df_2 = pd.DataFrame()

    # Loop through lead id list in chunks of 10,000
    # for i in range(0, len(null_wh_leads), batch_size):
    #     # Create the current batch of lead IDs
    #     leads_id_batch = null_wh_leads[i:(i + batch_size)]

    #     # Skip empty batch (sometimes tuple of one can cause SQL issues)
    #     if not leads_id_batch:
    #         continue

    #     params = {
    #         "fiscal_year_sales": fiscal_info["fiscal_year"],
    #         "leads_id_batch": leads_id_batch
    #     }

    #     query = text(query_fuzzy_null_wh).bindparams(
    #         bindparam("leads_id_batch", expanding=True)  # necessary for list expansion
    #     )
    #     # print(query)

    #     # Execute the query with the updated query and parameters
    #     df_batch_result = execute_select_query(engine, query, params)

    #     # Append the result to the master DataFrame
    #     master_df_2 = pd.concat([master_df_2, df_batch_result], ignore_index=True)

    # master_df = pd.concat([master_df_1, master_df_2], ignore_index=True)


    master_df['similarity_score'] = ((master_df['combined_field_score'] + 4 * master_df['full_address_score'] +
                                      3 * master_df['business_name_score']) / 8)

    df_fuzzy_result = master_df[master_df['similarity_score'] >= 80]


    # Step 1: Merge classified_df with df_result on 'lead_id'
    merged_df = pd.merge(classified_df, df_fuzzy_result, how='left', on='lead_id', suffixes=('_primary', '_fuzzy'))

    # Step 2: Update 'pos_id' and 'similarity_score' in classified_df based on similarity_score
    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']),
    'pos_id_primary'] = merged_df['pos_id_fuzzy']

  
    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']),
    'account_number_primary'] = merged_df['account_number_fuzzy'].astype('float64')

    
    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']),
    'match_type'] = "Fuzzy"

    merged_df['similarity_score_primary'] = merged_df['similarity_score_primary'].astype('float64')
    merged_df['similarity_score_fuzzy'] = merged_df['similarity_score_fuzzy'].astype('float64')


    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'similarity_score_primary'] = merged_df['similarity_score_fuzzy']

    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'fiscal_year_transaction'] = merged_df['fiscal_year']

    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'fiscal_period_transaction'] = merged_df['fiscal_period']

    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'business_name_transaction'] = merged_df['business_name_fuzzy']

    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'week_primary'] = merged_df['week_fuzzy']

    merged_df.loc[(merged_df['similarity_score_primary'] < merged_df['similarity_score_fuzzy']) & pd.notna(
        merged_df['pos_id_fuzzy']) & pd.notna(merged_df['similarity_score_fuzzy']),
    'warehouse_number_primary'] = merged_df['warehouse_number_fuzzy']


    # Step 3: Drop the result columns if you don't need them anymore
    merged_df.drop(columns=['pos_id_fuzzy', 'similarity_score_fuzzy','account_number_fuzzy',
    'week_fuzzy','warehouse_number_fuzzy','fiscal_year','fiscal_period','business_name_fuzzy'], inplace=True)
    merged_df.columns = merged_df.columns.str.replace(r'_primary$', '', regex=True)

    query_match_configuration = text(query_match_configuration)
    match_configuration_df = execute_select_query(engine, query_match_configuration)

    # Apply the function to your merged_df['confidence_level']
    merged_df['match_result'] = merged_df['similarity_score'].apply(
        lambda x: get_confidence_level(x, match_configuration_df)
    )

    #  Apply business rule:
    # 1. If HIGH exists → remove Medium/Low for that lead
    # 2. If multiple HIGH → keep earliest interaction


    # Step 1: Sort EARLIEST first
    merged_df = merged_df.sort_values(
        by=['lead_id', 'fiscal_year_transaction', 'fiscal_period_transaction', 'week'],
        ascending=[True, True, True, True]  
    )
    merged_df['primary_transaction'] = False

    # Step 2: Assign rank per lead (earliest = rank 1)
    merged_df['rank'] = merged_df.groupby('lead_id').cumcount() + 1

    # Step 3: Filter HIGH confidence
    high_conf_df = merged_df[merged_df['match_result'] == 'Complete']

    # Step 4: Assign NMI flag for high confidence matches
    #high_conf_latest = high_conf_df[high_conf_df['rank'] == 1]
    high_conf_df['primary_transaction'] = (high_conf_df['rank'] == 1) 

    # Step 5: Get leads that have HIGH
    high_conf_leads = high_conf_df['lead_id'].unique()

    # Step 6: Keep non-high only for leads WITHOUT high
    non_high_df = merged_df[
        (~merged_df['lead_id'].isin(high_conf_leads))
    ]

    # Step 7: Combine
    merged_df = pd.concat([high_conf_df, non_high_df], ignore_index=True)

    # Cleanup
    merged_df.drop(columns=['rank'], inplace=True)

    # Step 3: Drop unnecessary columns and final dataframe preparation
    classified_df_updated = merged_df[
        ['pos_id', 'lead_id','match_result','account_number','business_name_transaction',
        'warehouse_number', 'match_type','similarity_score','primary_transaction',
        'fiscal_year_transaction','fiscal_period_transaction','week']]

    classified_df_updated['updated_date'] = pd.to_datetime(datetime.now())

    # Handle pos_id and account_number
    classified_df_updated.loc[classified_df_updated['match_result'] == 'No Match', 'pos_id'] = ''
    classified_df_updated['account_number'].fillna(0, inplace=True)

    classified_df_updated = classified_df_updated.drop_duplicates(subset=['lead_id', 'pos_id'])

    # Check if fiscal_info has the right keys
    print(fiscal_info.keys())

    # Check the DataFrame columns
    print(classified_df_updated.columns)

    counter = 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    counter = counter % 10000  # reset the counter back to 0 after it reaches 9999

    # Generate the file name using the counter
    base_name = f"final_update_dataframe_{counter:04d}_{timestamp}"

    uri = process_and_archive_files(source_bucket_name, source_folder, destination_bucket_name, destination_folder,
                                    classified_df_updated, base_name)

    counter += 1
    return uri


