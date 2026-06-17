import csv, os, sys

csv_path   = os.environ["CSV_FILE"]
batch_size = int(os.environ["BATCH_SIZE"])
schema     = os.environ["SCHEMA_NAME"]
out_path   = os.environ["OUTPUT_SQL"]

with open(csv_path, newline="") as f:
    reader = csv.DictReader(f)
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    rows = [(r["lead_id"].strip(), r["week"].strip()) for r in reader]

if not rows:
    sys.exit("CSV contains no data rows")

batches = [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]

with open(out_path, "w") as sql:
    sql.write("-- Auto-generated batched UPDATE for {}.lead\n".format(schema))
    sql.write("-- Total rows: {:,}  Batch size: {:,}  Batches: {:,}\n\n".format(
        len(rows), batch_size, len(batches)))
    sql.write("BEGIN;\n\n")

    for i, batch in enumerate(batches, 1):
        sql.write("-- Batch {}/{} ({} rows)\n".format(i, len(batches), len(batch)))
        sql.write("UPDATE {}.lead AS t\n   SET week = v.week\n  FROM (\n    VALUES\n".format(schema))
        values = ["      ({}::bigint, {!r})".format(int(lead_id), week) for lead_id, week in batch]
        sql.write(",\n".join(values))
        sql.write("\n  ) AS v(lead_id, week)\n WHERE t.lead_id = v.lead_id;\n\n")

    sql.write("COMMIT;\n")

print("SQL written to", out_path)
print("Rows: {:,}  |  Batches: {:,}".format(len(rows), len(batches)))