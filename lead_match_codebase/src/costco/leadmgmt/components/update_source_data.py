import pandas as pd
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

    # List blobs under the folder
    blobs = list(bucket.list_blobs(prefix=folder_path))
    files = [blob.name for blob in blobs if not blob.name.endswith('/')]

    if len(files) != 1:
        raise ValueError(f"Expected exactly one file in '{uri}', found {len(files)}")

    return f"gs://{bucket_name}/{files[0]}"


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


# ======================================================================
# MARK SCANNED TRANSACTIONS AS PROCESSED  (20M-scale, memory-safe)
# ======================================================================
# The processed-pos-id manifest can be up to ~20M rows. The old approach
# loaded the whole file into pandas and built a 20M-element Python list,
# then ran ~4,000 `WHERE pos_id IN (...)` updates inside ONE giant
# transaction. Both the list and the single long transaction were the
# real bottlenecks (matched records are only ~57K and are fine).
#
# New approach:
#   1. Stream the manifest from GCS in chunks into a staging table — only
#      one chunk (e.g. 250K ids) is ever in memory, so RAM stays flat.
#   2. Index the staging table, then run ONE set-based UPDATE ... FROM
#      join. The database does the matching natively — no Python id list,
#      no thousands of round trips.
#
# Safe to fail: if anything here dies, the match writes are already
# committed and the affected rows simply stay is_processed=false → they
# get re-scanned and idempotently re-upserted next cycle. No data loss.
# ======================================================================

_STAGING_TABLE = "temp_processed_pos"


def _build_processed_staging_table(engine, manifest_uri, schema_name,
                                   read_chunksize=250_000, insert_chunksize=5_000):
    """
    Stream processed_pos_ids.csv from GCS into {schema}.temp_processed_pos
    in chunks. Never holds the full id set in memory.

    Returns the number of pos ids staged.
    """
    staging_fqn = f"{schema_name}.{_STAGING_TABLE}"

    # Fresh staging table each run (drop any leftover from a prior failure).
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"DROP TABLE IF EXISTS {staging_fqn}"))
            connection.execute(
                text(f"CREATE TABLE {staging_fqn} (pos_id text)")
            )
    print(f"Staging table {staging_fqn} created")

    total_staged = 0
    # pd.read_csv with chunksize streams the file — one chunk at a time in
    # memory. dtype=str keeps pos_id exact (no float coercion like 1234.0).
    reader = pd.read_csv(manifest_uri, dtype={"pos_id": str}, chunksize=read_chunksize)

    with engine.connect() as connection:
        for chunk in reader:
            ids = chunk[["pos_id"]].copy()
            ids["pos_id"] = ids["pos_id"].astype(str).str.strip()
            ids = ids[(ids["pos_id"] != "") & (ids["pos_id"].str.lower() != "nan")]
            if ids.empty:
                continue

            # Commit per chunk so memory and transaction size both stay
            # bounded; a mid-stream failure just leaves a partial staging
            # table, which we rebuild from scratch on the next run anyway.
            with connection.begin():
                ids.to_sql(
                    _STAGING_TABLE,
                    con=connection,
                    schema=schema_name,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=insert_chunksize,
                )
            total_staged += len(ids)
            print(f"  staged {total_staged} pos ids so far...")

    # Index the join key so the UPDATE join is fast, not a seq scan.
    if total_staged > 0:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(f"CREATE INDEX ON {staging_fqn} (pos_id)")
                )
                connection.execute(text(f"ANALYZE {staging_fqn}"))

    print(f"Total pos ids staged: {total_staged}")
    return total_staged


def _drop_processed_staging_table(engine, schema_name):
    staging_fqn = f"{schema_name}.{_STAGING_TABLE}"
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"DROP TABLE IF EXISTS {staging_fqn}"))
    print(f"Staging table {staging_fqn} dropped")


