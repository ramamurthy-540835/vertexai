# pos_pipeline.py
import apache_beam as beam
from apache_beam.options.pipeline_options import (
    PipelineOptions, SetupOptions
)
import argparse, json, logging
import psycopg2
from psycopg2.extras import execute_values
from google.cloud.sql.connector import Connector, IPTypes

from file_reader import read_file_to_dicts
from schema_utils import ensure_table_schema, normalise_col

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Step 1: Parse Pub/Sub Message ────────────────────────────────────────

class ParsePubSubMessage(beam.DoFn):
    def process(self, element):
        try:
            raw = element.decode('utf-8') if isinstance(element, bytes) else element
            msg = json.loads(raw)
            bucket = msg.get('bucket', '')
            name   = msg.get('name', '')
            ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
            allowed = {'csv','tsv','psv','txt','xlsx','xls','parquet','json','jsonl'}
            if bucket and name and ext in allowed:
                yield {'gcs_path': f'gs://{bucket}/{name}', 'filename': name}
            else:
                logger.warning(f'Skipping unsupported file: {name}')
        except Exception as e:
            logger.error(f'ParsePubSubMessage error: {e}')

# ─── Step 2: Read File from GCS ───────────────────────────────────────────

class ReadFileFromGCS(beam.DoFn):
    def process(self, element):
        from google.cloud import storage as gcs_lib
        gcs_path = element['gcs_path']
        filename = element['filename']
        try:
            _, path = gcs_path.split('gs://', 1)
            bucket_name, blob_name = path.split('/', 1)
            client = gcs_lib.Client()
            content = client.bucket(bucket_name).blob(blob_name).download_as_bytes()
            rows = read_file_to_dicts(content, filename)
            logger.info(f'Read {len(rows)} rows from {gcs_path}')
            # Yield in chunks to avoid memory pressure on huge files
            CHUNK = 10000  # ⚙ configurable chunk size
            for i in range(0, len(rows), CHUNK):
                yield {'rows': rows[i:i+CHUNK],
                       'gcs_path': gcs_path,
                       'col_names': list(rows[0].keys()) if rows else []}
        except Exception as e:
            logger.error(f'ReadFileFromGCS error {gcs_path}: {e}')
            # Send to DLQ
            yield beam.pvalue.TaggedOutput('dlq',
                {'error': str(e), 'gcs_path': gcs_path, 'stage': 'read'})

# ─── Step 3: Write to Postgres via IAM (Cloud SQL Connector) ─────────────

class WriteToPostgresIAM(beam.DoFn):
    """
    Uses Cloud SQL Python Connector for IAM-based auth.
    No passwords. Authenticates via the Dataflow worker service account.
    """
    def __init__(self, instance_connection_name, db_name,
                 db_schema, db_table, batch_size=2000):  
        self.instance_connection_name = instance_connection_name  
        self.db_name   = db_name    
        self.db_schema = db_schema  
        self.db_table  = db_table   
        self.batch_size = batch_size

    def setup(self):
        self._connector = Connector()

    def _get_conn(self):
        return self._connector.connect(
            self.instance_connection_name,
            'pg8000',
            db=self.db_name,
            enable_iam_auth=True,  # ← IAM service account auth
            ip_type=IPTypes.PRIVATE,  # ← private IP only
        )

    def start_bundle(self):
        self._conn = self._get_conn()
        self._buffer = []
        self._schema_initialised = False
        self._norm_cols = []

    def process(self, element):
        rows = element['rows']
        col_names = element['col_names']
        gcs_path = element['gcs_path']

        # Ensure schema on first batch
        if not self._schema_initialised:
            self._norm_cols = ensure_table_schema(
                self._conn, self.db_schema, self.db_table,
                col_names, rows
            )
            self._schema_initialised = True

        for row in rows:
            record = {normalise_col(k): v for k, v in row.items()}
            record['_gcs_source']     = gcs_path
            record['_pipeline_run_id'] = element.get('run_id', '')
            self._buffer.append(record)
            if len(self._buffer) >= self.batch_size:
                self._flush()

    def finish_bundle(self):
        if self._buffer:
            self._flush()
        try: self._conn.close()
        except: pass

    def teardown(self):
        try: self._connector.close()
        except: pass

    def _flush(self):
        if not self._buffer or not self._norm_cols:
            return
        all_cols = self._norm_cols + ['_gcs_source', '_pipeline_run_id']
        placeholders = ', '.join(['%s'] * len(all_cols))
        col_list = ', '.join(f'"{c}"' for c in all_cols)
        sql = (f'INSERT INTO {self.db_schema}.{self.db_table} ({col_list})'
               f' VALUES ({placeholders}) ON CONFLICT DO NOTHING')
        tuples = [tuple(r.get(c, None) for c in all_cols) for r in self._buffer]
        cur = self._conn.cursor()
        try:
            execute_values(cur, sql, tuples, page_size=self.batch_size)
            self._conn.commit()
            logger.info(f'Inserted {len(tuples)} rows into {self.db_schema}.{self.db_table}')
            self._buffer = []
        except Exception as e:
            self._conn.rollback()
            logger.error(f'DB flush error: {e}')
            raise
        finally:
            cur.close()

# ─── Pipeline Entry Point ─────────────────────────────────────────────────

def run():
    parser = argparse.ArgumentParser()
    # ⚙ All parameters below are configurable
    parser.add_argument('--subscription',             required=True)
    parser.add_argument('--instance_connection_name', required=True)
    parser.add_argument('--db_name',    required=True)
    parser.add_argument('--db_schema',  required=True)
    parser.add_argument('--db_table',   default='transaction_test')
    parser.add_argument('--batch_size', default=2000, type=int)
    parser.add_argument('--dlq_bucket', default='gs://gcp-gcs-lead-mgmt-us-adt/pos-raw-dlq/')
    known_args, pipeline_args = parser.parse_known_args()

    options = PipelineOptions(pipeline_args, streaming=True)
    options.view_as(SetupOptions).save_main_session = True

    with beam.Pipeline(options=options) as p:
        results = (
            p
            | 'ReadPubSub'   >> beam.io.ReadFromPubSub(
                  subscription=known_args.subscription,
                  with_attributes=False)
            | 'ParseMessage' >> beam.ParDo(ParsePubSubMessage())
            | 'ReadFile'     >> beam.ParDo(ReadFileFromGCS())
                                   .with_outputs('dlq', main='main')
        )
        # Main path
        (results.main
            | 'WriteDB' >> beam.ParDo(WriteToPostgresIAM(
                  instance_connection_name=known_args.instance_connection_name,
                  db_name=known_args.db_name,
                  db_schema=known_args.db_schema,
                  db_table=known_args.db_table,
                  batch_size=known_args.batch_size,
              )))
        # Dead-letter queue path — writes JSON to GCS for investigation
        (results.dlq
            | 'FormatDLQ'  >> beam.Map(lambda x: json.dumps(x))
            | 'WriteDLQ'   >> beam.io.WriteToText(
                  known_args.dlq_bucket + 'errors',
                  file_name_suffix='.json',
                  shard_name_template=''))

if __name__ == '__main__':
    run()
