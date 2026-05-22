# step2_match_validation.py

import json
import pandas as pd

from clients.gcp_client import GCPClient


# ---------------------------------------------------
# LOAD CONFIGS
# ---------------------------------------------------

with open("pos_match_validation/config/app_config.json") as f:
    app_config = json.load(f)

with open("pos_match_validation/config/expected_results.json") as f:
    expected_config = json.load(f)


# ---------------------------------------------------
# LOAD STEP 1 OUTPUT
# ---------------------------------------------------

mapping_file = (
    "pos_match_validation/output/pos_mapping/"
    "pos_mapping.csv"
)

mapping_df = pd.read_csv(mapping_file)

print(f"Rows found: {len(mapping_df)}")


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

for idx, row in mapping_df.iterrows():

    try:

        # ---------------------------------------------------
        # INPUT VALUES
        # ---------------------------------------------------

        gcp_pos_id = row.get("GCP_POS_ID", "")

        scenario = row.get("scenario", "")

        match_type_label = row.get("match_type_label", "")

        # ---------------------------------------------------
        # EXPECTED VALUES
        # ---------------------------------------------------

        expected = expected_config[scenario][match_type_label]

        expected_match_type = expected["match_type"]

        expected_score = expected["score"]

        # ---------------------------------------------------
        # HANDLE EMPTY POS ID
        # ---------------------------------------------------

        if pd.isna(gcp_pos_id) or str(gcp_pos_id).strip() == "":

            output_row = row.to_dict()

            output_row["expected_match_type"] = expected_match_type

            output_row["actual_match_type"] = ""

            output_row["expected_score"] = expected_score

            output_row["actual_score"] = ""

            output_row["type_match"] = False

            output_row["score_match"] = False

            output_row["match_validation_pass_fail"] = "NO_POS_ID"

            output_rows.append(output_row)

            continue

        # ---------------------------------------------------
        # QUERY GCP
        # ---------------------------------------------------

        gcp_df = gcp_client.fetch_transaction_data(
            pos_id=gcp_pos_id
        )

        # ---------------------------------------------------
        # HANDLE NO RECORD
        # ---------------------------------------------------

        if gcp_df.empty:

            output_row = row.to_dict()

            output_row["expected_match_type"] = expected_match_type

            output_row["actual_match_type"] = ""

            output_row["expected_score"] = expected_score

            output_row["actual_score"] = ""

            output_row["type_match"] = False

            output_row["score_match"] = False

            output_row["match_validation_pass_fail"] = "NOT_FOUND"

            output_rows.append(output_row)

            continue

        # ---------------------------------------------------
        # GCP RECORD
        # ---------------------------------------------------

        gcp_record = gcp_df.iloc[0].to_dict()

        actual_match_type = gcp_record.get("match_type", "")

        actual_score = gcp_record.get("match_score", "")

        # ---------------------------------------------------
        # VALIDATION
        # ---------------------------------------------------

        type_match = (
            str(actual_match_type).strip().upper()
            ==
            str(expected_match_type).strip().upper()
        )

        try:

            score_match = (
                int(float(actual_score))
                ==
                int(float(expected_score))
            )

        except:

            score_match = False

        # ---------------------------------------------------
        # PASS / FAIL
        # ---------------------------------------------------

        if type_match and score_match:

            pass_fail = "PASS"

        else:

            pass_fail = "FAIL"

        # ---------------------------------------------------
        # OUTPUT
        # ---------------------------------------------------

        output_row = row.to_dict()

        output_row["expected_match_type"] = expected_match_type

        output_row["actual_match_type"] = actual_match_type

        output_row["expected_score"] = expected_score

        output_row["actual_score"] = actual_score

        output_row["type_match"] = type_match

        output_row["score_match"] = score_match

        output_row["match_validation_pass_fail"] = pass_fail

        output_rows.append(output_row)

    except Exception as e:

        failed_row = row.to_dict()

        failed_row["expected_match_type"] = ""

        failed_row["actual_match_type"] = ""

        failed_row["expected_score"] = ""

        failed_row["actual_score"] = ""

        failed_row["type_match"] = False

        failed_row["score_match"] = False

        failed_row["match_validation_pass_fail"] = "ERROR"

        failed_row["error"] = str(e)

        output_rows.append(failed_row)


# ---------------------------------------------------
# OUTPUT DF
# ---------------------------------------------------

output_df = pd.DataFrame(output_rows)


# ---------------------------------------------------
# SAVE OUTPUT
# ---------------------------------------------------

output_path = (
    "pos_match_validation/output/match_validation/"
    "match_validation.csv"
)

output_df.to_csv(output_path, index=False)

print(f"Saved: {output_path}")