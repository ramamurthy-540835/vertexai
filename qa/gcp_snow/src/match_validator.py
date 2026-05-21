"""
match_validator.py
------------------
Step 2 — Match Validation

Reads the pos_mapping DataFrame (output of Step 1), queries GCP transaction
table by gcp_pos_id to fetch actual match_type and match_score, then compares
against expected values loaded from expected_results.json.

Output columns:
    gcp_pos_id, scenario, expected_match_type, actual_match_type,
    expected_match_score, actual_match_score, validation_status

Returns the validation DataFrame in-memory.
File output is handled by main.py.
"""

import json
import logging
from pathlib import Path

import pandas as pd

from src.gcp_client import GCPClient

logger = logging.getLogger(__name__)

# Validation status constants
PASS    = "PASS"
FAIL    = "FAIL"
NO_DATA = "NO_DATA"   # GCP returned no record for this pos_id


def _load_expected_results(config_path: str) -> dict:
    """
    Load expected_results.json and return a dict keyed by scenario name.

    Expected JSON structure:
    {
      "scenarios": [
        {
          "scenario_name": "Exact_Match_001",
          "expected_match_type": "EXACT",
          "expected_match_score": 100,
          ...
        }
      ]
    }

    Returns:
        { "Exact_Match_001": { "expected_match_type": "EXACT", "expected_match_score": 100 }, ... }
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"expected_results.json not found: {config_path}")

    with open(path, "r") as f:
        data = json.load(f)

    scenarios = data.get("scenarios", [])
    result    = {}
    for s in scenarios:
        name = s.get("scenario_name", "").strip()
        if name:
            result[name] = {
                "expected_match_type":  s.get("expected_match_type",  ""),
                "expected_match_score": s.get("expected_match_score", ""),
            }

    logger.info("Loaded %d expected scenarios from %s", len(result), config_path)
    return result


def run_match_validation(
    mapping_df: pd.DataFrame,
    gcp_client: GCPClient,
    expected_results_path: str,
) -> pd.DataFrame:
    """
    Validate actual GCP match results against expected scenario values.

    Process:
        1. Load expected values from expected_results.json
        2. For each row in mapping_df that has a gcp_pos_id:
           a. Query GCP transaction table by pos_id
           b. Extract actual match_type and match_score
           c. Look up expected values by scenario name
           d. Compare and assign PASS / FAIL / NO_DATA
        3. Return validation DataFrame

    Args:
        mapping_df             : Output DataFrame from Step 1 (pos_mapper).
        gcp_client             : Shared GCPClient instance.
        expected_results_path  : Path to config/expected_results.json.

    Returns:
        DataFrame with columns:
            gcp_pos_id, scenario, expected_match_type, actual_match_type,
            expected_match_score, actual_match_score, validation_status

    Raises:
        FileNotFoundError : If expected_results.json is not found.
    """
    expected = _load_expected_results(expected_results_path)
    rows     = []

    for _, rec in mapping_df.iterrows():
        gcp_pos_id = rec.get("gcp_pos_id")
        scenario   = str(rec.get("scenario", "")).strip()

        # Rows where Step 1 found no GCP match — skip with NO_DATA
        if not gcp_pos_id or pd.isna(gcp_pos_id):
            rows.append({
                "gcp_pos_id":           gcp_pos_id,
                "scenario":             scenario,
                "expected_match_type":  "",
                "actual_match_type":    "",
                "expected_match_score": "",
                "actual_match_score":   "",
                "validation_status":    NO_DATA,
            })
            continue

        gcp_pos_id = str(gcp_pos_id).strip()

        # ── Fetch actual match data from GCP ──────────────────────────
        actual_match_type  = ""
        actual_match_score = ""
        status             = NO_DATA

        try:
            gcp_df = gcp_client.fetch_transaction(pos_id=gcp_pos_id)

            if gcp_df.empty:
                logger.warning("No GCP record found for pos_id=%s", gcp_pos_id)
            else:
                actual_match_type  = str(gcp_df.iloc[0].get("match_type",  "")).strip()
                actual_match_score = str(gcp_df.iloc[0].get("match_score", "")).strip()

                # ── Load expected values ───────────────────────────────
                exp = expected.get(scenario, {})
                exp_match_type  = str(exp.get("expected_match_type",  "")).strip()
                exp_match_score = str(exp.get("expected_match_score", "")).strip()

                # ── Compare ───────────────────────────────────────────
                type_match  = actual_match_type  == exp_match_type
                score_match = actual_match_score == exp_match_score
                status = PASS if (type_match and score_match) else FAIL

                if status == FAIL:
                    logger.warning(
                        "FAIL | pos_id=%s | scenario=%s | "
                        "match_type: expected=%s actual=%s | "
                        "match_score: expected=%s actual=%s",
                        gcp_pos_id, scenario,
                        exp_match_type, actual_match_type,
                        exp_match_score, actual_match_score,
                    )

        except RuntimeError as exc:
            logger.error("GCP query failed | pos_id=%s | %s", gcp_pos_id, exc)
            exp = expected.get(scenario, {})
            exp_match_type  = str(exp.get("expected_match_type",  "")).strip()
            exp_match_score = str(exp.get("expected_match_score", "")).strip()
            status = FAIL

        rows.append({
            "gcp_pos_id":           gcp_pos_id,
            "scenario":             scenario,
            "expected_match_type":  exp.get("expected_match_type",  "") if scenario in expected else "",
            "actual_match_type":    actual_match_type,
            "expected_match_score": exp.get("expected_match_score", "") if scenario in expected else "",
            "actual_match_score":   actual_match_score,
            "validation_status":    status,
        })

    validation_df = pd.DataFrame(rows)

    total    = len(validation_df)
    passed   = (validation_df["validation_status"] == PASS).sum()
    failed   = (validation_df["validation_status"] == FAIL).sum()
    no_data  = (validation_df["validation_status"] == NO_DATA).sum()

    logger.info(
        "Match Validation complete | total=%d | PASS=%d | FAIL=%d | NO_DATA=%d",
        total, passed, failed, no_data,
    )

    return validation_df
