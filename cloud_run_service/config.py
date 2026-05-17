"""Environment configuration for the service."""

import os

PROJECT_ID  = os.environ.get("PROJECT_ID")
DB_INSTANCE = os.environ.get("DB_INSTANCE_CONNECTION_NAME")  # PROJECT:REGION:INSTANCE
DB_NAME     = os.environ.get("DB_NAME")
DB_USER     = os.environ.get("DB_USER")                      # SA email minus .gserviceaccount.com
DB_SCHEMA   = os.environ.get("DB_SCHEMA", "lead_mgmt_adt")

UPDATED_BY  = "ServiceNow"