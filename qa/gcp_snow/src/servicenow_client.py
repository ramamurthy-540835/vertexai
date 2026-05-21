"""
servicenow_client.py
--------------------
ServiceNow REST API integration layer for POS Match Validation Automation.

Handles:
  - OAuth 2.0 Client Credentials token fetch + auto-refresh
  - Fetching leads from ServiceNow by lead_id or recently updated
  - Pagination handling for large result sets
  - Returns data as list of dicts (JSON) — consistent with project convention

Instance : Set via SN_BASE_URL environment variable
Auth     : OAuth 2.0 Client Credentials

Environment variables (set before running):
  SN_BASE_URL       — e.g. https://your-instance.service-now.com
  SN_CLIENT_ID      — OAuth client ID
  SN_CLIENT_SECRET  — OAuth client secret
  SN_PAGE_SIZE      — records per page (default: 100)
"""

import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all from environment variables
# ---------------------------------------------------------------------------

SN_BASE_URL      = os.getenv("SN_BASE_URL",      "").rstrip("/")
SN_CLIENT_ID     = os.getenv("SN_CLIENT_ID",     "")
SN_CLIENT_SECRET = os.getenv("SN_CLIENT_SECRET", "")
SN_PAGE_SIZE     = int(os.getenv("SN_PAGE_SIZE", "100"))

# ServiceNow REST API paths
TOKEN_PATH       = "/oauth_token.do"
LEAD_TABLE_PATH  = "/api/now/table/sn_si_incident"   # update to your lead table name


