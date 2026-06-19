import pandas as pd
import sqlalchemy
from sqlalchemy import text, bindparam
from datetime import datetime
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import load_file_from_gcs
from google.cloud import storage
from sqlalchemy.types import TIMESTAMP
import numpy as np


def get_gcs_file_path(uri: str) -> str:
    if not uri.startswith("gs://"):
        raise ValueError("Invalid GCS URI. Must start with 'gs://'.")

    # Extract bucket and folder from URI
    path = uri[5:]
    parts = path.split('/', 1)
    bucket_name = parts[0]
    folder_path = parts[1] if len(parts) > 1 else ""

    if folder_path and not folder_path.endswith('/'):
        folder_path += '/'

    # Connect to GCS
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # Retries or archive timing can leave more than one final output in the
    # folder. Use the latest CSV instead of failing both update branches.
    blobs = [
        blob for blob in bucket.list_blobs(prefix=folder_path)
        if not blob.name.endswith('/') and blob.name.lower().endswith('.csv')
    ]

    if not blobs:
        raise ValueError(f"No CSV files found in '{uri}'")

    blobs.sort(key=lambda blob: blob.updated, reverse=True)
    if len(blobs) > 1:
        print(f"Found {len(blobs)} CSV files in '{uri}', using latest: {blobs[0].name}")

    return f"gs://{bucket_name}/{blobs[0].name}"


def _is_truthy(v) -> bool:
    """
    Robust truthiness for the closed_existing_flag column, which may come
    back from the GCS CSV as a real bool, or as the strings 'True'/'true'/
    '1', etc. NaN/empty is always False.
    """
    if pd.isna(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "t", "yes")
    return bool(v)


def lead_table_update(engine, leads_dataframe, create_temp_table_lead, insert_query_lead,schema_name):
    with engine.connect() as connection:
        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(create_temp_table_lead)
            )
        print("Temporary lead table created")

        leads_dataframe.to_sql("temp_lead", con=connection, schema=schema_name, if_exists="append", index=False,
                               method="multi", chunksize=5000, dtype={"updated_date": TIMESTAMP(timezone=False)})

        print("Data inserted into the temp_lead table")


        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(insert_query_lead)
            )

        print("Data updated to the lead table")


def transaction_table_update(engine, pos_dataframe, create_temp_table_transaction, insert_query_transaction,schema_name):
    with engine.connect() as connection:
        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(create_temp_table_transaction)
            )
        print("Temporary transaction table created")

        pos_dataframe.to_sql("temp_transaction", con=connection, schema=schema_name, if_exists="append", index=False,
                             method="multi", chunksize=5000, dtype={"updated_date": TIMESTAMP(timezone=False)})

        print("Data inserted into the temp_transaction table")


        with connection.begin():  # Automatically commits the transaction
            connection.execute(
                text(insert_query_transaction)
            )

        print("Data updated to the transaction table")


def mark_transactions_processed(engine, processed_pos_ids, schema_name, batch_size=5000):
    """
    Flip is_processed=true (and stamp process_datetime) for every POS id
    scanned this run. Called AFTER the match write commits, so a failure
    here just leaves rows unprocessed → they're re-scanned next cycle and
    re-upserted idempotently (no data loss). Batched to bound the IN-list.
    """
    if not processed_pos_ids:
        print("No processed transactions to mark")
        return

    update_sql = text(
        f"""
        UPDATE {schema_name}.transaction
        SET is_processed     = true,
            process_datetime = :process_datetime
        WHERE pos_id IN :pos_ids
        """
    ).bindparams(bindparam("pos_ids", expanding=True))

    now = datetime.now()
    total = 0
    with engine.connect() as connection:
        with connection.begin():
            for i in range(0, len(processed_pos_ids), batch_size):
                batch = processed_pos_ids[i:i + batch_size]
                result = connection.execute(
                    update_sql,
                    {"process_datetime": now, "pos_ids": batch},
                )
                total += result.rowcount if result.rowcount is not None else 0

    print(f"Marked is_processed=true for {total} transaction row(s) "
          f"across {len(processed_pos_ids)} scanned pos id(s)")


def lead_status_closed_existing_update(engine, ce_lead_ids, schema_name, batch_size=5000):
    """
    Set lead_status = 'Closed - Existing' (plus updated_date / updated_by)
    on every Closed-Existing lead, via a direct parametrized UPDATE.

    CE leads carry no Match/Potential row, so they bypass the temp-table
    upsert flow entirely — this is a straight status update keyed on
    lead_id. IDs are updated in batches to keep the IN-list bounded.
    """
    if not ce_lead_ids:
        print("No Closed-Existing leads to update")
        return

    update_sql = text(
        f"""
        UPDATE {schema_name}.lead
        SET lead_status  = :status,
            updated_date = :updated_date,
            updated_by   = :updated_by
        WHERE lead_id IN :lead_ids
        """
    ).bindparams(bindparam("lead_ids", expanding=True))

    now = datetime.now()
    total = 0

    with engine.connect() as connection:
        with connection.begin():  # single transaction for all batches
            for i in range(0, len(ce_lead_ids), batch_size):
                batch = ce_lead_ids[i:i + batch_size]
                result = connection.execute(
                    update_sql,
                    {
                        "status":       "Closed - Existing",
                        "updated_date": now,
                        "updated_by":   "GCP",
                        "lead_ids":     batch,
                    },
                )
                # rowcount reflects rows actually matched in the lead table
                total += result.rowcount if result.rowcount is not None else 0

    print(f"lead_status='Closed - Existing' applied to {total} lead row(s) "
          f"across {len(ce_lead_ids)} CE lead id(s)")


