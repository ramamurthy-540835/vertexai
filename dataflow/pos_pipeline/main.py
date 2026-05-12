"""
POS ETL Pipeline — batch Dataflow.

Reads ONE file from GCS (path passed via --input_file), applies field_map.json
column mapping, and batch-INSERTs into Cloud SQL Postgres via IAM auth.

Pipeline exits when the file is fully loaded. One Dataflow job per file —
the orchestrator (Cloud Workflow) launches multiple jobs in parallel for
runs that contain multiple files.
"""

import argparse
import logging

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
    """Maps each row through field_map.json and batch-INSERTs into Postgres."""

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
        gcs_path = element["gcs_path"]
        for raw_row in rows:
            mapped = apply_field_map(raw_row, self.field_map)
            if mapped is None:
                continue
            mapped["_gcs_source"] = gcs_path
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
        try:
            self._connector.close()
        except Exception:
            pass

    def _flush(self):
        """Insert all buffered rows in a single multi-row INSERT."""
        if not self.buffer:
            return
        
        conn = self._get_conn()
        cur = conn.cursor()
        
        try:
            # Use the keys from the first row as the column list (all rows
            # should have the same keys after field_map applies them).
            columns = list(self.buffer[0].keys())
            col_list = ", ".join(columns)
            
            # Build (%s, %s, ...) for one row, repeated for each row in batch
            row_ph = "(" + ", ".join(["%s"] * len(columns)) + ")"
            values_ph = ", ".join([row_ph] * len(self.buffer))
            
            sql = (
                f'INSERT INTO "{self.db_schema}".{self.db_table} ({col_list}) '
                f'VALUES {values_ph} '
                f'ON CONFLICT DO NOTHING'
            )
            
            # Flatten: [row1.col1, row1.col2, ..., row2.col1, row2.col2, ...]
            flat_values = tuple(
                row.get(col)
                for row in self.buffer
                for col in columns
            )
            
            cur.execute(sql, flat_values)
            conn.commit()
            logger.info(f"Inserted {len(self.buffer)} rows into {self.db_table}")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self.buffer.clear()


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

    known_args, pipeline_args = parser.parse_known_args()

    field_map = load_field_map(known_args.field_map_path)
    if not field_map:
        raise ValueError(
            f"field_map.json loaded empty from {known_args.field_map_path}"
        )
    logger.info(f"Field map loaded: {len(field_map)} column mappings")
    logger.info(f"Processing single file: {known_args.input_file}")

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
                )
            )
        )


if __name__ == "__main__":
    run()