class ServiceNowClient:
    """
    ServiceNow REST client with OAuth 2.0 Client Credentials auth.

    Token is fetched on first use and automatically refreshed when expired.
    All methods return data as list of dicts (JSON).

    Usage:
        client = ServiceNowClient()
        leads  = client.fetch_lead_by_id("LEAD123")
        recent = client.fetch_recent_leads(minutes=60)
    """

    def __init__(
        self,
        base_url: str      = SN_BASE_URL,
        client_id: str     = SN_CLIENT_ID,
        client_secret: str = SN_CLIENT_SECRET,
        page_size: int     = SN_PAGE_SIZE,
    ):
        if not base_url:
            raise ValueError(
                "ServiceNow base URL not set.\n"
                "Windows : set SN_BASE_URL=https://your-instance.service-now.com\n"
                "Mac/Linux: export SN_BASE_URL=https://your-instance.service-now.com"
            )
        if not client_id or not client_secret:
            raise ValueError(
                "ServiceNow OAuth credentials not set.\n"
                "Set SN_CLIENT_ID and SN_CLIENT_SECRET environment variables."
            )

        self.base_url      = base_url.rstrip("/")
        self.client_id     = client_id
        self.client_secret = client_secret
        self.page_size     = page_size

        self._access_token: Optional[str] = None
        self._token_expiry: float         = 0.0      # epoch seconds

        logger.info("ServiceNowClient initialised | base_url=%s", self.base_url)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _fetch_token(self) -> None:
        """Fetch a new OAuth access token using client credentials grant."""
        url = f"{self.base_url}{TOKEN_PATH}"
        payload = {
            "grant_type":    "client_credentials",
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
        }
        logger.debug("Fetching OAuth token from %s", url)
        try:
            resp = requests.post(url, data=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data["access_token"]
            # expires_in is in seconds; subtract 30s buffer
            self._token_expiry = time.time() + int(data.get("expires_in", 1800)) - 30
            logger.info("OAuth token fetched successfully. Expires in ~%ds", data.get("expires_in", 1800))

        except requests.RequestException as exc:
            raise RuntimeError(f"ServiceNow token fetch failed: {exc}") from exc

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if expired."""
        if not self._access_token or time.time() >= self._token_expiry:
            self._fetch_token()
        return self._access_token

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }

    # ------------------------------------------------------------------
    # Internal paginated GET helper
    # ------------------------------------------------------------------

    def _get_paginated(self, url: str, params: dict) -> list[dict]:
        """
        Fetch all pages from a ServiceNow table API endpoint.

        Args:
            url    : Full API URL.
            params : Query parameters dict (sysparm_* filters etc.)

        Returns:
            Flat list of all record dicts across all pages.
        """
        all_records = []
        offset      = 0
        params      = {**params, "sysparm_limit": self.page_size}

        while True:
            params["sysparm_offset"] = offset
            logger.debug("GET %s | offset=%d | limit=%d", url, offset, self.page_size)

            try:
                resp = requests.get(
                    url,
                    headers=self._auth_headers(),
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                records = resp.json().get("result", [])
            except requests.RequestException as exc:
                raise RuntimeError(f"ServiceNow API request failed: {exc}") from exc

            if not records:
                break

            all_records.extend(records)
            logger.debug("Fetched %d records (total so far: %d)", len(records), len(all_records))

            if len(records) < self.page_size:
                break   # last page

            offset += self.page_size

        logger.info("Total records fetched from ServiceNow: %d", len(all_records))
        return all_records

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch_lead_by_id(self, lead_id: str) -> list[dict]:
        """
        Fetch a single lead record from ServiceNow by its sys_id or lead_id.

        Used by:
            Step 3 — Lead Validation

        Args:
            lead_id : The ServiceNow lead sys_id or number field value.

        Returns:
            List of dicts — typically 1 item if found, empty list if not.

        Raises:
            ValueError  : If lead_id is empty.
            RuntimeError: On API failure.

        Example:
            leads = client.fetch_lead_by_id("LEAD123")
        """
        if not lead_id:
            raise ValueError("fetch_lead_by_id() requires a non-empty lead_id.")

        url    = f"{self.base_url}{LEAD_TABLE_PATH}"
        params = {
            "sysparm_query":        f"sys_id={lead_id}^ORnumber={lead_id}",
            "sysparm_display_value": "false",
        }
        logger.info("Fetching ServiceNow lead | lead_id=%s", lead_id)
        return self._get_paginated(url, params)

    def fetch_recent_leads(self, minutes: int = 60) -> list[dict]:
        """
        Fetch leads created or updated within the last N minutes.

        Used by:
            Step 3 — Lead Validation (batch mode)

        Args:
            minutes : How far back to look (default: 60 minutes).

        Returns:
            List of dicts, one per lead record.

        Raises:
            RuntimeError: On API failure.

        Example:
            leads = client.fetch_recent_leads(minutes=120)
        """
        url    = f"{self.base_url}{LEAD_TABLE_PATH}"
        params = {
            # ServiceNow relative date filter: javascript:gs.minutesAgoStart(N)
            "sysparm_query":         f"sys_updated_on>=javascript:gs.minutesAgoStart({minutes})",
            "sysparm_display_value": "false",
        }
        logger.info("Fetching recent ServiceNow leads | last %d minutes", minutes)
        return self._get_paginated(url, params)

    def fetch_leads_by_ids(self, lead_ids: list[str]) -> list[dict]:
        """
        Fetch multiple leads by a list of IDs in a single encoded query.

        Used by:
            Step 3 — Lead Validation (batch, when lead_ids come from GCP)

        Args:
            lead_ids : List of lead sys_id or number values.

        Returns:
            List of dicts — one per matching record found.

        Raises:
            ValueError  : If lead_ids is empty.
            RuntimeError: On API failure.

        Example:
            leads = client.fetch_leads_by_ids(["LEAD001", "LEAD002", "LEAD003"])
        """
        if not lead_ids:
            raise ValueError("fetch_leads_by_ids() requires a non-empty list of lead_ids.")

        # Build IN query: sys_id=ID1^ORsys_id=ID2^OR...
        query  = "^OR".join([f"sys_id={lid}" for lid in lead_ids])
        url    = f"{self.base_url}{LEAD_TABLE_PATH}"
        params = {
            "sysparm_query":         query,
            "sysparm_display_value": "false",
        }
        logger.info("Fetching %d ServiceNow leads by ID list", len(lead_ids))
        return self._get_paginated(url, params)
