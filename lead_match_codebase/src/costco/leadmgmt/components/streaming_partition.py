"""
Streaming, per-warehouse partitioning for the leads-to-POS matching pipeline.

WHY THIS EXISTS
----------------
`load_file_from_gcs` pulls the ENTIRE sales CSV (20M rows) into memory before
any matching runs — the original OOM source. But simply filtering by warehouse
during the read is NOT enough here: in this dataset EVERY transaction belongs
to one of the ~26 warehouses that have leads (observed: "rows kept == rows
seen"). So buffering all surviving warehouses in memory == buffering the whole
20M-row file == still OOM.

This module therefore SPILLS to GCS:

  PASS 1 (spill): stream the sales CSV with `blob.open("rb")` +
    `pandas.read_csv(chunksize=...)`. For each chunk (only ONE chunk in RAM at
    a time), split by warehouse and append each warehouse's slice to a per-
    warehouse part file in a temp GCS prefix. Nothing accumulates in memory
    across chunks.

  PASS 2 (process): for each warehouse that has leads, read back ONLY that
    warehouse's part files (one warehouse in RAM at a time), run
    classify_matches, collect the (much smaller) match output, delete that
    warehouse's temp files, and move on.

Peak memory = one chunk (pass 1) or one warehouse's transactions (pass 2),
plus the leads frame (small) and the accumulated match results (small relative
to 20M input). The full sales file is never resident.

NOTE on Cloud Run: /tmp is RAM-backed (tmpfs), so spilling to local disk would
still consume memory. We spill to GCS for that reason.
"""

import gc
import logging

import pandas as pd
from google.cloud import storage

log = logging.getLogger(__name__)

READ_CHUNKSIZE = 250_000  # rows per streamed chunk; tune to container memory


def _norm_wh_series(s: pd.Series) -> pd.Series:
    """Normalize warehouse_number the same way classify_matches.py does."""
    return pd.to_numeric(s, errors="coerce").astype("Int64").astype("string")


def _split_gs(uri: str):
    """Split gs://bucket/path → (bucket, path)."""
    bucket_name, path = uri.replace("gs://", "").split("/", 1)
    return bucket_name, path


# ==============================================================
# PASS 1 — stream and spill per-warehouse part files to GCS
# ==============================================================
def spill_sales_by_warehouse_to_gcs(
    file_path: str,
    target_warehouses: set,
    tmp_prefix: str,
    dtype: dict | None = None,
    chunksize: int = READ_CHUNKSIZE,
) -> tuple:
    """
    Stream `file_path` (gs:// CSV) and write each warehouse's rows to
    per-warehouse part files under `tmp_prefix` (a gs:// prefix), keeping
    only one chunk in memory at a time.

    Layout written:
        {tmp_prefix}/wh={wh}/part-{chunk_idx:05d}.csv

    Returns (wh_parts, total_rows_seen, wh_row_counts):
        wh_parts:       dict[wh_str] -> list[gs:// part uris]
        total_rows_seen total POS rows in the file (== old len(file_b))
        wh_row_counts:  dict[wh_str] -> int rows spilled for that warehouse
    """
    src_bucket_name, src_blob_path = _split_gs(file_path)
    tmp_bucket_name, tmp_root = _split_gs(tmp_prefix)

    client = storage.Client()
    src_blob = client.bucket(src_bucket_name).blob(src_blob_path)
    tmp_bucket = client.bucket(tmp_bucket_name)

    wh_parts: dict = {}
    wh_row_counts: dict = {}
    total_rows_seen = 0
    total_rows_kept = 0
    chunk_idx = 0

    with src_blob.open("rb") as f:
        reader = pd.read_csv(f, dtype=dtype, chunksize=chunksize, low_memory=False)
        for chunk in reader:
            chunk_idx += 1
            total_rows_seen += len(chunk)

            chunk["_wh"] = _norm_wh_series(chunk["warehouse_number"])
            chunk = chunk[chunk["_wh"].isin(target_warehouses)]

            if not chunk.empty:
                for wh, sub in chunk.groupby("_wh", sort=False):
                    wh = str(wh)
                    sub = sub.drop(columns=["_wh"])
                    part_path = f"{tmp_root}/wh={wh}/part-{chunk_idx:05d}.csv"
                    tmp_bucket.blob(part_path).upload_from_string(
                        sub.to_csv(index=False), content_type="text/csv"
                    )
                    uri = f"gs://{tmp_bucket_name}/{part_path}"
                    wh_parts.setdefault(wh, []).append(uri)
                    wh_row_counts[wh] = wh_row_counts.get(wh, 0) + len(sub)
                total_rows_kept += len(chunk)

            del chunk
            if chunk_idx % 10 == 0:
                log.info(
                    "Spill: %d chunk(s), %d rows seen, %d rows kept",
                    chunk_idx, total_rows_seen, total_rows_kept,
                )

    log.info(
        "Spill complete: %d chunk(s), %d rows seen, %d rows kept across %d wh",
        chunk_idx, total_rows_seen, total_rows_kept, len(wh_parts),
    )
    # Per-warehouse size distribution — watch for any single warehouse big
    # enough to OOM on its own (it can't be sub-split: CE/primary logic needs
    # the whole warehouse together).
    if wh_row_counts:
        top = sorted(wh_row_counts.items(), key=lambda x: -x[1])[:10]
        log.info("Largest warehouses by row count: %s", top)

    return wh_parts, total_rows_seen, wh_row_counts


