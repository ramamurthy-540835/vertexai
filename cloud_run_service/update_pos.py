"""Business logic for ServiceNow transaction-update callbacks.

These functions are deliberately Flask-unaware. They take/return plain Python
types so they can be tested without spinning up an HTTP server.
"""

import logging
from datetime import datetime, timezone

import sqlalchemy
from sqlalchemy import text

import config

log = logging.getLogger(__name__)


def apply_single_update(conn: sqlalchemy.Connection, record: dict, now: datetime) -> None:
    """Apply one ServiceNow record to the transaction (and lead) tables.

    Raises ValueError if the record is invalid or its target rows don't exist.
    Caller is responsible for transaction boundaries (engine.begin()).
    """
    pos_id = record.get("u_gcp_id")
    if not pos_id:
        raise ValueError("Missing required field 'u_gcp_id'")

    matched_lead = record.get("u_matched_lead") or {}
    lead_id      = matched_lead.get("number") or None
    match_value  = record.get("u_match_value") or None
    match_result = record.get("u_match_result") or None

    if lead_id:
        _apply_match(conn, pos_id, lead_id, match_value, match_result, now)
    else:
        _apply_unmatch(conn, pos_id, now)


def _apply_match(conn, pos_id, lead_id, match_value, match_result, now):
    """Update transaction with match info, then update the lead's match_result.

    primary_transaction is intentionally NOT touched on match — it stays as-is.
    """
    tx_result = conn.execute(
        text(f"""
            UPDATE {config.DB_SCHEMA}.transaction
               SET lead_id      = :lead_id,
                   match_score  = :match_score,
                   match_type   = 'Exact',
                   updated_date = :now,
                   updated_by   = :updated_by
             WHERE pos_id = :pos_id
        """),
        {
            "lead_id":     lead_id,
            "match_score": match_value,
            "now":         now,
            "updated_by":  config.UPDATED_BY,
            "pos_id":      pos_id,
        },
    )
    if tx_result.rowcount == 0:
        raise ValueError(f"transaction not found for pos_id={pos_id}")

    lead_result = conn.execute(
        text(f"""
            UPDATE {config.DB_SCHEMA}.lead
               SET match_result = :match_result,
                   updated_date = :now,
                   updated_by   = :updated_by
             WHERE lead_id = :lead_id
        """),
        {
            "match_result": match_result,
            "now":          now,
            "updated_by":   config.UPDATED_BY,
            "lead_id":      lead_id,
        },
    )
    if lead_result.rowcount == 0:
        raise ValueError(f"lead not found for lead_id={lead_id}")


def _apply_unmatch(conn, pos_id, now):
    """Clear the match on a transaction. primary_transaction becomes False
    because there's no lead to be primary for."""
    tx_result = conn.execute(
        text(f"""
            UPDATE {config.DB_SCHEMA}.transaction
               SET lead_id              = NULL,
                   match_score          = NULL,
                   match_type           = NULL,
                   primary_transaction  = NULL,
                   matching_comments    = NULL,
                   updated_date         = :now,
                   updated_by           = :updated_by
             WHERE pos_id = :pos_id
        """),
        {
            "now":        now,
            "updated_by": config.UPDATED_BY,
            "pos_id":     pos_id,
        },
    )
    if tx_result.rowcount == 0:
        raise ValueError(f"transaction not found for pos_id={pos_id}")


def process_batch(engine: sqlalchemy.Engine, records: list[dict]) -> dict:
    """Process a list of records, one DB transaction per record.

    Returns a summary dict with per-record error details for any failures.
    """
    now = datetime.now(timezone.utc)
    succeeded, errors = 0, []

    for idx, rec in enumerate(records):
        pos_id = (rec or {}).get("u_gcp_id")
        try:
            with engine.begin() as conn:
                apply_single_update(conn, rec or {}, now)
            succeeded += 1
            log.info("Updated pos_id=%s", pos_id)
        except Exception as e:
            log.exception("Failed processing pos_id=%s", pos_id)
            errors.append({"index": idx, "pos_id": pos_id, "error": str(e)})

    return {
        "processed": len(records),
        "succeeded": succeeded,
        "failed":    len(errors),
        "errors":    errors,
    }