def mark_transactions_processed(engine, manifest_uri, schema_name,
                                read_chunksize=250_000, update_batch_size=50_000):
    """
    Flip is_processed=true (and stamp process_datetime) for every scanned
    pos id in the manifest, in bounded batches.

    Each batch is ONE statement that:
      1. deletes a slice of pos ids from the staging table (RETURNING them), and
      2. updates the matching transaction rows using that slice,
    and is committed on its own. The staging table draining to empty is the
    progress marker — no Python id list, no "what's already done" tracking.

    Why batched (vs one 20M-row UPDATE): bounds dead-tuple/bloat spikes,
    keeps WAL bursts (and read-replica lag) small, releases locks between
    commits, and preserves partial progress on failure. The re-scan design
    means any rows left unmarked are simply picked up next cycle.
    """
    staged = _build_processed_staging_table(
        engine, manifest_uri, schema_name, read_chunksize=read_chunksize
    )

    if staged == 0:
        print("No processed transactions to mark")
        _drop_processed_staging_table(engine, schema_name)
        return

    staging_fqn = f"{schema_name}.{_STAGING_TABLE}"

    # One statement per batch: delete up to :n rows from staging and update
    # the matching transaction rows. The final SELECT returns how many
    # staging rows were consumed (deleted) and how many transaction rows
    # were actually flipped (updated). deleted == 0 → staging empty → stop.
    batch_sql = text(
        f"""
        WITH batch AS (
            DELETE FROM {staging_fqn}
            WHERE ctid IN (SELECT ctid FROM {staging_fqn} LIMIT :n)
            RETURNING pos_id
        ),
        upd AS (
            UPDATE {schema_name}.transaction AS t
            SET is_processed     = true,
                process_datetime = :process_datetime
            FROM batch AS b
            WHERE t.pos_id = b.pos_id
            RETURNING t.pos_id
        )
        SELECT
            (SELECT count(*) FROM batch) AS deleted,
            (SELECT count(*) FROM upd)   AS updated
        """
    )

    total_consumed = 0
    total_marked = 0
    batch_no = 0

    try:
        while True:
            batch_no += 1
            with engine.connect() as connection:
                with connection.begin():  # commit per batch
                    result = connection.execute(
                        batch_sql,
                        {"n": update_batch_size,
                         "process_datetime": datetime.now()},
                    )
                    deleted, updated = result.fetchone()

            if deleted == 0:
                break

            total_consumed += deleted
            total_marked += updated
            print(f"  [batch {batch_no}] consumed {deleted} staged id(s), "
                  f"flipped {updated} transaction row(s) "
                  f"(running totals: {total_consumed}/{staged} consumed, "
                  f"{total_marked} flipped)")

        print(f"Done: marked is_processed=true for {total_marked} transaction "
              f"row(s) from {total_consumed} staged pos id(s) "
              f"across {batch_no - 1} batch(es)")
    finally:
        # Always clean up the staging table, success or failure.
        _drop_processed_staging_table(engine, schema_name)


def lead_status_closed_existing_update(engine, ce_lead_ids, schema_name, batch_size=5000):
    """
    Set lead_status = 'Closed - Existing' (plus updated_date / updated_by)
    on every Closed-Existing lead, via a direct parametrized UPDATE.

    CE leads carry no Match/Potential row, so they bypass the temp-table
    upsert flow entirely — this is a straight status update keyed on
    lead_id. IDs are updated in batches to keep the IN-list bounded.
    (CE lead counts are small — well within the matched ~57K — so the
    IN-list approach is fine here, unlike the 20M processed-pos path.)
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

    # ------------------------------------------------------------------
    # NOTE: final_df here is the MATCHED output (~57K rows), not the 20M
    # scanned set — so loading it into pandas and doing the sort/dedup
    # below is cheap and stays as-is. Only the processed-pos manifest at
    # the very bottom is at 20M scale, and that's handled separately.
    # ------------------------------------------------------------------
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
    leads_dataframe['account_number'] = leads_dataframe['account_number'].astype('Int64')
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
    #
    # The manifest (processed_pos_ids.csv) can be ~20M rows — every
    # SCANNED transaction, matched or not — so it is streamed in chunks
    # into a staging table and applied with a single set-based UPDATE
    # join. The full id set is never materialized in memory.
    # ------------------------------------------------------------------
    try:
        temporary_folder = storage_config.temporary_folder
        output_bucket    = storage_config.output_bucket_name
        manifest_uri = f"gs://{output_bucket}/{temporary_folder}/processed_pos_ids.csv"
        mark_transactions_processed(engine, manifest_uri, schema_name)
    except Exception as e:
        # Non-fatal: matches are already committed. Leaving rows
        # unprocessed only means they're re-scanned next cycle.
        print(f"WARNING: could not mark transactions processed: {e}")