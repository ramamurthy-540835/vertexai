"""
lead_validator.py
-----------------
Step 3 — Lead Validation

Fetches lead records from ServiceNow and GCP, compares them field-by-field,
and returns a PASS/FAIL validation DataFrame.

Comparison logic:
  - For each lead_id fetched from ServiceNow:
      1. Fetch matching lead from GCP (lead table) by lead_id
      2. Fetch matching account from GCP (account table) by account_id / business_name
      3. Compare shared fields side-by-side
      4. Emit one row per field comparison with PASS / FAIL / MISSING status

Output columns:
    lead_id, field_name, servicenow_value, gcp_value, validation_status

Returns the validation DataFrame in-memory.
File output is handled by main.py.
"""

import logging
from typing import Optional

import pandas as pd

from src.gcp_client import GCPClient
from src.servicenow_client import ServiceNowClient

logger = logging.getLogger(__name__)

# Validation status constants
PASS    = "PASS"
FAIL    = "FAIL"
MISSING = "MISSING"   # record found in one system but not the other

# ---------------------------------------------------------------------------
# Fields to compare between ServiceNow lead and GCP lead table.
# Key   = ServiceNow field name (as returned by the REST API)
# Value = Corresponding GCP column name in lead_mgmt_qat.lead
#
# Update this map to match your actual field names once you confirm them.
# ---------------------------------------------------------------------------
LEAD_FIELD_MAP: dict[str, str] = {
    "sys_id":          "lead_id",
    "first_name":      "first_name",
    "last_name":       "last_name",
    "email":           "email",
    "phone":           "phone",
    "company":         "company",
    "status":          "status",
    "lead_source":     "lead_source",
    "description":     "description",
}

# Fields to compare between ServiceNow lead and GCP account table.
# Key   = ServiceNow field name
# Value = GCP account column name
ACCOUNT_FIELD_MAP: dict[str, str] = {
    "company":         "business_name",
    "account_id":      "account_id",
}


