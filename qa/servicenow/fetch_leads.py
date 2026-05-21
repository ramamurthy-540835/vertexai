import os
import requests
import pandas as pd
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load credentials from .env
load_dotenv()

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------
INSTANCE = os.getenv("SNOW_INSTANCE")
CLIENT_ID = os.getenv("SNOW_CLIENT_ID")
CLIENT_SECRET = os.getenv("SNOW_CLIENT_SECRET")
TABLE = "sn_retail_lead"

# ---------------------------------------------------
# STEP 1 - GENERATE OAUTH TOKEN
# ---------------------------------------------------
token_url = f"https://{INSTANCE}/oauth_token.do"

token_response = requests.post(
    token_url,
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    },
    verify=False
)
token_response.raise_for_status()
access_token = token_response.json()["access_token"]
print("OAuth token generated successfully")

# ---------------------------------------------------
# STEP 2 - FETCH LEADS
# ---------------------------------------------------
table_url = f"https://{INSTANCE}/api/now/table/{TABLE}"

headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json"
}

params = {
    "sysparm_query":
        "account.sys_updated_onONToday@javascript:gs.beginningOfToday()@javascript:gs.endOfToday()^accountSTARTSWITHTEST - 06",
    "sysparm_fields": ",".join([
        "sys_id", "number", "sys_updated_on",
        "u_warehouse_number", "u_lead_source_new", "u_membership_number",
        "account.name", "account.street", "account.city",
        "account.state", "account.u_zip_code", "account.u_phone",
        "account.u_contact_email", "account.u_bd_industry_new",
        "account.u_industry_codes", "account.notes"
    ]),
    "sysparm_display_value": "true",
    "sysparm_limit": "1000"
}

response = requests.get(table_url, headers=headers, params=params, verify=False)
response.raise_for_status()

data = response.json()["result"]
print(f"Records fetched: {len(data)}")

# ---------------------------------------------------
# STEP 3 - DATAFRAME
# ---------------------------------------------------
df = pd.DataFrame(data)
print(df.head())

df.to_csv("servicenow_leads.csv", index=False)
print("CSV saved!")