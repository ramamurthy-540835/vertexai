"""
Manual matching: given a lead-like payload, search the transaction table
for candidate POS records that may correspond to the lead.

Behavior:
- warehouse_number is a strict equality filter (required from payload).
- Fiscal year filter: always current and previous fiscal year, computed
  server-side via get_costco_fiscal_info(). The payload's u_fiscal_year
  field is ignored — fiscal scope is determined by today's date.
- Other text fields use ILIKE '%value%' (case-insensitive contains).
- Empty/missing input fields are skipped (no filter contributed).
- Phone and membership_number are deliberately NOT used in v1.
- Score = count of contains-matching fields. Higher = better.
- Returns up to MAX_RESULTS rows ordered by score DESC, regardless of score.
  (Rows that match nothing still come back, just with score=0 at the bottom.)
"""

import logging
from typing import Any

import sqlalchemy
from sqlalchemy import text

import config
from fiscal import get_costco_fiscal_info

log = logging.getLogger(__name__)

MAX_RESULTS = 500


# Input payload field → transaction column for contains-style matching.
SEARCHABLE_FIELDS = {
    "u_business_name": "business_name",
    "u_address_1":     "address_line_one",
    "u_address_2":     "address_line_two",
    "u_city":          "city",
    "u_state_pos":     "state",
    "u_zip_code":      "zip_code",
    "u_email":         "email",
}

# Transaction columns selected for the response.
RESPONSE_COLUMNS_SQL = [
    "account_number",
    "warehouse_number",
    "membership_number",
    "business_name",
    "address_line_one",
    "address_line_two",
    "first_name",
    "last_name",
    "city",
    "state",
    "zip_code",
    "email",
    "phone",
    "fiscal_year",
    "fiscal_period",
    "week",
    "sales_reference_id",
    "order_amount",
    "industry_description",
    "bd_industry",
    "shop_type"
]


def _non_empty(payload: dict, key: str) -> str | None:
    """Return trimmed payload[key] if present and non-empty, else None."""
    val = payload.get(key)
    if val is None:
        return None
    stripped = str(val).strip()
    return stripped if stripped else None


def find_candidates(engine: sqlalchemy.Engine, payload: dict) -> list[dict]:
    """Find candidate transactions for manual matching.

    Always searches the last 2 fiscal years (current + previous), computed
    server-side. Any u_fiscal_year in the payload is ignored.

    Returns up to MAX_RESULTS rows ranked by match score (DESC).
    """
    warehouse = _non_empty(payload, "u_warehouse_number")
    if not warehouse:
        raise ValueError("u_warehouse_number is required")

    # Always derive fiscal scope from today — ignore any payload fiscal year.
    current_fy = get_costco_fiscal_info()["fiscal_year"]
    prev_fy = current_fy - 1

    # Collect non-empty searchable inputs only.
    active_filters: list[tuple[str, str, str]] = []   # (column, key, value)
    for payload_key, column in SEARCHABLE_FIELDS.items():
        value = _non_empty(payload, payload_key)
        if value is not None:
            active_filters.append((column, payload_key, value))

    log.info(
        "Manual match: warehouse=%s, fiscal_years=[%d, %d], active_filters=%d",
        warehouse, current_fy, prev_fy, len(active_filters),
    )

    # Build score expression: count of ILIKE-matching fields.
    if active_filters:
        score_parts = [
            f"(CASE WHEN {col} ILIKE :v_{key} THEN 1 ELSE 0 END)"
            for (col, key, _) in active_filters
        ]
        score_expr = " + ".join(score_parts)
    else:
        # No active filters → all rows score 0 (the warehouse + fiscal filter
        # still applies; we just return everything ordered arbitrarily).
        score_expr = "0"

    params: dict[str, Any] = {
        "warehouse":  warehouse,
        "fy_current": current_fy,
        "fy_prev":    prev_fy,
    }
    for (_, key, val) in active_filters:
        params[f"v_{key}"] = f"%{val}%"

    select_cols = ", ".join(RESPONSE_COLUMNS_SQL)
    sql = f"""
        SELECT
            {select_cols},
            ({score_expr}) AS match_score
        FROM {config.DB_SCHEMA}.transaction
        WHERE warehouse_number = :warehouse
          AND fiscal_year_transaction IN (:fy_current, :fy_prev)
        ORDER BY match_score DESC, sales_reference_id ASC
        LIMIT {MAX_RESULTS}
    """

    log.debug("Manual match SQL:\n%s\nParams: %s", sql, params)

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        rows = [dict(r._mapping) for r in result]

    log.info("Manual match found %d candidates", len(rows))
    return rows


def to_servicenow_response(rows: list[dict]) -> dict:
    """Shape rows into ServiceNow-style response."""
    if not rows:
        return {
            "result": [],
            "message": "No matching records found",
        }
    out = []
    for r in rows:
        out.append({
            "active":                  "true",
            "u_type":                  _as_str(r.get("shop_type")),
            "u_business_name":         _as_str(r.get("business_name")),
            "u_address_1":             _as_str(r.get("address_line_one")),
            "u_address_2":             _as_str(r.get("address_line_two")),
            "u_first":                 _as_str(r.get("first_name")),
            "u_last":                  _as_str(r.get("last_name")),
            "u_city":                  _as_str(r.get("city")),
            "u_state_pos":             _as_str(r.get("state")),
            "u_zip_code":              _as_str(r.get("zip_code")),
            "u_email":                 _as_str(r.get("email")),
            "u_phone_number":          _as_str(r.get("phone")),
            "u_fiscal_year":           _as_str(r.get("fiscal_year")),
            "u_period_1":              _as_str(r.get("fiscal_period")),
            "u_week":                  _as_str(r.get("week")),
            "u_sales_reference_id":    _as_str(r.get("sales_reference_id")),
            "u_account_number":        _as_str(r.get("account_number")),
            "u_warehouse_number":      _as_str(r.get("warehouse_number")),
            "u_membership_number":     _as_str(r.get("membership_number")),
            "u_industry_description":  _as_str(r.get("industry_description")),
            "u_bd_industry_pos":       _as_str(r.get("bd_industry")),
            "u_order_amount_rounded":  _as_str(r.get("order_amount")),
            "u_matching_comments":     "",
        })
    return {"result": out}


def _as_str(v: Any) -> str:
    """Convert any DB value to a string. None → ''."""
    if v is None:
        return ""
    return str(v)