def _normalize(value) -> str:
    """Normalize a value to a stripped lowercase string for comparison."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def _compare_fields(
    lead_id: str,
    sn_record: dict,
    gcp_record: pd.Series,
    field_map: dict[str, str],
) -> list[dict]:
    """
    Compare fields between a ServiceNow record and a GCP record.

    Args:
        lead_id    : Identifier for this lead (for output labelling).
        sn_record  : Single ServiceNow record as a dict.
        gcp_record : Single GCP record as a pandas Series (one row of a DataFrame).
        field_map  : { sn_field: gcp_field } mapping.

    Returns:
        List of row dicts, one per field compared.
    """
    rows = []
    for sn_field, gcp_field in field_map.items():
        sn_value  = sn_record.get(sn_field,  "")
        gcp_value = gcp_record.get(gcp_field, "") if gcp_record is not None else ""

        sn_norm  = _normalize(sn_value)
        gcp_norm = _normalize(gcp_value)

        status = PASS if sn_norm == gcp_norm else FAIL

        if status == FAIL:
            logger.warning(
                "FAIL | lead_id=%s | field=%s | SN=%s | GCP=%s",
                lead_id, sn_field, sn_value, gcp_value,
            )

        rows.append({
            "lead_id":            lead_id,
            "field_name":         sn_field,
            "servicenow_value":   str(sn_value),
            "gcp_value":          str(gcp_value),
            "validation_status":  status,
        })

    return rows


def run_lead_validation(
    gcp_client: GCPClient,
    sn_client: ServiceNowClient,
    recent_minutes: int          = 60,
    lead_ids: Optional[list]     = None,
) -> pd.DataFrame:
    """
    Validate lead data between ServiceNow and GCP.

    Process:
        1. Fetch leads from ServiceNow (by lead_ids list OR recent N minutes)
        2. For each SN lead:
           a. Fetch corresponding GCP lead by lead_id
           b. Fetch corresponding GCP account by account_id / business_name
           c. Compare fields using LEAD_FIELD_MAP and ACCOUNT_FIELD_MAP
           d. Emit PASS / FAIL / MISSING rows
        3. Return all comparison rows as a DataFrame

    Args:
        gcp_client      : Shared GCPClient instance.
        sn_client       : Shared ServiceNowClient instance.
        recent_minutes  : If lead_ids is None, fetch leads updated in last N minutes.
        lead_ids        : Optional explicit list of lead IDs to validate.
                          If provided, recent_minutes is ignored.

    Returns:
        DataFrame with columns:
            lead_id, field_name, servicenow_value, gcp_value, validation_status

    Raises:
        RuntimeError: On GCP or ServiceNow API failure.
    """
    # ── 1. Fetch leads from ServiceNow ────────────────────────────────
    if lead_ids:
        logger.info("Fetching %d specific leads from ServiceNow", len(lead_ids))
        sn_leads = sn_client.fetch_leads_by_ids(lead_ids)
    else:
        logger.info("Fetching recent ServiceNow leads | last %d minutes", recent_minutes)
        sn_leads = sn_client.fetch_recent_leads(minutes=recent_minutes)

    if not sn_leads:
        logger.warning("No leads returned from ServiceNow. Validation skipped.")
        return pd.DataFrame(columns=[
            "lead_id", "field_name", "servicenow_value", "gcp_value", "validation_status"
        ])

    logger.info("ServiceNow returned %d lead(s) to validate", len(sn_leads))

    # ── 2. Validate each lead ──────────────────────────────────────────
    all_rows = []

    for sn_lead in sn_leads:
        # Extract identifiers from the SN record
        lead_id      = str(sn_lead.get("sys_id", "")).strip()
        account_id   = str(sn_lead.get("account_id",   "")).strip()
        business_name = str(sn_lead.get("company",     "")).strip()

        if not lead_id:
            logger.warning("Skipping SN record with no sys_id: %s", sn_lead)
            continue

        logger.debug("Validating lead | lead_id=%s", lead_id)

        # ── 2a. Fetch GCP lead record ──────────────────────────────────
        gcp_lead_record = None
        try:
            gcp_lead_df = gcp_client.fetch_lead(lead_id=lead_id)
            if gcp_lead_df.empty:
                logger.warning("GCP lead not found | lead_id=%s", lead_id)
                all_rows.append({
                    "lead_id":           lead_id,
                    "field_name":        "ALL",
                    "servicenow_value":  "RECORD EXISTS",
                    "gcp_value":         "NOT FOUND",
                    "validation_status": MISSING,
                })
                continue
            gcp_lead_record = gcp_lead_df.iloc[0]
        except RuntimeError as exc:
            logger.error("GCP lead fetch failed | lead_id=%s | %s", lead_id, exc)
            all_rows.append({
                "lead_id":           lead_id,
                "field_name":        "ALL",
                "servicenow_value":  "RECORD EXISTS",
                "gcp_value":         f"ERROR: {exc}",
                "validation_status": FAIL,
            })
            continue

        # ── 2b. Compare lead fields ────────────────────────────────────
        lead_rows = _compare_fields(
            lead_id    = lead_id,
            sn_record  = sn_lead,
            gcp_record = gcp_lead_record,
            field_map  = LEAD_FIELD_MAP,
        )
        all_rows.extend(lead_rows)

        # ── 2c. Fetch and compare GCP account record ───────────────────
        if account_id or business_name:
            try:
                gcp_account_df = gcp_client.fetch_account(
                    account_id    = account_id    or None,
                    business_name = business_name or None,
                )
                if not gcp_account_df.empty:
                    account_rows = _compare_fields(
                        lead_id    = lead_id,
                        sn_record  = sn_lead,
                        gcp_record = gcp_account_df.iloc[0],
                        field_map  = ACCOUNT_FIELD_MAP,
                    )
                    all_rows.extend(account_rows)
                else:
                    logger.warning(
                        "GCP account not found | lead_id=%s | account_id=%s | business=%s",
                        lead_id, account_id, business_name,
                    )
                    all_rows.append({
                        "lead_id":           lead_id,
                        "field_name":        "account",
                        "servicenow_value":  business_name or account_id,
                        "gcp_value":         "NOT FOUND",
                        "validation_status": MISSING,
                    })
            except RuntimeError as exc:
                logger.error("GCP account fetch failed | lead_id=%s | %s", lead_id, exc)

    validation_df = pd.DataFrame(all_rows)

    if not validation_df.empty:
        total   = len(validation_df)
        passed  = (validation_df["validation_status"] == PASS).sum()
        failed  = (validation_df["validation_status"] == FAIL).sum()
        missing = (validation_df["validation_status"] == MISSING).sum()
        logger.info(
            "Lead Validation complete | total=%d | PASS=%d | FAIL=%d | MISSING=%d",
            total, passed, failed, missing,
        )

    return validation_df
