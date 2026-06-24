#!/usr/bin/env python3
"""Pull match run report artifacts from GCS to local reports/ tree.

Searches two GCS prefixes in order:
  1. reports/lead_match/ctoteam/<warehouse>/<run_id>/   (post-analysis artifacts)
  2. preflight/lead_match/ctoteam/<warehouse>/           (preflight-only artifacts)

Downloads everything found into:
  reports/lead_match/ctoteam/<warehouse>/<run_id>/

Usage:
  python3 scripts/pull_gcs_report.py \
    --bucket lead-match-ctoteam \
    --warehouse 827 \
    --run-id github-28029672690-1-827
"""

import argparse
import logging
import sys
from pathlib import Path

from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_FILES = {"summary.json", "matches.csv", "report.md"}
PREFLIGHT_FILES = {"result.json", "report.md", "READY"}
OPTIONAL_FILES = {"comparative_analysis.md"}


def _download_prefix(client: storage.Client, bucket_name: str, prefix: str,
                     local_dir: Path) -> set[str]:
    """Download all blobs under a GCS prefix. Returns set of filenames downloaded."""
    blobs = list(client.list_blobs(bucket_name, prefix=prefix))
    downloaded = set()
    for blob in blobs:
        filename = blob.name.removeprefix(prefix)
        if not filename or filename.startswith("."):
            continue
        dest = local_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        size_kb = dest.stat().st_size / 1024
        downloaded.add(filename)
        logger.info("  %-40s  %8.1f KB", filename, size_kb)
    return downloaded


def pull_report(bucket_name: str, warehouse: str, run_id: str) -> bool:
    local_dir = REPO_ROOT / "reports" / "lead_match" / "ctoteam" / warehouse / run_id
    local_dir.mkdir(parents=True, exist_ok=True)

    client = storage.Client()
    all_downloaded: set[str] = set()

    # --- Try reports/ prefix first (post-analysis artifacts) ---
    reports_prefix = f"reports/lead_match/ctoteam/{warehouse}/{run_id}/"
    logger.info("Checking gs://%s/%s ...", bucket_name, reports_prefix)
    downloaded = _download_prefix(client, bucket_name, reports_prefix, local_dir)
    if downloaded:
        logger.info("  Found %d file(s) under reports/ prefix", len(downloaded))
        all_downloaded.update(downloaded)
    else:
        logger.info("  No report artifacts yet (analysis workflow may not have run)")

    # --- Try preflight/ prefix (always available after match run) ---
    preflight_prefix = f"preflight/lead_match/ctoteam/{warehouse}/"
    logger.info("Checking gs://%s/%s ...", bucket_name, preflight_prefix)
    preflight_dir = local_dir / "preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    pf_downloaded = _download_prefix(client, bucket_name, preflight_prefix, preflight_dir)
    if pf_downloaded:
        logger.info("  Found %d preflight file(s)", len(pf_downloaded))
        all_downloaded.update(f"preflight/{f}" for f in pf_downloaded)
    else:
        logger.info("  No preflight artifacts found")

    if not all_downloaded:
        logger.error("No artifacts found for warehouse %s, run %s", warehouse, run_id)
        return False

    # --- Checklist ---
    logger.info("")
    logger.info("Download checklist:")
    has_reports = REPORT_FILES.issubset(all_downloaded)

    for f in sorted(REPORT_FILES):
        found = f in all_downloaded
        logger.info("  [%s] %s (report)", "x" if found else " ", f)
    for f in sorted(OPTIONAL_FILES):
        found = f in all_downloaded
        logger.info("  [%s] %s (optional)", "x" if found else " ", f)
    for f in sorted(f for f in all_downloaded if f.startswith("preflight/")):
        logger.info("  [x] %s (preflight)", f)

    logger.info("")
    logger.info("Local path: %s", local_dir)

    if not has_reports:
        logger.warning(
            "Report artifacts (matches.csv, summary.json) not yet in GCS. "
            "Run the analysis workflow first:\n"
            "  gh workflow run lead_match_analysis.yml "
            "-f warehouse=%s -f run_id=%s",
            warehouse, run_id,
        )

    return True


def main():
    parser = argparse.ArgumentParser(description="Pull match run report from GCS")
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--warehouse", required=True, help="Warehouse number")
    parser.add_argument("--run-id", required=True, help="Match run ID")
    args = parser.parse_args()

    ok = pull_report(args.bucket, args.warehouse, args.run_id)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
