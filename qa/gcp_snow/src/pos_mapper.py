"""
pos_mapper.py
-------------
Step 1 — POS Mapping

Reads the input POS CSV, queries GCP transaction table by oms_company
for each record, maps each POS row to its GCP transaction record,
and returns the mapping as an in-memory DataFrame.

Output columns:
    row_number, oms_company, scenario, record_type, gcp_pos_id,
    + all original POS CSV columns

The DataFrame is passed directly to match_validator (Step 2).
No file is written here — file output is handled by main.py.
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from src.gcp_client import GCPClient

logger = logging.getLogger(__name__)


def run_pos_mapping(
    pos_csv_path: str,
    gcp_client: GCPClient,
) -> pd.DataFrame:
    """
    Map each POS record in the input CSV to its GCP transaction record.

    Process:
        1. Read POS CSV from pos_csv_path
        2. For each row, query GCP transaction table by oms_company
        3. Extract gcp_pos_id from transaction record
        4. Build and return the mapping DataFrame

    Args:
        pos_csv_path : Path to the input POS CSV file.
        gcp_client   : Initialised GCPClient instance (shared across steps).

    Returns:
        DataFrame with columns:
            row_number, oms_company, scenario, record_type, gcp_pos_id,
            + all original columns from the POS CSV.
        Rows where GCP returned no match will have gcp_pos_id = None.

    Raises:
        FileNotFoundError : If pos_csv_path does not exist.
        ValueError        : If required columns are missing from the POS CSV.
    """
    # ── 1. Load POS CSV ────────────────────────────────────────────────
    path = Path(pos_csv_path)
    if not path.exists():
        raise FileNotFoundError(f"POS CSV not found: {pos_csv_path}")

    pos_df = pd.read_csv(path)
    logger.info("Loaded POS CSV | path=%s | rows=%d | cols=%s",
                pos_csv_path, len(pos_df), list(pos_df.columns))

    # ── 2. Validate required columns ──────────────────────────────────
    required = {"oms_company"}
    missing  = required - set(pos_df.columns)
    if missing:
        raise ValueError(
            f"POS CSV is missing required columns: {missing}\n"
            f"Found columns: {list(pos_df.columns)}"
        )

    # Optional columns — use empty string default if not present
    if "scenario"    not in pos_df.columns: pos_df["scenario"]    = ""
    if "record_type" not in pos_df.columns: pos_df["record_type"] = ""

    # ── 3. Map each row to GCP ─────────────────────────────────────────
    mapping_rows = []

    for idx, row in pos_df.iterrows():
        row_number  = idx + 1
        oms_company = str(row["oms_company"]).strip()
        scenario    = str(row.get("scenario",    "")).strip()
        record_type = str(row.get("record_type", "")).strip()

        logger.debug("Processing row %d | oms_company=%s", row_number, oms_company)

        gcp_pos_id = None
        try:
            gcp_df = gcp_client.fetch_transaction(oms_company=oms_company)

            if gcp_df.empty:
                logger.warning("Row %d | No GCP transaction found for oms_company=%s",
                               row_number, oms_company)
            else:
                if len(gcp_df) > 1:
                    logger.warning("Row %d | Multiple GCP records for oms_company=%s — using first",
                                   row_number, oms_company)
                # Extract pos_id — the GCP identifier for this transaction
                gcp_pos_id = gcp_df.iloc[0].get("pos_id") or gcp_df.iloc[0].get("id")

        except RuntimeError as exc:
            logger.error("Row %d | GCP query failed | oms_company=%s | %s",
                         row_number, oms_company, exc)

        # Build mapping row — start with metadata, then all original POS fields
        mapping_row = {
            "row_number":  row_number,
            "oms_company": oms_company,
            "scenario":    scenario,
            "record_type": record_type,
            "gcp_pos_id":  gcp_pos_id,
        }
        # Append all original POS columns (excluding those already captured above)
        for col in pos_df.columns:
            if col not in mapping_row:
                mapping_row[col] = row[col]

        mapping_rows.append(mapping_row)

    mapping_df = pd.DataFrame(mapping_rows)

    total     = len(mapping_df)
    matched   = mapping_df["gcp_pos_id"].notna().sum()
    unmatched = total - matched

    logger.info(
        "POS Mapping complete | total=%d | matched=%d | unmatched=%d",
        total, matched, unmatched,
    )

    return mapping_df