def _read_warehouse_parts(part_uris: list, dtype: dict | None) -> pd.DataFrame:
    """Read and concat all part files for ONE warehouse into a single frame."""
    client = storage.Client()
    frames = []
    for uri in part_uris:
        bucket_name, blob_path = _split_gs(uri)
        blob = client.bucket(bucket_name).blob(blob_path)
        from io import StringIO
        frames.append(pd.read_csv(StringIO(blob.download_as_text()), dtype=dtype))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _delete_parts(part_uris: list) -> None:
    """Delete a warehouse's temp part files from GCS after processing."""
    client = storage.Client()
    for uri in part_uris:
        bucket_name, blob_path = _split_gs(uri)
        try:
            client.bucket(bucket_name).blob(blob_path).delete()
        except Exception as e:  # noqa: BLE001 — cleanup must not fail the run
            log.warning("Failed to delete temp part %s: %s", uri, e)


# ==============================================================
# ORCHESTRATOR — spill then process one warehouse at a time
# ==============================================================
def run_streaming_classification(
    file_leads: pd.DataFrame,
    sales_file_path: str,
    classify_matches_fn,
    tmp_prefix: str,
    sales_dtype: dict | None = None,
    chunksize: int = READ_CHUNKSIZE,
):
    """
    Memory-bounded replacement for `run_batched_classification`.

    `file_leads`        already-loaded leads DataFrame (small; ~317K rows).
    `sales_file_path`   gs:// path to the 20M-row sales CSV — NEVER fully
                        loaded; streamed and spilled per warehouse to GCS.
    `classify_matches_fn`  the existing `classify_matches` (passed in to
                        avoid a circular import).
    `tmp_prefix`        gs:// prefix for temp per-warehouse part files;
                        cleaned up as each warehouse finishes.

    Returns (final_df, processed_pos_ids, total_pos_rows):
        same contract as before; total_pos_rows == old len(file_b), so the
        caller can keep the audit row's pos_count accurate.
    """
    fa = file_leads.copy()
    fa["_wh"] = _norm_wh_series(fa["warehouse_number"])
    lead_whs = {w for w in fa["_wh"].dropna().unique() if w not in (None, "<NA>")}
    fa = fa.drop(columns=["_wh"])
    log.info("Leads span %d distinct warehouse(s)", len(lead_whs))

    # PASS 1 — spill to GCS (one chunk in RAM at a time).
    wh_parts, total_pos_rows, wh_row_counts = spill_sales_by_warehouse_to_gcs(
        sales_file_path, lead_whs, tmp_prefix,
        dtype=sales_dtype, chunksize=chunksize,
    )

    # PASS 2 — process one warehouse at a time (one warehouse in RAM).
    out_cols = None
    results = []
    processed_pos_ids = []

    for i, wh in enumerate(sorted(wh_parts.keys()), start=1):
        part_uris = wh_parts[wh]
        sales_wh = _read_warehouse_parts(part_uris, sales_dtype)
        if sales_wh.empty:
            _delete_parts(part_uris)
            continue

        leads_wh = fa[_norm_wh_series(fa["warehouse_number"]) == wh]
        if leads_wh.empty:
            log.info("Warehouse %s — no leads after normalization, skipping", wh)
            _delete_parts(part_uris)
            del sales_wh
            continue

        log.info(
            "Warehouse %d/%d (%s) — %d POS rows, %d leads",
            i, len(wh_parts), wh, len(sales_wh), len(leads_wh),
        )

        processed_pos_ids.extend(sales_wh["pos_id"].astype(str).tolist())

        res = classify_matches_fn(leads_wh, sales_wh)
        if not res.empty:
            results.append(res)
            if out_cols is None:
                out_cols = list(res.columns)

        # Free this warehouse before moving to the next, and remove its
        # temp part files from GCS.
        _delete_parts(part_uris)
        del sales_wh, leads_wh, res
        gc.collect()

    final_df = (
        pd.concat(results, ignore_index=True) if results
        else pd.DataFrame(columns=out_cols or [])
    )
    del results
    gc.collect()
    return final_df, processed_pos_ids, total_pos_rows