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