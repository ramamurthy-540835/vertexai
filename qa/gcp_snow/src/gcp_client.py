"""
gcp_client.py
-------------
GCP Cloud SQL integration layer for POS Match Validation Automation.

Handles fetching data from:
  - lead_mgmt_qat.transaction  (used in POS Mapping + Match Validation)
  - lead_mgmt_qat.account      (used in Lead Validation)
  - lead_mgmt_qat.lead         (used in Lead Validation)

Instance  : Cloud SQL — lead-mgmt-qat   (PostgreSQL 15)
Project   : p-601-np-bcleadsmgmt-qat
Region    : us-central1-c
Schema    : lead_mgmt_qat
IP Type   : PRIVATE (no public IP on this instance)

Connection strategy:
  - Uses Cloud SQL Python Connector with pg8000 driver (PostgreSQL)
  - IPTypes.PRIVATE because the instance has only a private IP (10.240.1.8)
  - For local development: run Cloud SQL Auth Proxy first, then set
    USE_PROXY=true so the code connects via 127.0.0.1:5432 instead

All fetched data is returned as pandas DataFrames (in-memory, not saved to files).
File writes are handled by the calling step modules (pos_mapper, match_validator, lead_validator).

Usage:
    from src.gcp_client import GCPClient

    client = GCPClient()

    # POS Mapping / Match Validation
    df = client.fetch_transaction(oms_company="ACME_001", pos_id="POS123")

    # Lead Validation
    df_lead    = client.fetch_lead(lead_id="LEAD456")
    df_account = client.fetch_account(account_id="ACC789", business_name="Acme Corp")

Local development (Cloud SQL Auth Proxy):
    1. Run proxy in a separate terminal:
       cloud-sql-proxy.exe p-601-np-bcleadsmgmt-qat:us-central1:lead-mgmt-qat --port=5432
    2. Set environment variable:
       set USE_PROXY=true          (Windows)
       export USE_PROXY=true       (Mac/Linux)
    3. Run your script normally — the client detects USE_PROXY and connects via localhost.
"""

import logging
import os
from typing import Any, Optional

import pg8000.dbapi
import pandas as pd
from google.cloud.sql.connector import Connector, IPTypes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration — all values read from environment variables.
# Set these in your shell before running; never hardcode secrets in this file.
#
# Required:
#   GCP_DB_PASSWORD   — your Cloud SQL database password
#
# Optional overrides (defaults shown below):
#   GCP_PROJECT_ID    — GCP project (default: p-601-np-bcleadsmgmt-qat)
#   GCP_REGION        — Cloud SQL region (default: us-central1)
#   GCP_INSTANCE_NAME — Cloud SQL instance name (default: lead-mgmt-qat)
#   GCP_DATABASE_NAME — PostgreSQL database/schema (default: lead_mgmt_qat)
#   GCP_DB_USER       — Database user (default: postgres)
#   USE_PROXY         — Set to "true" to connect via local Auth Proxy on 127.0.0.1:5432
# ---------------------------------------------------------------------------

PROJECT_ID    = os.getenv("GCP_PROJECT_ID",    "p-601-np-bcleadsmgmt-qat")
REGION        = os.getenv("GCP_REGION",        "us-central1")
INSTANCE_NAME = os.getenv("GCP_INSTANCE_NAME", "lead-mgmt-qat")       # FIXED: was lead-mgmt-db
DATABASE_NAME = os.getenv("GCP_DATABASE_NAME", "lead_mgmt_qat")
DB_USER       = os.getenv("GCP_DB_USER",       "postgres")             # PostgreSQL default user
DB_PASSWORD   = os.getenv("GCP_DB_PASSWORD",   "")
USE_PROXY     = os.getenv("USE_PROXY",         "false").lower() == "true"

# Cloud SQL connection name: project:region:instance
INSTANCE_CONNECTION_NAME = f"{PROJECT_ID}:{REGION}:{INSTANCE_NAME}"

# Schema-qualified table names (PostgreSQL syntax)
TABLE_TRANSACTION = "lead_mgmt_qat.transaction"
TABLE_LEAD        = "lead_mgmt_qat.lead"
TABLE_ACCOUNT     = "lead_mgmt_qat.account"


# ---------------------------------------------------------------------------
# GCPClient
# ---------------------------------------------------------------------------

