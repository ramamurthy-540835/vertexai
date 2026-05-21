"""
main.py
-------
Pipeline Orchestrator — POS Match Validation Automation

Runs all 3 validation steps in sequence:
  Step 1 — POS Mapping        (pos_mapper.py)
  Step 2 — Match Validation   (match_validator.py)
  Step 3 — Lead Validation    (lead_validator.py)

All data flows in-memory between steps.
CSV output files are written at the end of each step.

Usage:
  python main.py

Environment variables (must be set before running):
  GCP_DB_PASSWORD   — Cloud SQL password
  USE_PROXY         — "true" for local dev via Auth Proxy
  SN_BASE_URL       — ServiceNow instance URL
  SN_CLIENT_ID      — ServiceNow OAuth client ID
  SN_CLIENT_SECRET  — ServiceNow OAuth client secret

Optional:
  GCP_PROJECT_ID, GCP_REGION, GCP_INSTANCE_NAME, GCP_DB_USER
  SN_PAGE_SIZE
  LEAD_VALIDATION_MINUTES  — how far back to look for recent leads (default: 60)
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── Add repo root to path so  "from src.xxx import"  works ────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gcp_client          import GCPClient
from src.servicenow_client   import ServiceNowClient
from src.pos_mapper          import run_pos_mapping
from src.match_validator     import run_match_validation
from src.lead_validator      import run_lead_validation

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

run_ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file    = LOG_DIR / f"pipeline_{run_ts}.log"

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Output folder setup
# ---------------------------------------------------------------------------

OUTPUT_POS_MAPPING        = Path("output/pos_mapping")
OUTPUT_MATCH_VALIDATION   = Path("output/match_validation")
OUTPUT_LEAD_VALIDATION    = Path("output/lead_validation")

for folder in [OUTPUT_POS_MAPPING, OUTPUT_MATCH_VALIDATION, OUTPUT_LEAD_VALIDATION]:
    folder.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_app_config() -> dict:
    """Load config/app_config.json if it exists, else return empty dict."""
    config_path = Path("config/app_config.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}

def _get_pos_csv_path(config: dict) -> str:
    """Resolve POS CSV path from config or default input folder."""
    # Check config first
    pos_folder = config.get("input", {}).get("pos_folder", "input/pos/")
    folder     = Path(pos_folder)

    # Find the first CSV in the folder
    csvs = list(folder.glob("*.csv"))
    if not csvs:
        raise FileNotFoundError(
            f"No CSV files found in {folder}.\n"
            f"Place your POS input CSV in: {folder.resolve()}"
        )
    if len(csvs) > 1:
        logger.warning("Multiple CSVs found in %s — using: %s", folder, csvs[0].name)
    return str(csvs[0])

def _get_expected_results_path(config: dict) -> str:
    return config.get("config", {}).get(
        "expected_results", "config/expected_results.json"
    )

# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def _save_csv(df: pd.DataFrame, folder: Path, filename: str) -> Path:
    """Save a DataFrame to a timestamped CSV file."""
    out_path = folder / f"{filename}_{run_ts}.csv"
    df.to_csv(out_path, index=False)
    logger.info("Saved: %s (%d rows)", out_path, len(df))
    return out_path

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("POS Match Validation Pipeline — START")
    logger.info("Run timestamp: %s", run_ts)
    logger.info("=" * 60)

    config = _load_app_config()

    # ── Resolve inputs ─────────────────────────────────────────────────
    pos_csv_path          = _get_pos_csv_path(config)
    expected_results_path = _get_expected_results_path(config)
    recent_minutes        = int(os.getenv("LEAD_VALIDATION_MINUTES", "60"))

    logger.info("POS CSV              : %s", pos_csv_path)
    logger.info("Expected results     : %s", expected_results_path)
    logger.info("Lead lookback window : %d minutes", recent_minutes)

    # ── Initialise shared clients ──────────────────────────────────────
    logger.info("-" * 60)
    logger.info("Initialising GCP and ServiceNow clients...")

    try:
        gcp_client = GCPClient()
    except ValueError as exc:
        logger.error("GCP client init failed: %s", exc)
        sys.exit(1)

    try:
        sn_client = ServiceNowClient()
    except ValueError as exc:
        logger.error("ServiceNow client init failed: %s", exc)
        sys.exit(1)

    # ── STEP 1: POS Mapping ────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1 — POS Mapping")
    logger.info("=" * 60)

    try:
        mapping_df = run_pos_mapping(
            pos_csv_path = pos_csv_path,
            gcp_client   = gcp_client,
        )
        _save_csv(mapping_df, OUTPUT_POS_MAPPING, "pos_mapping")
        logger.info("Step 1 complete | rows=%d", len(mapping_df))

    except Exception as exc:
        logger.error("Step 1 FAILED: %s", exc, exc_info=True)
        gcp_client.close()
        sys.exit(1)

    # ── STEP 2: Match Validation ───────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 2 — Match Validation")
    logger.info("=" * 60)

    try:
        match_df = run_match_validation(
            mapping_df            = mapping_df,
            gcp_client            = gcp_client,
            expected_results_path = expected_results_path,
        )
        _save_csv(match_df, OUTPUT_MATCH_VALIDATION, "match_validation")

        # Print summary to console
        _print_validation_summary("Match Validation", match_df, "validation_status")
        logger.info("Step 2 complete | rows=%d", len(match_df))

    except Exception as exc:
        logger.error("Step 2 FAILED: %s", exc, exc_info=True)
        gcp_client.close()
        sys.exit(1)

    # ── STEP 3: Lead Validation ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 3 — Lead Validation")
    logger.info("=" * 60)

    try:
        lead_df = run_lead_validation(
            gcp_client     = gcp_client,
            sn_client      = sn_client,
            recent_minutes = recent_minutes,
        )
        _save_csv(lead_df, OUTPUT_LEAD_VALIDATION, "lead_validation")

        _print_validation_summary("Lead Validation", lead_df, "validation_status")
        logger.info("Step 3 complete | rows=%d", len(lead_df))

    except Exception as exc:
        logger.error("Step 3 FAILED: %s", exc, exc_info=True)

    finally:
        gcp_client.close()
        logger.info("GCP connector closed.")

    # ── Final summary ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Pipeline COMPLETE")
    logger.info("Outputs:")
    logger.info("  POS Mapping     : output/pos_mapping/")
    logger.info("  Match Validation: output/match_validation/")
    logger.info("  Lead Validation : output/lead_validation/")
    logger.info("  Log file        : %s", log_file)
    logger.info("=" * 60)


def _print_validation_summary(label: str, df: pd.DataFrame, status_col: str) -> None:
    """Print a clean PASS/FAIL/OTHER summary table to the console."""
    if df.empty:
        logger.info("%s | No records to summarise.", label)
        return

    counts = df[status_col].value_counts().to_dict()
    total  = len(df)
    logger.info(
        "%s Summary | Total: %d | %s",
        label,
        total,
        " | ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_pipeline()
