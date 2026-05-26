# utils/parser_utils.py

def parse_company_oms_info(oms_id):

    # POS-20260520_143022-001-S6-FUZZY_MISMATCH

    parts = oms_id.split("-")

    return {
        "timestamp": parts[1],
        "run_id": parts[2],
        "scenario": parts[3],
        "match_type_label": parts[4]
    }

import re


def parse_matching_comments(comment):

    if not comment:

        return {
            "match_type": "",
            "match_score": ""
        }

    comment = str(comment)

    # ---------------------------------------------------
    # MATCH TYPE
    # ---------------------------------------------------

    if "Complete match" in comment:

        match_type = "MATCH"

    elif "Potential match" in comment:

        match_type = "FUZZY_MATCH"

    else:

        match_type = "NO_MATCH"

    # ---------------------------------------------------
    # SCORE
    # ---------------------------------------------------

    score_match = re.search(
        r"score\s+(\d+)/150",
        comment,
        re.IGNORECASE
    )

    if score_match:

        match_score = score_match.group(1)

    else:

        match_score = ""

    return {
        "match_type": match_type,
        "match_score": match_score
    }