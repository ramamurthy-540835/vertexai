"""
POS ETL Pipeline — batch Dataflow.

Reads ONE file from GCS, applies field_map.json column mapping, batch-INSERTs
into Cloud SQL Postgres via IAM auth.

Multi-worker safe. Each WriteToPostgresIAM worker yields its per-flush row
counts; CombineGlobally(sum) collapses them into one total; WriteBatchAudit
writes a SINGLE batch_audit row per file regardless of worker count.
"""

import argparse
import json
import logging
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions
from apache_beam.transforms.window import GlobalWindow
from apache_beam.utils.windowed_value import WindowedValue
from google.cloud.sql.connector import Connector, IPTypes

from pos_pipeline.file_reader import read_file_to_dicts
from pos_pipeline.schema_utils import load_field_map, apply_field_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Postgres bind-parameter hard limit is 65535. Stay safely under it.
MAX_PG_PARAMS = 60000


# ─────────────────────────────────────────────────────────────
# Step 1 — Read the single input file from GCS, yield row chunks
# ─────────────────────────────────────────────────────────────
class ReadFileFromGCS(beam.DoFn):
    """Downloads one file from GCS and yields chunks of parsed rows."""

    def __init__(self, chunk_size: int = 10000):
        self.chunk_size = chunk_size

    def process(self, gcs_path: str):
        from google.cloud import storage as gcs_lib
        try:
            _, path = gcs_path.split("gs://", 1)
            bucket_name, blob_name = path.split("/", 1)
            filename = blob_name.split("/")[-1]

            client = gcs_lib.Client()
            content = client.bucket(bucket_name).blob(blob_name).download_as_bytes()

            rows = read_file_to_dicts(content, filename)
            logger.info(f"Read {len(rows)} rows from {gcs_path}")

            for i in range(0, len(rows), self.chunk_size):
                yield {"rows": rows[i : i + self.chunk_size]}
        except Exception as e:
            logger.error(f"ReadFileFromGCS error for {gcs_path}: {e}")
            raise


