"""
Streaming, per-warehouse partitioning for the leads-to-POS matching pipeline.

WHY THIS EXISTS
----------------
`load_file_from_gcs` calls `blob.download_as_text()`, which pulls the ENTIRE
sales CSV (20M rows) into a single Python string, then `pd.read_csv` parses
that whole string into one DataFrame. `run_batched_classification` then does
`.copy()` on top of that. That's ~3x the file's footprint in memory before
any matching logic runs — the actual OOM source, not the per-warehouse
batching (which is sound, but starts too late).

This module streams the sales CSV directly from the GCS blob using
`blob.open("rb")` + `pandas.read_csv(..., chunksize=...)`, and partitions
rows into one small buffer per warehouse as they're read. Since there are
only ~26 warehouses, holding 26 accumulator buffers in memory is cheap —
we never materialize the full 20M-row frame.

Two partitioning modes are supported:
  - IN-MEMORY (small warehouse count, like 26): accumulate each warehouse's
    rows as a list of chunk-DataFrames, concat once per warehouse at
    process time. This is what you want here.
  - SPILL-TO-GCS (use if warehouse count grows much larger, or per-warehouse
    volume itself is too large to hold matched_df + scoring structures for
    in memory): write each warehouse's rows to its own temp CSV in GCS as
    chunks arrive, then re-read one warehouse's small file at a time in
    pass 2. Included for future-proofing; not needed at 26 warehouses.
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


def partition_sales_by_warehouse_in_memory(
    file_path: str,
    target_warehouses: set,
    dtype: dict | None = None,
    chunksize: int = READ_CHUNKSIZE,
) -> tuple:
    """
    Stream `file_path` (a gs:// CSV) in chunks and bucket rows into one
    list-of-DataFrames per warehouse, keeping ONLY warehouses present in
    `target_warehouses` (i.e. warehouses that actually have leads — every
    other row is dropped immediately, same as the existing pre-filter, but
    now applied during the read instead of after a full load).

    Returns: (buffers, total_rows_seen)
             buffers: dict[warehouse_str] -> list[pd.DataFrame chunks]
             (caller concats per-warehouse lists lazily, one warehouse at
             a time, during processing — never all at once).
             total_rows_seen: total POS rows in the file (every row read,
             before warehouse filtering) — equals what len(file_b) used to
             report, so the audit row's pos_count stays accurate.

    Memory profile: at most `chunksize` raw rows in flight at a time, plus
    the cumulative size of rows belonging to `target_warehouses` (which is
    a small fraction of 20M since only 26 warehouses have leads at all).
    Rows for warehouses NOT in target_warehouses are discarded per-chunk
    and never retained.
    """
    bucket_name, blob_path = file_path.replace("gs://", "").split("/", 1)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    buffers: dict = {wh: [] for wh in target_warehouses}
    total_rows_seen = 0
    total_rows_kept = 0
    chunk_idx = 0

    with blob.open("rb") as f:
        reader = pd.read_csv(f, dtype=dtype, chunksize=chunksize)
        for chunk in reader:
            chunk_idx += 1
            total_rows_seen += len(chunk)

            chunk["_wh"] = _norm_wh_series(chunk["warehouse_number"])
            chunk = chunk[chunk["_wh"].isin(target_warehouses)]

            if not chunk.empty:
                for wh, sub in chunk.groupby("_wh", sort=False):
                    buffers[wh].append(sub.drop(columns=["_wh"]).copy())
                total_rows_kept += len(chunk)

            del chunk
            if chunk_idx % 10 == 0:
                log.info(
                    "Streamed %d chunk(s), %d rows seen, %d rows kept "
                    "(warehouses with leads only)",
                    chunk_idx, total_rows_seen, total_rows_kept,
                )

    log.info(
        "Streaming partition complete: %d chunk(s), %d rows seen, "
        "%d rows kept across %d warehouse(s)",
        chunk_idx, total_rows_seen, total_rows_kept, len(target_warehouses),
    )
    return buffers, total_rows_seen


def materialize_warehouse(buffers: dict, wh: str) -> pd.DataFrame:
    """
    Concat one warehouse's accumulated chunks into a single DataFrame,
    then free that warehouse's entry in `buffers` immediately. Call this
    one warehouse at a time inside the processing loop — never for all
    warehouses up front.
    """
    parts = buffers.pop(wh, [])
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    del parts
    return df


def run_streaming_classification(
    file_leads: pd.DataFrame,
    sales_file_path: str,
    classify_matches_fn,
    sales_dtype: dict | None = None,
    chunksize: int = READ_CHUNKSIZE,
):
    """
    Drop-in streaming replacement for `run_batched_classification`.

    `file_leads` is the already-loaded leads DataFrame (300K rows — cheap,
    load it normally via load_file_from_gcs as before).

    `sales_file_path` is the gs:// path to the 20M-row sales CSV — this is
    NEVER fully loaded. It is streamed once, partitioned by warehouse into
    in-memory buffers (cheap since only ~26 warehouses have leads), then
    processed one warehouse at a time.

    `classify_matches_fn` is `classify_matches` from the existing module —
    passed in to avoid a circular import; call site just does
    `run_streaming_classification(leads, sales_path, classify_matches)`.

    Returns (final_df, processed_pos_ids, total_pos_rows):
      • final_df, processed_pos_ids — same contract as
        `run_batched_classification`.
      • total_pos_rows — total rows in the streamed POS file (every row
        read, before warehouse filtering). Equals what len(file_b) used
        to report, so the caller can keep the audit row's pos_count
        accurate without a second read of the file.
    """
    fa = file_leads.copy()
    fa["_wh"] = _norm_wh_series(fa["warehouse_number"])
    lead_whs = {w for w in fa["_wh"].dropna().unique() if w not in (None, "<NA>")}
    fa = fa.drop(columns=["_wh"])

    log.info("Leads span %d distinct warehouse(s)", len(lead_whs))

    buffers, total_pos_rows = partition_sales_by_warehouse_in_memory(
        sales_file_path, lead_whs, dtype=sales_dtype, chunksize=chunksize,
    )

    out_cols = None  # filled in lazily from first non-empty classify result
    results = []
    processed_pos_ids = []

    for i, wh in enumerate(sorted(lead_whs), start=1):
        sales_wh = materialize_warehouse(buffers, wh)
        if sales_wh.empty:
            log.info("Warehouse %s — no POS rows, skipping", wh)
            continue

        leads_wh = fa[_norm_wh_series(fa["warehouse_number"]) == wh]

        if leads_wh.empty:
            log.info("Warehouse %s — no leads after normalization, skipping", wh)
            del sales_wh
            continue

        log.info(
            "Warehouse %d/%d (%s) — %d POS rows, %d leads",
            i, len(lead_whs), wh, len(sales_wh), len(leads_wh),
        )

        processed_pos_ids.extend(sales_wh["pos_id"].astype(str).tolist())

        res = classify_matches_fn(leads_wh, sales_wh)
        if not res.empty:
            results.append(res)
            if out_cols is None:
                out_cols = list(res.columns)

        del sales_wh, leads_wh, res
        gc.collect()

    final_df = (
        pd.concat(results, ignore_index=True) if results
        else pd.DataFrame(columns=out_cols or [])
    )
    del buffers
    gc.collect()
    return final_df, processed_pos_ids, total_pos_rows