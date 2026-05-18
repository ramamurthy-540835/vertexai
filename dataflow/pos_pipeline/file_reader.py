"""
Multi-format file reader.

Returns a list of dicts where keys are the source column headers
(preserved as-is from the file) and values are Python primitives.
Mapping to DB columns happens later in schema_utils.apply_field_map.
"""

import csv
import io
import json
import logging
import os
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {
    ".csv", ".tsv", ".psv", ".txt",
    ".xlsx", ".xls",
    ".json", ".jsonl",
    ".parquet",
}


def read_file_to_dicts(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """
    Dispatch to the right reader based on file extension.
    Returns a list of row dicts. Header names are preserved verbatim.
    """
    ext = os.path.splitext(filename.lower())[1]
    logger.info(f"Reading file {filename} with extension {ext}")

    if ext in (".csv", ".txt"):
        return _read_delimited(content, delimiter=",")
    if ext == ".tsv":
        return _read_delimited(content, delimiter="\t")
    if ext == ".psv":
        return _read_delimited(content, delimiter="|")
    if ext in (".xlsx", ".xls"):
        return _read_excel(content)
    if ext == ".json":
        return _read_json(content)
    if ext == ".jsonl":
        return _read_jsonl(content)
    if ext == ".parquet":
        return _read_parquet(content)

    logger.warning(f"Unknown extension {ext}, attempting CSV parse")
    return _read_delimited(content, delimiter=",")


def _read_delimited(content: bytes, delimiter: str = ",") -> List[Dict[str, Any]]:
    """Read CSV/TSV/PSV. Tries multiple encodings to handle BOM / Windows files.

    Headers are normalized (stripped + lowercased) to match field_map.json keys,
    consistent with how _read_excel handles them.
    """
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = content.decode(enc)
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)

            try:
                raw_headers = next(reader)
            except StopIteration:
                logger.warning("CSV file is empty (no header row)")
                return []

            # Normalize the same way Excel does: str -> strip -> lowercase
            headers = [
                str(h).strip().lower() if h is not None else ""
                for h in raw_headers
            ]
            logger.info(f"CSV normalized headers ({len(headers)}): {headers}")

            rows: List[Dict[str, Any]] = []
            for raw_row in reader:
                # Skip blank rows
                if not raw_row or all(
                    (v is None or str(v).strip() == "") for v in raw_row
                ):
                    continue

                row_dict = {
                    headers[i]: raw_row[i]
                    for i in range(min(len(headers), len(raw_row)))
                    if headers[i]  # skip empty header columns
                }
                rows.append(row_dict)

            logger.info(
                f"Delimited read: {len(rows)} rows "
                f"(encoding={enc}, headers={len([h for h in headers if h])})"
            )
            return rows
        except UnicodeDecodeError:
            continue

    raise ValueError("Could not decode file with any supported encoding")


def _read_excel(content: bytes) -> List[Dict[str, Any]]:
    """Read .xlsx (openpyxl) or .xls (xlrd) into list of row dicts."""
    # Try xlsx first
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = next(rows_iter, None)
        if not headers:
            return []
        headers = [str(h).strip().lower() if h is not None else "" for h in headers]

        out: List[Dict[str, Any]] = []
        for row in rows_iter:
            if all(v is None or v == "" for v in row):
                continue  # skip blank rows
            out.append({
                headers[i]: (row[i] if i < len(row) else None)
                for i in range(len(headers))
                if headers[i]
            })
        logger.info(f"Excel (xlsx) read: {len(out)} rows")
        return out
    except Exception as e:
        logger.info(f"openpyxl failed ({e}); trying xlrd fallback for .xls")

    # Fall back to xlrd for old .xls
    import xlrd
    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)
    headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
    out = []
    for r in range(1, ws.nrows):
        row_dict = {
            headers[c]: ws.cell_value(r, c)
            for c in range(ws.ncols)
            if headers[c]
        }
        if any(v not in (None, "") for v in row_dict.values()):
            out.append(row_dict)
    logger.info(f"Excel (xls) read: {len(out)} rows")
    return out


def _read_json(content: bytes) -> List[Dict[str, Any]]:
    """Parse JSON as list-of-objects or {rows: [...]} envelope."""
    text = content.decode("utf-8-sig")
    data = json.loads(text)
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = [r for r in data["rows"] if isinstance(r, dict)]
    else:
        raise ValueError("JSON must be a list of objects, or {rows: [...]}")
    logger.info(f"JSON read: {len(rows)} rows")
    return rows


def _read_jsonl(content: bytes) -> List[Dict[str, Any]]:
    """Parse newline-delimited JSON."""
    text = content.decode("utf-8-sig")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    logger.info(f"JSONL read: {len(rows)} rows")
    return rows


def _read_parquet(content: bytes) -> List[Dict[str, Any]]:
    """Parse Parquet to list of row dicts."""
    import pyarrow.parquet as pq
    table = pq.read_table(io.BytesIO(content))
    rows = table.to_pylist()
    logger.info(f"Parquet read: {len(rows)} rows")
    return rows