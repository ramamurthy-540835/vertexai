# step3_lead_validation.py

import json
import pandas as pd

from clients.gcp_client import GCPClient
from clients.servicenow_client import ServiceNowClient


# ---------------------------------------------------
# LOAD CONFIGS
# ---------------------------------------------------

with open("POS_MATCH_VALIDATION/config/app_config.json") as f:
    app_config = json.load(f)

with open("POS_MATCH_VALIDATION/config/field_mapping.json") as f:
    field_mapping_config = json.load(f)

lead_mapping = field_mapping_config["lead_fields"]

account_mapping = field_mapping_config["account_fields"]


# ---------------------------------------------------
# CLIENTS
# ---------------------------------------------------

sn_client = ServiceNowClient(app_config)

gcp_client = GCPClient(app_config)


# ---------------------------------------------------
# FETCH SERVICENOW DATA
# ---------------------------------------------------

sn_df = sn_client.fetch_leads()

print(f"ServiceNow rows fetched: {len(sn_df)}")


# ---------------------------------------------------
# PROCESS ROWS
# ---------------------------------------------------

output_rows = []

for idx, row in sn_df.iterrows():

    try:

        # ---------------------------------------------------
        # LEAD ID
        # ---------------------------------------------------

        lead_id = row.get("sys_id", "")

        # ---------------------------------------------------
        # FETCH GCP LEAD
        # ---------------------------------------------------

        gcp_lead_df = gcp_client.fetch_lead_data(
            lead_id=lead_id
        )

        # ---------------------------------------------------
        # HANDLE NO LEAD
        # ---------------------------------------------------

        if gcp_lead_df.empty:

            output_rows.append({
                "lead_id": lead_id,
                "field_name": "",
                "servicenow_value": "",
                "gcp_value": "",
                "pass_fail": "LEAD_NOT_FOUND"
            })

            continue

        # ---------------------------------------------------
        # GCP LEAD RECORD
        # ---------------------------------------------------

        gcp_lead_record = gcp_lead_df.iloc[0].to_dict()

        # ---------------------------------------------------
        # ACCOUNT ID
        # ---------------------------------------------------

        account_id = gcp_lead_record.get("account_id", "")

        # ---------------------------------------------------
        # FETCH GCP ACCOUNT
        # ---------------------------------------------------

        gcp_account_df = gcp_client.fetch_account_data(
            account_id=account_id
        )

        # ---------------------------------------------------
        # HANDLE NO ACCOUNT
        # ---------------------------------------------------

        if gcp_account_df.empty:

            output_rows.append({
                "lead_id": lead_id,
                "field_name": "",
                "servicenow_value": "",
                "gcp_value": "",
                "pass_fail": "ACCOUNT_NOT_FOUND"
            })

            continue

        # ---------------------------------------------------
        # GCP ACCOUNT RECORD
        # ---------------------------------------------------

        gcp_account_record = gcp_account_df.iloc[0].to_dict()

        # ---------------------------------------------------
        # COMPARE LEAD FIELDS
        # ---------------------------------------------------

        for sn_col, gcp_col in lead_mapping.items():

            sn_value = str(row.get(sn_col, "")).strip()

            gcp_value = str(
                gcp_lead_record.get(gcp_col, "")
            ).strip()

            pass_fail = (
                "PASS"
                if sn_value == gcp_value
                else "FAIL"
            )

            output_rows.append({

                "lead_id": lead_id,

                "field_name": sn_col,

                "servicenow_value": sn_value,

                "gcp_value": gcp_value,

                "pass_fail": pass_fail
            })

        # ---------------------------------------------------
        # COMPARE ACCOUNT FIELDS
        # ---------------------------------------------------

        for sn_col, gcp_col in account_mapping.items():

            sn_value = str(row.get(sn_col, "")).strip()

            gcp_value = str(
                gcp_account_record.get(gcp_col, "")
            ).strip()

            pass_fail = (
                "PASS"
                if sn_value == gcp_value
                else "FAIL"
            )

            output_rows.append({

                "lead_id": lead_id,

                "field_name": sn_col,

                "servicenow_value": sn_value,

                "gcp_value": gcp_value,

                "pass_fail": pass_fail
            })

    except Exception as e:

        output_rows.append({

            "lead_id": row.get("sys_id", ""),

            "field_name": "",

            "servicenow_value": "",

            "gcp_value": "",

            "pass_fail": "ERROR",

            "error": str(e)
        })


# ---------------------------------------------------
# OUTPUT DF
# ---------------------------------------------------

output_df = pd.DataFrame(output_rows)


# ---------------------------------------------------
# SAVE OUTPUT
# ---------------------------------------------------

output_path = (
    "POS_MATCH_VALIDATION/output/lead_validation/"
    "lead_validation.csv"
)

output_df.to_csv(output_path, index=False)

print(f"Saved: {output_path}")