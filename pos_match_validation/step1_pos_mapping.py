# step1_pos_mapping.py

import json
import pandas as pd

from clients.gcp_client import GCPClient

from utils.file_utils import get_latest_file
from utils.parser_utils import parse_company_oms_info
from utils.compare_utils import compare_pos_vs_gcp


# ---------------------------------------------------
# LOAD CONFIGS
# ---------------------------------------------------

with open("pos_match_validation/config/app_config.json") as f:
    app_config = json.load(f)

with open("pos_match_validation/config/field_mapping.json") as f:
    field_mapping_config = json.load(f)

field_mapping = field_mapping_config["pos_to_gcp"]


# ---------------------------------------------------
# GET LATEST POS FILE
# ---------------------------------------------------

pos_file = get_latest_file(
    app_config["paths"]["pos_input"]
)

print(f"POS file found: {pos_file}")


# ---------------------------------------------------
# READ POS FILE
# ---------------------------------------------------

if pos_file.endswith(".csv"):

    pos_df = pd.read_csv(pos_file)

elif pos_file.endswith(".xlsx"):

    pos_df = pd.read_excel(pos_file)

else:

    raise Exception(
        f"Unsupported file format: {pos_file}"
    )

print(f"Rows found: {len(pos_df)}")


# ---------------------------------------------------
# GCP CLIENT
# ---------------------------------------------------

config_file_path = (
    "pos_match_validation/configuration_qa.ini"
)

gcp_client = GCPClient(config_file_path)


# ---------------------------------------------------
# PROCESS ROWS
# ---------------------------------------------------

output_rows = []

for idx, row in pos_df.iterrows():

    try:

        # ---------------------------------------------------
        # OMS ID
        # ---------------------------------------------------

        oms_id = row["Company OMS Info"]

        parsed = parse_company_oms_info(oms_id)

        # ---------------------------------------------------
        # QUERY GCP
        # ---------------------------------------------------

        gcp_df = gcp_client.fetch_transaction_data(
            oms_company=oms_id
        )

        # ---------------------------------------------------
        # HANDLE NO RECORD
        # ---------------------------------------------------

        if gcp_df.empty:

            output_row = row.to_dict()

            output_row["GCP_POS_ID"] = ""

            output_row["scenario"] = parsed["scenario"]

            output_row["match_type_label"] = parsed["match_type_label"]

            output_row["mapping_pass_fail"] = "NOT_FOUND"

            output_row["mismatch_fields"] = "No record found in GCP"

            output_rows.append(output_row)

            continue

        # ---------------------------------------------------
        # GCP RECORD
        # ---------------------------------------------------

        gcp_record = gcp_df.iloc[0].to_dict()

        gcp_pos_id = gcp_record.get("pos_id", "")

        # ---------------------------------------------------
        # COMPARE
        # ---------------------------------------------------

        compare_result = compare_pos_vs_gcp(
            pos_row=row,
            gcp_row=gcp_record,
            field_mapping=field_mapping
        )

        # ---------------------------------------------------
        # OUTPUT
        # ---------------------------------------------------

        output_row = row.to_dict()

        output_row["GCP_POS_ID"] = gcp_pos_id

        output_row["scenario"] = parsed["scenario"]

        output_row["match_type_label"] = parsed["match_type_label"]

        output_row["mapping_pass_fail"] = compare_result["pass_fail"]

        output_row["mismatch_fields"] = compare_result["mismatch_fields"]

        output_rows.append(output_row)

    except Exception as e:

        failed_row = row.to_dict()

        failed_row["GCP_POS_ID"] = ""

        failed_row["scenario"] = ""

        failed_row["match_type_label"] = ""

        failed_row["mapping_pass_fail"] = "ERROR"

        failed_row["mismatch_fields"] = str(e)

        output_rows.append(failed_row)


# ---------------------------------------------------
# OUTPUT DF
# ---------------------------------------------------

output_df = pd.DataFrame(output_rows)


# ---------------------------------------------------
# SAVE OUTPUT
# ---------------------------------------------------

output_path = (
    "pos_match_validation/output/pos_mapping/"
    "pos_mapping.csv"
)

output_df.to_csv(output_path, index=False)

print(f"Saved: {output_path}")