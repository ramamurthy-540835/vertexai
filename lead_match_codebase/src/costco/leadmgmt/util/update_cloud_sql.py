from costco.leadmgmt.util.load_data_from_gcs import load_file_from_gcs
from costco.leadmgmt.util.get_secret_from_secret_manager import access_secret
import pandas as pd
from io import StringIO
import sqlalchemy
from sqlalchemy import create_engine, text
from datetime import datetime
from costco.leadmgmt.util.cloud_sql_conn import engine_creation


def update_operation(engine, high_confidence_dataframe, other_confidence_dataframe, pos_dataframe, final_update_lead_query_high, final_update_lead_query_normal, final_update_pos_query, batch_size=1000):
    # Update high-confidence leads
    num_batches = len(high_confidence_dataframe) // batch_size + (1 if len(high_confidence_dataframe) % batch_size != 0 else 0)
    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min((batch_num + 1) * batch_size, len(high_confidence_dataframe))
        batch_df = high_confidence_dataframe.iloc[start_idx:end_idx]

        # Set updated_date to the current timestamp for all records in this batch
        updated_date = datetime.now()

        # Execute the batch update
        with engine.connect() as connection:
            with connection.begin():  # Automatically commits the transaction
                connection.execute(
                    text(final_update_lead_query_high),
                    [{'lead_id': row['lead_id'], 'lead_status': row['lead_status'], 'confidence_level': row['confidence_level'], 'updated_date': updated_date, 'closed_fiscal_period': row['closed_fiscal_period'], 'closed_fiscal_year': row['closed_fiscal_year']} for _, row in batch_df.iterrows()]
                )

    # Update other-confidence leads
    num_batches = len(other_confidence_dataframe) // batch_size + (1 if len(other_confidence_dataframe) % batch_size != 0 else 0)
    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min((batch_num + 1) * batch_size, len(other_confidence_dataframe))
        batch_df = other_confidence_dataframe.iloc[start_idx:end_idx]

        # Set updated_date to the current timestamp for all records in this batch
        updated_date = datetime.now()

        # Execute the batch update
        with engine.connect() as connection:
            with connection.begin():  # Automatically commits the transaction
                connection.execute(
                    text(final_update_lead_query_normal),
                    [{'lead_id': row['lead_id'], 'lead_status': row['lead_status'], 'confidence_level': row['confidence_level'], 'updated_date': updated_date} for _, row in batch_df.iterrows()]
                )

    # Update pos dataframe
    num_batches = len(pos_dataframe) // batch_size + (1 if len(pos_dataframe) % batch_size != 0 else 0)
    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min((batch_num + 1) * batch_size, len(pos_dataframe))
        batch_df = pos_dataframe.iloc[start_idx:end_idx]

        # Set updated_date to the current timestamp for all records in this batch
        updated_date = datetime.now()

        # Execute the batch update
        with engine.connect() as connection:
            with connection.begin():  # Automatically commits the transaction
                connection.execute(
                    text(final_update_pos_query),
                    [{'order_number': int(row['order_number']), 'lead_id': row['lead_id'], 'consumer_id' : int(row['consumer_id']),'updated_date': updated_date, 'match_type': row['match_type'], 'match_score': row['similarity_score']} for _, row in batch_df.iterrows() if int(row['order_number']) != 0]
                )


final_df = load_file_from_gcs(file_path)


high_confidence_dataframe = final_df[final_df['confidence_level'] == 'High']
high_confidence_dataframe['closed_fiscal_period']=high_confidence_dataframe['closed_fiscal_period'].astype(int)
high_confidence_dataframe['closed_fiscal_year']=high_confidence_dataframe['closed_fiscal_year'].astype(int)
print('High confidence dataframe',len(high_confidence_dataframe))
other_confidence_dataframe = final_df[final_df['confidence_level'] != 'High']
print('other confidence dataframe',len(other_confidence_dataframe))
pos_dataframe = final_df[final_df['order_number'] != 0]
pos_dataframe['consumer_id'] = pos_dataframe['consumer_id'].astype(int)
print('pos confidence dataframe',len(pos_dataframe))

engine = engine_creation(connection_string,secret_user_name,secret_password,database_name,project_id)

update_operation(engine, high_confidence_dataframe, other_confidence_dataframe, pos_dataframe, final_update_lead_query_high, final_update_lead_query_normal, final_update_pos_query, batch_size=1000)