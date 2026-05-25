# step4_match_sync_validation.py

import json
import pandas as pd
import os
from clients.gcp_client import GCPClient
from clients.servicenow_client import ServiceNowClient


# ---------------------------------------------------
# LOAD CONFIGS
# ---------------------------------------------------

with open("config/app_config.json") as f:
    app_config = json.load(f)


# ---------------------------------------------------
# LOAD STEP1 OUTPUT
# ---------------------------------------------------

mapping_file = (
    f"{os.environ['TMP_POS_MAPPING_DIR']}/"
    "pos_mapping.csv"
)

mapping_df = pd.read_csv(mapping_file)

print(f"Rows found: {len(mapping_df)}")


# ---------------------------------------------------
# CLIENTS
# ---------------------------------------------------

config_file_path = (
    "configuration_qa.ini"
)

gcp_client = GCPClient(config_file_path)

sn_client = ServiceNowClient(app_config)


# ---------------------------------------------------
# PROCESS ROWS
# ---------------------------------------------------

output_rows = []

for idx, row in mapping_df.iterrows():

    try:

        # ---------------------------------------------------
        # INPUTS
        # ---------------------------------------------------

        gcp_pos_id = row.get("GCP_POS_ID", "")

        oms_company = row.get("Company OMS Info", "")

        pos_number = row.get("POS ID", "")

        # ---------------------------------------------------
        # FETCH GCP TRANSACTION
        # ---------------------------------------------------

        gcp_df = gcp_client.fetch_transaction_data(
            pos_id=gcp_pos_id
        )

        # ---------------------------------------------------
        # HANDLE NO GCP RECORD
        # ---------------------------------------------------

        if gcp_df.empty:

            output_rows.append({

                "pos_id": gcp_pos_id,

                "pos_number": pos_number,

                "gcp_match_type": "",

                "sn_match_type": "",

                "gcp_match_score": "",

                "sn_match_score": "",

                "pass_fail": "GCP_NOT_FOUND"
            })

            continue

        # ---------------------------------------------------
        # GCP RECORD
        # ---------------------------------------------------

        gcp_record = gcp_df.iloc[0].to_dict()

        gcp_match_type = gcp_record.get(
            "match_type",
            ""
        )

        gcp_match_score = gcp_record.get(
            "match_score",
            ""
        )

        # ---------------------------------------------------
        # FETCH SERVICENOW POS RECORD
        # ---------------------------------------------------

        sn_df = sn_client.fetch_pos_record(
            pos_number=pos_number
        )

        # ---------------------------------------------------
        # HANDLE NO SN RECORD
        # ---------------------------------------------------

        if sn_df.empty:

            output_rows.append({

                "pos_id": gcp_pos_id,

                "pos_number": pos_number,

                "gcp_match_type": gcp_match_type,

                "sn_match_type": "",

                "gcp_match_score": gcp_match_score,

                "sn_match_score": "",

                "pass_fail": "SN_NOT_FOUND"
            })

            continue

        # ---------------------------------------------------
        # SN RECORD
        # ---------------------------------------------------

        sn_record = sn_df.iloc[0].to_dict()

        

        sn_match_type = sn_record.get(
            "u_match_result",
            ""
        )

        sn_match_score = sn_record.get(
            "u_match_value",
            ""
        )

        # ---------------------------------------------------
        # VALIDATION
        # ---------------------------------------------------

        gcp_score = str(gcp_match_score).split(".")[0]

        sn_score = str(sn_match_score).split(".")[0]

        if (
            str(gcp_match_type).strip().upper()
            ==
            str(sn_match_type).strip().upper()
        ) and (
            gcp_score == sn_score
        ):

            pass_fail = "PASS"

        else:

            pass_fail = "FAIL"

        # ---------------------------------------------------
        # OUTPUT
        # ---------------------------------------------------

        output_rows.append({

            "pos_id": gcp_pos_id,

            "pos_number": pos_number,

            "gcp_match_type": gcp_match_type,

            "sn_match_type": sn_match_type,

            "gcp_match_score": gcp_match_score,

            "sn_match_score": sn_match_score,

            "pass_fail": pass_fail
        })

    except Exception as e:

        output_rows.append({

            "pos_id": row.get("GCP_POS_ID", ""),

            "pos_number": row.get(
                "POS ID",
                ""
            ),

            "gcp_match_type": "",

            "sn_match_type": "",

            "gcp_match_score": "",

            "sn_match_score": "",

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

output_dir = os.environ["TMP_MATCH_SYNC_VALIDATION_DIR"]

os.makedirs(output_dir, exist_ok=True)

output_path = (
    f"{output_dir}/match_sync_validation.csv"
)

output_df.to_csv(output_path, index=False)

print(f"Saved: {output_path}")