def update_cloud_sql(config_file_path: str,file_path: str = ""):


    # Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query

    # query
    create_temp_table_transaction=query_config.create_temp_table_transaction
    create_temp_table_lead=query_config.create_temp_table_lead
    insert_query_transaction=query_config.insert_query_transaction
    insert_query_lead=query_config.insert_query_lead

    # engine
    engine = db_config.get_engine()
    schema_name = db_config.schema_name

    #in case of failure
    storage_config=job_config.storage_config
    standalone_file_path=storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    # Load the files from GCS into pandas DataFrames
    final_df = load_file_from_gcs(file_path)

    final_df.rename(columns={'similarity_score': 'match_score'}, inplace=True)
    # Ensure match_score is numeric (in case it's a string)
    final_df['match_score'] = pd.to_numeric(final_df['match_score'], errors='coerce')
    final_df['updated_by'] = 'GCP'

    # ------------------------------------------------------------------
    # Closed-Existing leads — collect ids for a direct lead_status update.
    # These rows have closed_existing_flag=True and a null match_result,
    # so they never enter the Match/Potential dataframes below.
    # ------------------------------------------------------------------
    if 'closed_existing_flag' in final_df.columns:
        ce_mask = final_df['closed_existing_flag'].apply(_is_truthy)
        ce_lead_ids = (
            final_df.loc[ce_mask, 'lead_id']
            .dropna()
            .astype(str)
            .str.strip()
        )
        ce_lead_ids = ce_lead_ids[ce_lead_ids != ''].unique().tolist()
    else:
        ce_lead_ids = []
    print('closed-existing leads: ', len(ce_lead_ids))

    # preprocessing pos_dataframe
    # pos_dataframe = final_df[final_df['pos_id'] != '']
    pos_dataframe = final_df[final_df['match_result'].isin(['Match','Potential'])]
    pos_dataframe = pos_dataframe[['pos_id', 'lead_id', 'match_type', 'match_score', 'updated_by', 'updated_date','primary_transaction','matching_comments']]
    # Sort by match_score descending so the highest score comes first
    pos_dataframe = pos_dataframe.sort_values(by='match_score', ascending=False)
    # Drop duplicates, keeping the first (i.e., highest match_score)
    pos_dataframe = pos_dataframe.drop_duplicates(subset='pos_id', keep='first').reset_index(drop=True)
    print('pos confidence dataframe: ', len(pos_dataframe))

    # preprocessing leads_dataframe
    leads_dataframe = final_df[final_df['match_result'].isin(['Match','Potential'])]

    # Sort by match_score descending so the highest score comes first
    leads_dataframe = leads_dataframe.sort_values(by='match_score', ascending=False)
    # Drop duplicates, keeping the first (i.e., highest match_score)
    leads_dataframe = leads_dataframe.drop_duplicates(subset='lead_id', keep='first').reset_index(drop=True)
    leads_dataframe = leads_dataframe[
        ['lead_id','account_number',  'match_result', 'updated_date', 'updated_by']]
    leads_dataframe['account_number'] = leads_dataframe['account_number'].astype(int)
    print('lead table dataframe: ', len(leads_dataframe))


    if not pos_dataframe.empty:
        transaction_table_update(engine, pos_dataframe, create_temp_table_transaction,
                                 insert_query_transaction,schema_name)  # transaction table update

    if not leads_dataframe.empty:
        lead_table_update(engine, leads_dataframe, create_temp_table_lead, insert_query_lead,schema_name)  # lead table update

    # Closed-Existing status update (independent of Match/Potential flow)
    lead_status_closed_existing_update(engine, ce_lead_ids, schema_name)

    # ------------------------------------------------------------------
    # Mark scanned transactions as processed (is_processed=true) so they
    # are excluded from future match cycles. Done LAST, after the match
    # writes above commit: if this step fails, the rows simply stay
    # unprocessed and get re-scanned + idempotently re-upserted next run.
    # The id list comes from the manifest the match job wrote to
    # temporary_folder (it covers every SCANNED transaction, match or not
    # — which the match output alone does not).
    # ------------------------------------------------------------------
    try:
        temporary_folder = storage_config.temporary_folder
        output_bucket    = storage_config.output_bucket_name
        manifest_uri = f"gs://{output_bucket}/{temporary_folder}/processed_pos_ids.csv"
        manifest_df = load_file_from_gcs(manifest_uri)
        processed_pos_ids = (
            manifest_df['pos_id']
            .dropna().astype(str).str.strip()
        )
        processed_pos_ids = processed_pos_ids[processed_pos_ids != ''].unique().tolist()
        print('scanned transactions to mark processed: ', len(processed_pos_ids))
        mark_transactions_processed(engine, processed_pos_ids, schema_name)
    except Exception as e:
        # Non-fatal: matches are already committed. Leaving rows
        # unprocessed only means they're re-scanned next cycle.
        print(f"WARNING: could not mark transactions processed: {e}")
