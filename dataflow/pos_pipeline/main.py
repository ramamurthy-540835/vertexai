"""
POS ETL Pipeline — batch Dataflow.

Reads ONE file from GCS (path passed via --input_file), applies field_map.json
column mapping, and batch-INSERTs into Cloud SQL Postgres via IAM auth.

Pipeline exits when the file is fully loaded. One Dataflow job per file —
the orchestrator (Cloud Workflow) launches multiple jobs in parallel for
runs that contain multiple files.

At end of pipeline, writes one row to batch_audit summarizing the run.
"""

import argparse
import json
import logging
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions
from google.cloud.sql.connector import Connector, IPTypes
from psycopg2.extras import execute_values

from pos_pipeline.file_reader import read_file_to_dicts
from pos_pipeline.schema_utils import load_field_map, apply_field_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
                yield {
                    "rows": rows[i : i + self.chunk_size],
                    "gcs_path": gcs_path,
                }
        except Exception as e:
            logger.error(f"ReadFileFromGCS error for {gcs_path}: {e}")
            # Re-raise so the Dataflow job fails — the workflow will detect this
            # and abort the run before triggering matching.
            raise


# ─────────────────────────────────────────────────────────────
# Step 2 — Apply column mapping and batch-INSERT into Postgres via IAM
# ─────────────────────────────────────────────────────────────
class WriteToPostgresIAM(beam.DoFn):
    """Maps each row through field_map.json and batch-INSERTs into Postgres.

    At teardown, writes a single batch_audit row summarizing this run.
    """

    def __init__(
        self,
        instance_connection_name: str,
        db_name: str,
        db_schema: str,
        db_user: str,
        db_table: str,
        field_map: dict,
        batch_id: str,
        manifest_run_id: str,
        input_file: str,
        batch_size: int = 2000,
    ):
        self.instance_connection_name = instance_connection_name
        self.db_name = db_name
        self.db_schema = db_schema
        self.db_table = db_table
        self.db_user = db_user
        self.field_map = field_map
        self.batch_size = batch_size

        # Audit tracking
        self.batch_id = batch_id
        self.manifest_run_id = manifest_run_id
        self.input_file = input_file
        self.total_rows_written = 0
        self.start_time = None
        self._had_error = False

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
        if self.start_time is None:
            self.start_time = datetime.now(timezone.utc)

    def process(self, element):
        rows = element["rows"]
        gcs_path = element["gcs_path"]
        for raw_row in rows:
            mapped = apply_field_map(raw_row, self.field_map)
            if mapped is None:
                continue
            self._buffer.append(mapped)
            if len(self._buffer) >= self.batch_size:
                self._flush()

    def finish_bundle(self):
        if self._buffer:
            self._flush()
        try:
            self._conn.close()
        except Exception:
            pass

    def teardown(self):
        """Write a single batch_audit row at end of pipeline.

        Soft-fails: an audit-write failure must NOT fail the pipeline, since
        the data is already committed. This is observability, not correctness.
        """
        # If no batch_id was passed (e.g., a manual/standalone run), skip the
        # audit row rather than inserting a NULL batch_id.
        if not self.batch_id:
            logger.warning("No batch_id provided — skipping batch_audit row.")
            return

        status = "failed" if self._had_error else "succeeded"

        try:
            conn = self._get_conn()
            cur = conn.cursor()

            audit_sql = f'''
                INSERT INTO "{self.db_schema}".batch_audit
                    (batch_id, data_type, total_volume, success_count,
                     stage, status, start_date, end_date, comments)
                VALUES
                    (%s, 'pos', %s, %s,
                     'ingestion', %s, %s, %s, %s)
            '''

            comments = json.dumps({
                "file": self.input_file,
                "manifest_run_id": self.manifest_run_id,
                "db_schema": self.db_schema,
                "db_table": self.db_table,
            })

            cur.execute(audit_sql, (
                self.batch_id,                      # batch_id (uuid)
                self.total_rows_written,            # total_volume
                self.total_rows_written,            # success_count
                status,                             # status (succeeded / failed)
                self.start_time,                    # start_date
                datetime.now(timezone.utc),         # end_date
                comments,                           # comments (JSON string)
            ))
            conn.commit()
            cur.close()
            conn.close()

            logger.info(
                f"Audit row written: file={self.input_file}, "
                f"batch_id={self.batch_id}, rows={self.total_rows_written}, "
                f"status={status}"
            )
        except Exception as e:
            # Audit failures should NOT fail the pipeline.
            logger.error(f"Failed to write audit row (non-fatal): {e}")

    # Postgres bind-parameter hard limit is 65535. Stay safely under it.
    MAX_PG_PARAMS = 60000

    def _flush(self):
        """Insert buffered rows, sub-batching to respect Postgres's 65535-parameter limit."""
        if not self._buffer:
            return

        columns = list(self._buffer[0].keys())
        num_cols = len(columns)
        col_list = ", ".join(columns)

        # Max rows per INSERT so that rows * cols stays under the param limit
        max_rows_per_insert = max(1, MAX_PG_PARAMS // num_cols)

        cur = self._conn.cursor()
        try:
            # Split the buffer into sub-batches
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
            self.total_rows_written += len(self._buffer)
            logger.info(f"Inserted {len(self._buffer)} rows into {self.db_table} "
                        f"({num_cols} cols, sub-batches of {max_rows_per_insert})")
        except Exception:
            self._conn.rollback()
            self._had_error = True
            raise
        finally:
            cur.close()
            self._buffer.clear()


# ─────────────────────────────────────────────────────────────
# Pipeline entry point — BATCH
# ─────────────────────────────────────────────────────────────
def run():
    parser = argparse.ArgumentParser()

    # ── Single-file input (set by the workflow per Dataflow job) ────────
    parser.add_argument(
        "--input_file",
        required=True,
        help="Full GCS path of the single file to process, e.g. gs://bucket/path/file.csv",
    )

    # ── Database target ─────────────────────────────────────────────────
    parser.add_argument("--instance_connection_name", required=True,
                        help="PROJECT:REGION:INSTANCE")
    parser.add_argument("--db_name", required=True)
    parser.add_argument("--db_schema", required=True)
    parser.add_argument("--db_table", required=True)
    parser.add_argument("--db_user", required=True,
                        help="Postgres IAM username (SA email minus .gserviceaccount.com)")

    # ── Field mapping ───────────────────────────────────────────────────
    parser.add_argument(
        "--field_map_path",
        required=True,
        help="Local path or GCS path (gs://...) to field_map.json",
    )

    # ── Performance ─────────────────────────────────────────────────────
    parser.add_argument("--batch_size", default=2000, type=int)
    parser.add_argument("--chunk_size", default=10000, type=int)

    # ── Audit / correlation ─────────────────────────────────────────────
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

    # streaming=False → batch pipeline. Beam exits when input is exhausted.
    options = PipelineOptions(pipeline_args, streaming=False)
    options.view_as(SetupOptions).save_main_session = True

    with beam.Pipeline(options=options) as p:
        (
            p
            | "StartWithInputPath" >> beam.Create([known_args.input_file])
            | "ReadFile" >> beam.ParDo(ReadFileFromGCS(chunk_size=known_args.chunk_size))
            | "WriteDB" >> beam.ParDo(
                WriteToPostgresIAM(
                    instance_connection_name=known_args.instance_connection_name,
                    db_name=known_args.db_name,
                    db_schema=known_args.db_schema,
                    db_table=known_args.db_table,
                    db_user=known_args.db_user,
                    field_map=field_map,
                    batch_size=known_args.batch_size,
                    batch_id=known_args.batch_id,
                    manifest_run_id=known_args.manifest_run_id,
                    input_file=known_args.input_file,
                )
            )
        )


if __name__ == "__main__":
    run()