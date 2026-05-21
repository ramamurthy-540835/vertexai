# utils/compare_utils.py

import pandas as pd


def normalize_value(value):

    if pd.isna(value):
        return ""

    return str(value).strip().lower()


def compare_pos_vs_gcp(pos_row, gcp_row, field_mapping):

    mismatches = []

    for pos_col, gcp_col in field_mapping.items():

        pos_value = normalize_value(pos_row.get(pos_col, ""))
        gcp_value = normalize_value(gcp_row.get(gcp_col, ""))

        if pos_value != gcp_value:
            mismatches.append(pos_col)

    return {
        "pass_fail": "PASS" if len(mismatches) == 0 else "FAIL",
        "mismatch_fields": ",".join(mismatches)
    }