# ─────────────────────────────────────────────────────────────
# Step 2 — Apply column mapping and batch-INSERT into Postgres via IAM.
#          Yields per-flush row counts (integers) for downstream aggregation.
#          Does NOT write the audit row — that's WriteBatchAudit's job.
# ─────────────────────────────────────────────────────────────
class WriteToPostgresIAM(beam.DoFn):
    """Maps each row through field_map.json and batch-INSERTs into Postgres.

    Yields the count of rows successfully written per flush, so a downstream
    CombineGlobally(sum) + WriteBatchAudit can produce ONE audit row.
    """

    def __init__(
        self,
        instance_connection_name: str,
        db_name: str,
        db_schema: str,
        db_user: str,
        db_table: str,
        field_map: dict,
        batch_size: int = 2000,
    ):
        self.instance_connection_name = instance_connection_name
        self.db_name = db_name
        self.db_schema = db_schema
        self.db_table = db_table
        self.db_user = db_user
        self.field_map = field_map
        self.batch_size = batch_size

    def setup(self):
        self._connector = Connector()

    def _get_conn(self):
        return self._connector.connect(
            self.instance_connection_name,
            "pg8000",
            db=self.db_name,
            user=self.db_user,
            enable_iam_auth=True,
            ip_type=IPTypes.PRIVATE,
        )

    def start_bundle(self):
        self._conn = self._get_conn()
        self._buffer = []

    def process(self, element):
        rows = element["rows"]
        for raw_row in rows:
            mapped = apply_field_map(raw_row, self.field_map)
            if mapped is None:
                continue
            self._buffer.append(mapped)
            if len(self._buffer) >= self.batch_size:
                written = self._flush()
                if written:
                    yield written

    def finish_bundle(self):
        """Flush any tail of the buffer.

        finish_bundle yields differently from process: it must wrap values
        in WindowedValue. This is a Beam idiom.
        """
        if self._buffer:
            written = self._flush()
            if written:
                yield WindowedValue(written, 0, [GlobalWindow()])
        try:
            self._conn.close()
        except Exception:
            pass

    def _flush(self):
        """Insert buffered rows, sub-batching to respect Postgres's 65535-param limit.

        Returns the number of rows successfully committed.
        Returns 0 if the buffer was empty.
        """
        if not self._buffer:
            return 0

        columns = list(self._buffer[0].keys())
        num_cols = len(columns)
        col_list = ", ".join(columns)
        max_rows_per_insert = max(1, MAX_PG_PARAMS // num_cols)

        cur = self._conn.cursor()
        try:
            for start in range(0, len(self._buffer), max_rows_per_insert):
                sub_batch = self._buffer[start:start + max_rows_per_insert]

                row_ph = "(" + ", ".join(["%s"] * num_cols) + ")"
                values_ph = ", ".join([row_ph] * len(sub_batch))

                sql = (
                    f'INSERT INTO "{self.db_schema}".{self.db_table} ({col_list}) '
                    f'VALUES {values_ph} '
                    f'ON CONFLICT DO NOTHING'
                )

                flat_values = tuple(
                    row.get(col)
                    for row in sub_batch
                    for col in columns
                )

                cur.execute(sql, flat_values)

            self._conn.commit()
            flushed = len(self._buffer)
            logger.info(
                f"Inserted {flushed} rows into {self.db_table} "
                f"({num_cols} cols, sub-batches of {max_rows_per_insert})"
            )
            return flushed
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()
            self._buffer.clear()


# ─────────────────────────────────────────────────────────────
# Step 3 — Write ONE batch_audit row, on the globally-summed total.
#          Runs exactly once per file, regardless of worker count.
# ─────────────────────────────────────────────────────────────
class WriteBatchAudit(beam.DoFn):
    """Writes a single batch_audit row using the globally-summed total.

    Receives the output of beam.CombineGlobally(sum), which is one integer
    (the total rows written across ALL workers for this Dataflow job).

    Soft-fails: an audit-write failure must NOT fail the pipeline.
    """

    def __init__(
        self,
        instance_connection_name: str,
        db_name: str,
        db_schema: str,
        db_user: str,
        batch_id: str,
        manifest_run_id: str,
        input_file: str,
        start_time_iso: str,
    ):
        self.instance_connection_name = instance_connection_name
        self.db_name = db_name
        self.db_schema = db_schema
        self.db_user = db_user
        self.batch_id = batch_id
        self.manifest_run_id = manifest_run_id
        self.input_file = input_file
        self.start_time_iso = start_time_iso

    def setup(self):
        self._connector = Connector()

    def _get_conn(self):
        return self._connector.connect(
            self.instance_connection_name,
            "pg8000",
            db=self.db_name,
            user=self.db_user,
            enable_iam_auth=True,
            ip_type=IPTypes.PRIVATE,
        )

    def process(self, total_rows):
        # total_rows is one integer — output of CombineGlobally(sum).
        if not self.batch_id:
            logger.warning("No batch_id provided — skipping batch_audit row.")
            return

        try:
            conn = self._get_conn()
            cur = conn.cursor()

            audit_sql = f'''
                INSERT INTO "{self.db_schema}".batch_audit
                    (batch_id, data_type, total_volume, success_count,
                     stage, status, start_date, end_date, comments)
                VALUES
                    (%s, 'pos', %s, %s,
                     'ingestion', 'succeeded', %s, %s, %s)
            '''

            comments = json.dumps({
                "file": self.input_file,
                "manifest_run_id": self.manifest_run_id,
                "db_schema": self.db_schema,
            })

            cur.execute(audit_sql, (
                self.batch_id,
                int(total_rows),
                int(total_rows),
                self.start_time_iso,
                datetime.now(timezone.utc),
                comments,
            ))
            conn.commit()
            cur.close()
            conn.close()

            logger.info(
                f"Audit row written: file={self.input_file}, "
                f"batch_id={self.batch_id}, rows={total_rows}"
            )
        except Exception as e:
            # Audit failures must not fail the pipeline.
            logger.error(f"Failed to write audit row (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────
# Pipeline entry point — BATCH
# ─────────────────────────────────────────────────────────────
def run():
    parser = argparse.ArgumentParser()

    parser.add_argument("--input_file", required=True,
                        help="Full GCS path of the single file to process")
    parser.add_argument("--instance_connection_name", required=True,
                        help="PROJECT:REGION:INSTANCE")
    parser.add_argument("--db_name", required=True)
    parser.add_argument("--db_schema", required=True)
    parser.add_argument("--db_table", required=True)
    parser.add_argument("--db_user", required=True,
                        help="Postgres IAM username")
    parser.add_argument("--field_map_path", required=True,
                        help="Local or GCS path to field_map.json")
    parser.add_argument("--batch_size", default=2000, type=int)
    parser.add_argument("--chunk_size", default=10000, type=int)
    parser.add_argument("--batch_id", required=False, default=None,
                        help="UUID identifying this batch run (for audit table)")
    parser.add_argument("--manifest_run_id", required=False, default=None,
                        help="Human-readable run identifier from manifest")

    known_args, pipeline_args = parser.parse_known_args()

    field_map = load_field_map(known_args.field_map_path)
    if not field_map:
        raise ValueError(
            f"field_map.json loaded empty from {known_args.field_map_path}"
        )
    logger.info(f"Field map loaded: {len(field_map)} column mappings")
    logger.info(f"Processing single file: {known_args.input_file}")
    logger.info(f"batch_id={known_args.batch_id}, manifest_run_id={known_args.manifest_run_id}")

    # Captured once in the launcher process. Used as the audit start_date.
    # Note: with multi-worker, individual worker start_times aren't meaningful —
    # this is the pipeline's logical start.
    start_time_iso = datetime.now(timezone.utc).isoformat()

    options = PipelineOptions(pipeline_args, streaming=False)
    options.view_as(SetupOptions).save_main_session = True

    with beam.Pipeline(options=options) as p:
        # Per-worker row counts flow into `counts`.
        counts = (
            p
            | "StartWithInputPath" >> beam.Create([known_args.input_file])
            | "ReadFile" >> beam.ParDo(
                ReadFileFromGCS(chunk_size=known_args.chunk_size))
            | "WriteDB" >> beam.ParDo(
                WriteToPostgresIAM(
                    instance_connection_name=known_args.instance_connection_name,
                    db_name=known_args.db_name,
                    db_schema=known_args.db_schema,
                    db_table=known_args.db_table,
                    db_user=known_args.db_user,
                    field_map=field_map,
                    batch_size=known_args.batch_size,
                )
            )
        )

        # Sum across ALL workers → single value → ONE audit row.
        (
            counts
            | "SumRowCounts" >> beam.CombineGlobally(sum)
            | "WriteAudit" >> beam.ParDo(
                WriteBatchAudit(
                    instance_connection_name=known_args.instance_connection_name,
                    db_name=known_args.db_name,
                    db_schema=known_args.db_schema,
                    db_user=known_args.db_user,
                    batch_id=known_args.batch_id,
                    manifest_run_id=known_args.manifest_run_id,
                    input_file=known_args.input_file,
                    start_time_iso=start_time_iso,
                )
            )
        )


if __name__ == "__main__":
    run()