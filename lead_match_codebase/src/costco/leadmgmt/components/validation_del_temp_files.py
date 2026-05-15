import pandas as pd
from costco.leadmgmt.config.Configuration import JobConfig
from google.cloud import storage
from sqlalchemy import text
from datetime import datetime, timezone
from costco.leadmgmt.util.apputil import load_file_from_gcs
from costco.leadmgmt.components.update_servicenow import get_gcs_file_path
from datetime import datetime, timezone

# ── Validation constants — must mirror lead_matching.py ──
MINIMUM_SCORE = 80
COMPLETE_SCORE = 100
# adjust "Match"/"Complete" to whatever your matcher produces
HIGH_LABEL = "Match"
POTENTIAL_LABEL = "Potential"



def mark_match_failed(match_id: str, config_file_path: str, error_message: str = ""):
    """Mark a match_audit row as Failed. Idempotent — safe to call multiple times."""
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    engine = db_config.get_engine()

    end_date = datetime.now(timezone.utc)
    
    # Use the existing failed_status_query (from configuration_adt.ini)
    # It does INSERT ... ON CONFLICT (match_id) DO UPDATE — so it handles
    # both "row doesn't exist yet" and "row exists but stuck in InProgress".
    failed_status_query = query_config.failed_status_query
    
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(failed_status_query),
                [{
                    'match_id': match_id,
                    'start_date': end_date,
                    'end_date': end_date,
                    'status': 'Failed',
                    'comments': error_message or "Pipeline stage failed",
                }]
            )
    print(f"⚠️  Marked match_id={match_id} as Failed")
def validate_match_output(final_df, leads_df, pos_df):
    """
    Structural validation of match results. Runs before temp file deletion,
    while leads_temp.csv and pos_temp.csv still exist.

    Returns dict of issues — empty dict means clean.
    """
    issues = {}

    # 1. Duplicate (lead_id, pos_id) pairs
    dups = final_df.duplicated(subset=["lead_id", "pos_id"], keep=False)
    if dups.any():
        issues["duplicate_pairs"] = int(dups.sum())

    # 2. Scores below threshold (filter should have removed these)
    matched = final_df[final_df["match_result"] != "No Match"]
    below = matched[matched["similarity_score"] < MINIMUM_SCORE]
    if not below.empty:
        issues["below_threshold"] = len(below)

    # 3. Classification consistent with score
    def expected_label(score):
        return HIGH_LABEL if score >= COMPLETE_SCORE else POTENTIAL_LABEL
    mismatched = matched[
        matched["match_result"] != matched["similarity_score"].apply(expected_label)
    ]
    if not mismatched.empty:
        issues["wrong_classification"] = len(mismatched)

    # 4. Result IDs must exist in the source files
    lead_ids = set(leads_df["lead_id"].astype(str))
    pos_ids = set(pos_df["pos_id"].astype(str))
    orphan_leads = matched[~matched["lead_id"].astype(str).isin(lead_ids)]
    orphan_pos = matched[~matched["pos_id"].astype(str).isin(pos_ids)]
    if not orphan_leads.empty:
        issues["lead_not_in_source"] = len(orphan_leads)
    if not orphan_pos.empty:
        issues["pos_not_in_source"] = len(orphan_pos)

    # 5. Null in critical columns
    for col in ["lead_id", "pos_id", "similarity_score", "match_result"]:
        if col in final_df.columns:
            n = int(final_df[col].isna().sum())
            if n > 0:
                issues[f"null_{col}"] = n

    # 6. Multiple primary_transaction per lead
    if "primary_transaction" in final_df.columns:
        primaries = final_df[final_df["primary_transaction"] == True]
        multi = primaries.groupby("lead_id").size()
        bad = multi[multi > 1]
        if not bad.empty:
            issues["multiple_primary_per_lead"] = len(bad)

    return issues


def delete_temp_files_from_gcs(match_id: str, config_file_path: str, file_path: str = ""):
    """Validate match results, update audit, then delete temp files."""
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query
    storage_config = job_config.storage_config
    standalone_file_path = storage_config.standalone_file_path

    if file_path == "":
        file_path = get_gcs_file_path(standalone_file_path)

    input_bucket = storage_config.input_bucket_name
    temp_folder = storage_config.temporary_folder
    temp_leads_path = storage_config.temp_leads_path
    temp_pos_path = storage_config.temp_pos_path
    update_match_audit_query = query_config.update_match_audit_query
    engine = db_config.get_engine()

    # ── Load match results ──
    final_df = load_file_from_gcs(file_path)

    # ── VALIDATION — runs while temp files still exist ──
    validation_issues = {}
    try:
        leads_df = load_file_from_gcs(temp_leads_path)
        pos_df = load_file_from_gcs(temp_pos_path)
        validation_issues = validate_match_output(final_df, leads_df, pos_df)

        if validation_issues:
            print(f"⚠️  VALIDATION ISSUES for match_id={match_id}: {validation_issues}")
        else:
            print(f"✅ Validation passed for match_id={match_id}")
    except Exception as e:
        # Validation failure must not break the pipeline — it's a check, not core logic
        print(f"⚠️  Validation step errored (non-fatal): {e}")
        validation_issues = {"validation_error": str(e)}

    # ── Existing audit logic ──
    high_medium_low_df = final_df[final_df['match_result'].isin([HIGH_LABEL, POTENTIAL_LABEL])]
    no_match_df = final_df[final_df['match_result'] == 'No Match']
    no_match_df_unique = no_match_df[~no_match_df['lead_id'].isin(high_medium_low_df['lead_id'])]
    final_df = pd.concat([high_medium_low_df, no_match_df_unique], ignore_index=True)

    match_count = final_df[final_df['match_result'] != 'No Match']['lead_id'].nunique()
    no_match_count = final_df[final_df['match_result'] == 'No Match']['lead_id'].nunique()
    high_match_count = final_df[final_df['match_result'] == HIGH_LABEL]['lead_id'].nunique()
    medium_match_count = final_df[final_df['match_result'] == POTENTIAL_LABEL]['lead_id'].nunique()
    end_date = datetime.now(timezone.utc)

    # Fold validation result into the stats string so it lands in the audit table
    stats = f"Complete: {high_match_count}, Potential: {medium_match_count}"
    if validation_issues:
        stats += f" | VALIDATION: {validation_issues}"

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(update_match_audit_query),
                [{'match_count': match_count, 'no_match_count': no_match_count,
                  'stats': stats, 'status': 'completed',
                  'end_date': end_date, 'match_id': match_id}]
            )

    # ── Delete temp files ──
    # If validation found issues, KEEP the temp files for debugging.
    if validation_issues:
        print(f"⚠️  Keeping temp files for debugging (validation issues present).")
        print(f"    leads: {temp_leads_path}")
        print(f"    pos:   {temp_pos_path}")
        return

    storage_client = storage.Client()
    bucket = storage_client.bucket(input_bucket)
    blobs = bucket.list_blobs(prefix=temp_folder)
    for blob in blobs:
        if not blob.name.endswith('/'):
            print(f"Deleting file: gs://{input_bucket}/{blob.name}")
            blob.delete()
        else:
            print(f"Skipping folder: gs://{input_bucket}/{blob.name}")