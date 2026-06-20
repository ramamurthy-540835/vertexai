#!/usr/bin/env python3
"""
Warehouse Smoke Test Compatibility Wrapper
Legacy filename kept for compatibility. This wrapper now points to the generic runtime smoke test.

Warehouse smoke test must run inside Cloud Run Job, not GitHub runner, because Cloud SQL is accessed through GCP runtime connectivity.
"""

import sys
import os

warehouse = os.environ.get("WAREHOUSE_SCOPE") or os.environ.get("WAREHOUSE") or ""

print("=" * 70)
print("WAREHOUSE SMOKE TEST (COMPATIBILITY WRAPPER)")
print("=" * 70)
print("Warehouse smoke test must run inside Cloud Run Job, not GitHub runner, because Cloud SQL is accessed through GCP runtime connectivity.")
print("")
print("To run the new parameterizable smoke test inside Cloud Run:")
print("  gcloud run jobs execute lead-match-warehouse-smoke \\")
print("    --region=us-central1 \\")
print("    --update-env-vars=\"WAREHOUSE_SCOPE=<warehouse>\" \\")
print("    --wait")
print("")
print("Or run the job runner module locally (if ALLOW_LOCAL_DB=true):")
print("  python -m lead_match_runtime.job_runner smoke --warehouse <warehouse>")
print("=" * 70)

# If they really want to execute it locally and ALLOW_LOCAL_DB is true, we can forward to lead_match_runtime.smoke_test
if os.environ.get("ALLOW_LOCAL_DB", "false").lower() == "true":
    print("[INFO] ALLOW_LOCAL_DB is true. Invoking the generic smoke test module...")
    from lead_match_runtime.smoke_test import main
    if warehouse and "--warehouse" not in sys.argv:
        sys.argv.extend(["--warehouse", warehouse])
    main()
else:
    print("[ERROR] Direct database access is disabled. Refusing to run from GitHub runner.")
    sys.exit(1)
