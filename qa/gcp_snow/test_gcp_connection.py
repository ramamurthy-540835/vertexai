"""
test_gcp_connection.py
----------------------
Quick connectivity test for GCP Cloud SQL.
Run this BEFORE deploying to confirm your connection works.

Steps:
  1. Start Cloud SQL Auth Proxy in a separate terminal:
     cloud-sql-proxy.exe p-601-np-bcleadsmgmt-qat:us-central1:lead-mgmt-qat --port=5432

  2. Set environment variables in THIS terminal:
     set GCP_DB_PASSWORD=your_password_here
     set USE_PROXY=true

  3. Run this script from the repo root:
     python test_gcp_connection.py
"""

import sys
import os
import logging

# Show INFO logs so you can see what's happening
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

# Allow  "from src.gcp_client import ..."  when run from repo root
sys.path.insert(0, ".")

from src.gcp_client import GCPClient

def run_test():
    print("\n========================================")
    print("  GCP Cloud SQL Connection Test")
    print("========================================\n")

    # Confirm env vars are set before trying
    password = os.getenv("GCP_DB_PASSWORD", "")
    proxy    = os.getenv("USE_PROXY", "false")

    if not password:
        print("ERROR: GCP_DB_PASSWORD is not set.")
        print("  Windows : set GCP_DB_PASSWORD=your_password")
        print("  Mac/Linux: export GCP_DB_PASSWORD=your_password")
        sys.exit(1)

    print(f"USE_PROXY     : {proxy}")
    print(f"GCP_DB_PASSWORD: {'*' * len(password)}  (set)\n")

    try:
        with GCPClient() as client:

            # ── Test 1: transaction table ──────────────────────────────
            print("Test 1: transaction table (oms_company filter)")
            df = client.fetch_transaction(oms_company="TEST_VALUE")
            print(f"  Result : {len(df)} row(s) returned")
            if not df.empty:
                print(f"  Columns: {list(df.columns)}")
            else:
                print("  Columns: (run against a real value to see columns)")
            print()

            # ── Test 2: transaction table (pos_id filter) ──────────────
            print("Test 2: transaction table (pos_id filter)")
            df2 = client.fetch_transaction(pos_id="TEST_VALUE")
            print(f"  Result : {len(df2)} row(s) returned")
            print()

            # ── Test 3: lead table ─────────────────────────────────────
            print("Test 3: lead table")
            df3 = client.fetch_lead(lead_id="TEST_VALUE")
            print(f"  Result : {len(df3)} row(s) returned")
            print()

            # ── Test 4: account table ──────────────────────────────────
            print("Test 4: account table")
            df4 = client.fetch_account(account_id="TEST_VALUE")
            print(f"  Result : {len(df4)} row(s) returned")
            print()

            print("========================================")
            print("  All connection tests PASSED")
            print("  (0 rows = connection works, TEST_VALUE just has no match)")
            print("========================================\n")

    except RuntimeError as e:
        print(f"\nCONNECTION FAILED: {e}")
        print("\nCommon causes:")
        print("  1. Auth Proxy not running — start it first (see instructions at top of this file)")
        print("  2. USE_PROXY not set to true — set USE_PROXY=true")
        print("  3. Wrong DB user — default is 'postgres', set GCP_DB_USER if yours differs")
        print("  4. Wrong password — double-check GCP_DB_PASSWORD")
        print("  5. Your GCP account lacks 'Cloud SQL Client' IAM role — ask your GCP admin")
        sys.exit(1)

if __name__ == "__main__":
    run_test()