class GCPClient:
    """
    Manages a Cloud SQL Connector instance (PostgreSQL / pg8000) and provides
    typed fetch methods for each table used in the validation automation.

    Supports two connection modes:
      1. Cloud SQL Connector (production/CI)  — default, uses private IP
      2. Auth Proxy mode (local development)  — set USE_PROXY=true in env

    The connector is lazily initialised on first use and reused across calls.
    """

    def __init__(
        self,
        project_id: str            = PROJECT_ID,
        region: str                = REGION,
        instance_name: str         = INSTANCE_NAME,
        database_name: str         = DATABASE_NAME,
        db_user: str               = DB_USER,
        db_password: Optional[str] = None,
        use_proxy: bool            = USE_PROXY,
    ):
        self.instance_connection_name = f"{project_id}:{region}:{instance_name}"
        self.database_name = database_name
        self.db_user       = db_user
        self.db_password   = db_password or DB_PASSWORD
        self.use_proxy     = use_proxy

        self._connector: Optional[Connector] = None

        if not self.db_password:
            raise ValueError(
                "DB password is not set.\n"
                "Windows : set GCP_DB_PASSWORD=your_password\n"
                "Mac/Linux: export GCP_DB_PASSWORD=your_password\n"
                "Or pass db_password= directly to GCPClient()."
            )

        logger.info(
            "GCPClient initialised | instance=%s | db=%s | proxy=%s",
            self.instance_connection_name, self.database_name, self.use_proxy,
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connector(self) -> Connector:
        """Return (or lazily create) the Cloud SQL Connector."""
        if self._connector is None:
            logger.debug("Creating Cloud SQL Connector for %s", self.instance_connection_name)
            self._connector = Connector()
        return self._connector

    def _get_connection(self) -> Any:
        """
        Open and return a pg8000 connection.

        Two modes:
          - use_proxy=False (default): Cloud SQL Connector with PRIVATE IP.
            Use this in GCP-hosted environments (Cloud Run, GCE, etc.)

          - use_proxy=True: Direct TCP to 127.0.0.1:5432 via Auth Proxy.
            Use this for local development when the Auth Proxy is running.
        """
        if self.use_proxy:
            # Local development via Cloud SQL Auth Proxy
            logger.debug("Connecting via Auth Proxy at 127.0.0.1:5432")
            conn = pg8000.dbapi.connect(
                host="127.0.0.1",
                port=5432,
                user=self.db_user,
                password=self.db_password,
                database=self.database_name,
            )
        else:
            # Production: Cloud SQL Connector with private IP
            logger.debug("Connecting via Cloud SQL Connector (PRIVATE IP)")
            connector = self._get_connector()
            conn = connector.connect(
                self.instance_connection_name,
                "pg8000",                        # FIXED: was "pymysql"
                user=self.db_user,
                password=self.db_password,
                db=self.database_name,
                ip_type=IPTypes.PRIVATE,         # FIXED: was IPTypes.PUBLIC — instance has no public IP
            )

        logger.debug("Connection established to %s", self.instance_connection_name)
        return conn

    def close(self) -> None:
        """Close the underlying Cloud SQL Connector (call at end of process)."""
        if self._connector:
            self._connector.close()
            self._connector = None
            logger.debug("Cloud SQL Connector closed.")

    # ------------------------------------------------------------------
    # Internal query helper
    # ------------------------------------------------------------------

    def _execute_query(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """
        Execute a parameterised SQL query and return all results as a DataFrame.

        Args:
            sql    : SQL string with %s placeholders (pg8000 style).
            params : Tuple of bind values corresponding to each %s.

        Returns:
            pandas DataFrame with all matching rows.
            Returns an empty DataFrame (no rows) if the query matches nothing.

        Raises:
            RuntimeError: Wraps any pg8000 / connector exception with context.
        """
        logger.debug("SQL: %s | Params: %s", sql, params)
        try:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)

                # Build DataFrame from cursor — works with pg8000 which does
                # not support DictCursor; column names come from cursor.description
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows    = cursor.fetchall()
                cursor.close()
            finally:
                conn.close()

            df = pd.DataFrame(rows, columns=columns)
            logger.debug("Query returned %d row(s) | columns: %s", len(df), list(df.columns))
            return df

        except Exception as exc:
            logger.error("Query failed | SQL: %s | Error: %s", sql, exc)
            raise RuntimeError(f"GCP query failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch_transaction(
        self,
        oms_company: Optional[str] = None,
        pos_id: Optional[str]      = None,
    ) -> pd.DataFrame:
        """
        Fetch all columns from lead_mgmt_qat.transaction.

        Used by:
            Step 1 — POS Mapping      : filter by oms_company
            Step 2 — Match Validation : filter by pos_id

        Args:
            oms_company : Filter on the oms_company column (optional).
            pos_id      : Filter on the pos_id column (optional).
                          At least one of these must be provided.

        Returns:
            DataFrame — all columns, rows matching the filter.
            Empty DataFrame if no rows match.

        Raises:
            ValueError  : If both oms_company and pos_id are None/empty.
            RuntimeError: On database or connection error.

        Examples:
            # Step 1 — POS Mapping
            df = client.fetch_transaction(oms_company="ACME_001")

            # Step 2 — Match Validation
            df = client.fetch_transaction(pos_id="POS123")

            # Both filters (AND)
            df = client.fetch_transaction(oms_company="ACME_001", pos_id="POS123")
        """
        if not oms_company and not pos_id:
            raise ValueError(
                "fetch_transaction() requires at least one filter: oms_company or pos_id."
            )

        conditions, params = [], []

        if oms_company:
            conditions.append("oms_company = %s")
            params.append(oms_company)
        if pos_id:
            conditions.append("pos_id = %s")
            params.append(pos_id)

        sql = f"SELECT * FROM {TABLE_TRANSACTION} WHERE {' AND '.join(conditions)}"

        logger.info("fetch_transaction | oms_company=%s | pos_id=%s", oms_company, pos_id)
        return self._execute_query(sql, tuple(params))

    def fetch_lead(self, lead_id: str) -> pd.DataFrame:
        """
        Fetch all columns from lead_mgmt_qat.lead for a given lead_id.

        Used by:
            Step 3 — Lead Validation

        Args:
            lead_id : The lead identifier to filter on. Required.

        Returns:
            DataFrame — all columns for the matching lead record(s).
            Empty DataFrame if lead_id is not found.

        Raises:
            ValueError  : If lead_id is empty or None.
            RuntimeError: On database or connection error.

        Example:
            df = client.fetch_lead(lead_id="LEAD456")
        """
        if not lead_id:
            raise ValueError("fetch_lead() requires a non-empty lead_id.")

        sql = f"SELECT * FROM {TABLE_LEAD} WHERE lead_id = %s"

        logger.info("fetch_lead | lead_id=%s", lead_id)
        return self._execute_query(sql, (lead_id,))

    def fetch_account(
        self,
        account_id: Optional[str]    = None,
        business_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch all columns from lead_mgmt_qat.account.

        Used by:
            Step 3 — Lead Validation

        Args:
            account_id    : Filter on the account_id column (optional).
            business_name : Filter on the business_name column (optional).
                            At least one of these must be provided.

        Returns:
            DataFrame — all columns, rows matching the filter(s).
            Empty DataFrame if no rows match.

        Raises:
            ValueError  : If both account_id and business_name are None/empty.
            RuntimeError: On database or connection error.

        Examples:
            df = client.fetch_account(account_id="ACC789")
            df = client.fetch_account(business_name="Acme Corp")
            df = client.fetch_account(account_id="ACC789", business_name="Acme Corp")
        """
        if not account_id and not business_name:
            raise ValueError(
                "fetch_account() requires at least one filter: account_id or business_name."
            )

        conditions, params = [], []

        if account_id:
            conditions.append("account_id = %s")
            params.append(account_id)
        if business_name:
            conditions.append("business_name = %s")
            params.append(business_name)

        sql = f"SELECT * FROM {TABLE_ACCOUNT} WHERE {' AND '.join(conditions)}"

        logger.info("fetch_account | account_id=%s | business_name=%s", account_id, business_name)
        return self._execute_query(sql, tuple(params))

    # ------------------------------------------------------------------
    # Unified dispatcher — fetch_gcp_data()
    # ------------------------------------------------------------------

    def fetch_gcp_data(
        self,
        table_name: str,
        oms_company: Optional[str]   = None,
        pos_id: Optional[str]        = None,
        lead_id: Optional[str]       = None,
        account_id: Optional[str]    = None,
        business_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Single entry point — routes to the correct fetch method by table_name.

        Routing:
            "transaction" → fetch_transaction(oms_company, pos_id)
            "lead"        → fetch_lead(lead_id)
            "account"     → fetch_account(account_id, business_name)

        Args:
            table_name    : "transaction", "lead", or "account" (case-insensitive).
            oms_company   : Used when table_name="transaction".
            pos_id        : Used when table_name="transaction".
            lead_id       : Used when table_name="lead".
            account_id    : Used when table_name="account".
            business_name : Used when table_name="account".

        Returns:
            pandas DataFrame with all matching rows (in-memory).

        Raises:
            ValueError  : Unknown table_name or missing required filters.
            RuntimeError: On database or connection error.

        Examples:
            # Step 1 — POS Mapping
            df = client.fetch_gcp_data("transaction", oms_company="ACME_001")

            # Step 2 — Match Validation
            df = client.fetch_gcp_data("transaction", pos_id="POS123")

            # Step 3 — Lead Validation
            df = client.fetch_gcp_data("lead",    lead_id="LEAD456")
            df = client.fetch_gcp_data("account", account_id="ACC789", business_name="Acme Corp")
        """
        t = table_name.strip().lower()

        if t == "transaction":
            return self.fetch_transaction(oms_company=oms_company, pos_id=pos_id)
        elif t == "lead":
            return self.fetch_lead(lead_id=lead_id)
        elif t == "account":
            return self.fetch_account(account_id=account_id, business_name=business_name)
        else:
            raise ValueError(
                f"Unknown table_name '{table_name}'. "
                "Expected one of: 'transaction', 'lead', 'account'."
            )

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
