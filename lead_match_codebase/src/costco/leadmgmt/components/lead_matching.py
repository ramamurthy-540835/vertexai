import gc
import logging
from datetime import datetime

import numpy as np
import pandas as pd
from google.cloud import storage
from sqlalchemy import MetaData, Table, insert, update

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import (
    load_file_from_gcs,
    process_and_archive_files,
)
# CHANGE 1: import the new streaming function
from costco.leadmgmt.components.streaming_partition import (
    run_streaming_classification,
)

log = logging.getLogger(__name__)


def _friendly(field: str) -> str:
    """Strip the _normalized suffix to recover the original column name."""
    return field.replace("_normalized", "")


# ==============================================================
# SET-BASED MATCHING CONFIGURATION
# ==============================================================
# Six matching sets. Each set defines which POS-side ORIGINAL column
# supplies each of the seven scoring fields. A candidate pair is scored
# AGAINST EVERY SET, and the set with the highest score wins. Ties are
# broken in favor of the lower-numbered set (POS over OMS, primary OMS
# over secondary), via numpy.argmax which returns the first occurrence.
#
# The seven fields, with point values:
#   business : 40   addr  : 40
#   email    : 30   phone : 20
#   zip      : 10   city  :  5   state : 5
# Maximum possible per-set score = 150.

SETS = {
    1: {
        "name":     "POS",
        "business": "business_name",
        "email":    "email",
        "phone":    "phone",
        "addr":     "address_line_one",
        "zip":      "zip_code",
        "city":     "city",
        "state":    "state",
    },
    2: {
        "name":     "POS + OMS (Company)",
        "business": "oms_company",
        "email":    "email",
        "phone":    "phone",
        "addr":     "address_line_one",
        "zip":      "zip_code",
        "city":     "city",
        "state":    "state",
    },
    3: {
        "name":     "OMS Primary (Business)",
        "business": "business_name",
        "email":    "oms_email_1",
        "phone":    "oms_phone_1",
        "addr":     "oms_address_line_1",
        "zip":      "oms_zip",
        "city":     "oms_city",
        "state":    "oms_state",
    },
    4: {
        "name":     "OMS Primary (Company)",
        "business": "oms_company",
        "email":    "oms_email_1",
        "phone":    "oms_phone_1",
        "addr":     "oms_address_line_1",
        "zip":      "oms_zip",
        "city":     "oms_city",
        "state":    "oms_state",
    },
    5: {
        "name":     "OMS Secondary (Business)",
        "business": "business_name",
        "email":    "oms_email_2",
        "phone":    "oms_phone_2",
        "addr":     "oms_address_line_1_v2",
        "zip":      "oms_zip_2",
        "city":     "oms_city_2",
        "state":    "oms_state_2",
    },
    6: {
        "name":     "OMS Secondary (Company)",
        "business": "oms_company_2",
        "email":    "oms_email_2",
        "phone":    "oms_phone_2",
        "addr":     "oms_address_line_1_v2",
        "zip":      "oms_zip_2",
        "city":     "oms_city_2",
        "state":    "oms_state_2",
    },
}

# Field point values. Order is preserved when reporting matched fields.
FIELD_SCORES = {
    "business": 40,
    "addr":     40,
    "email":    30,
    "phone":    20,
    "zip":      10,
    "city":      5,
    "state":     5,
}

# Lead-side normalized column for each logical field.
LEAD_COL = {
    "business": "business_name_normalized",
    "email":    "email_normalized",
    "phone":    "phone_normalized",
    "addr":     "address_line_one_normalized",
    "zip":      "zip_code_normalized",
    "city":     "city_normalized",
    "state":    "state_normalized",
}

# Friendly display names for the matching comment.
FIELD_DISPLAY = {
    "business": "business_name",
    "email":    "email",
    "phone":    "phone",
    "addr":     "address_line_one",
    "zip":      "zip_code",
    "city":     "city",
    "state":    "state",
}

# Output column ← set-field-key. Used for the final substitution step:
# for each row we look up the winning set, then pull the value from
# that set's source column for each output field.
OUTPUT_FIELD_TO_SET_KEY = {
    "business_name_transaction": "business",
    "email":                     "email",
    "phone":                     "phone",
    "address_line_one":          "addr",
    "zip_code":                  "zip",
    "city":                      "city",
    "state":                     "state",
}

LEAD_NORMALIZE_COLS = list(LEAD_COL.values())

# Every POS-side normalized column referenced by any set, deduplicated.
POS_NORMALIZE_COLS = list({
    f"{mapping[field]}_normalized"
    for mapping in SETS.values()
    for field in FIELD_SCORES
})

# Every POS-side ORIGINAL column referenced by any set, deduplicated.
# Used to build sales_subset for the final merge so output substitution
# can read from the winning set's source columns.
ALL_SET_ORIGINAL_COLS = list({
    mapping[field]
    for mapping in SETS.values()
    for field in FIELD_SCORES
})

# Blocking keys for candidate generation. A (lead, POS) pair becomes a
# candidate when they share a warehouse AND agree on the value of one
# of these lead/POS-variant pairs. We block on the fields whose point
# values are large enough to potentially push a pair to >= MINIMUM_SCORE
# in combination with other hits — i.e. business, email, phone, addr.
# zip/city/state alone (max 20 across all three) cannot reach 70, so
# they are not blocking keys.
_blocking = {}
for _mapping in SETS.values():
    for _field in ("business", "email", "phone", "addr"):
        _blocking.setdefault(LEAD_COL[_field], set()).add(
            f"{_mapping[_field]}_normalized"
        )
BLOCKING_KEYS = [
    (lead_col, sorted(pos_vars))
    for lead_col, pos_vars in _blocking.items()
]

MINIMUM_SCORE      = 70
COMPLETE_SCORE     = 100
MAX_POSSIBLE_SCORE = sum(FIELD_SCORES.values())  # 150

# Fiscal calendar / Closed-Existing window.
# A POS transaction is classified Closed-Existing only when it is
# CHRONOLOGICALLY PRIOR to the lead AND the gap is at most
# CE_PERIOD_WINDOW periods. Older priors (gap > CE_PERIOD_WINDOW) are
# treated like post-lead transactions — they flow through the normal
# six-set scoring with no special flag. Costco uses a 13-period fiscal
# year; "exactly 6 periods" falls into CE per spec.
PERIODS_PER_YEAR = 13
CE_PERIOD_WINDOW = 6


# ==============================================================
# MATCHING COMMENT BUILDER
# ==============================================================
def build_matching_comment(row: pd.Series) -> str:
    """
    Human-readable explanation of which fields hit and which POS column
    each matched against. The set name itself is intentionally NOT in
    the comment — the column mapping makes it explicit anyway (and the
    winning_set column carries the structured value separately).

    Example:
        "Complete Match (score 150/150). Fields matched:
         business_name → business_name, email → oms_email_1,
         phone → oms_phone_1, address_line_one → oms_address_line_1,
         zip_code → oms_zip, city → oms_city, state → oms_state.
         Designated as primary transaction (earliest fiscal period
         for this lead)."
    """
    score          = row["similarity_score"]
    result         = row["match_result"]
    winning_set    = row.get("winning_set")
    matched_fields = row.get("matched_fields", []) or []

    parts = []
    if result == "Match":
        parts.append(f"Complete Match (score {score}/{MAX_POSSIBLE_SCORE}).")
    else:
        parts.append(
            f"Potential match (score {score}/{MAX_POSSIBLE_SCORE}); "
            "Marketer review recommended."
        )

    if matched_fields:
        # Render "logical_name -> pos_column" in FIELD_SCORES order so
        # the comment is stable across rows. The arrow is ALWAYS shown,
        # even for pure-POS matches where logical and POS column names
        # are identical (e.g. "email -> email"), so the format stays
        # uniform for downstream parsers/UIs.
        mapping = SETS[winning_set] if winning_set in SETS else {}
        ordered = []
        for f in FIELD_SCORES:
            if f not in matched_fields:
                continue
            logical = FIELD_DISPLAY[f]
            pos_col = mapping.get(f, logical)
            ordered.append(f"{logical} -> {pos_col}")
        parts.append(f"Fields matched: {', '.join(ordered)}.")
    else:
        parts.append("No fields matched exactly.")

    if row.get("primary_transaction"):
        parts.append(
            "Designated as primary transaction "
            "(earliest fiscal period for this lead)."
        )

    return " ".join(parts)


# ==============================================================
# PREPROCESS
# ==============================================================
def _normalize_col(s: pd.Series) -> pd.Series:
    """Strip + lowercase + NaN-out empty/'nan'/'<NA>'."""
    if pd.api.types.is_float_dtype(s):
        s = pd.to_numeric(s, errors="coerce").astype("Int64").astype(str)
    return (
        s.astype(str).str.strip().str.lower()
        .replace({"nan": pd.NA, "<na>": pd.NA, "": pd.NA, "none": pd.NA})
    )


def preprocess_leads(df: pd.DataFrame) -> pd.DataFrame:
    """Lead-side preprocessing — only the 7 primary matching cols."""
    df = df.copy()
    df = df.dropna(subset=["warehouse_number"])
    df["warehouse_number"] = (
        pd.to_numeric(df["warehouse_number"], errors="coerce")
        .astype("Int64").astype(str)
    )
    df = df[df["warehouse_number"] != ""]

    for col in LEAD_NORMALIZE_COLS:
        if col not in df.columns:
            original = _friendly(col)
            if original in df.columns:
                df[col] = _normalize_col(df[original])
            else:
                df[col] = pd.NA
        else:
            df[col] = _normalize_col(df[col])
    return df


def preprocess_sales(df: pd.DataFrame) -> pd.DataFrame:
    """POS-side preprocessing — every column referenced by any set."""
    df = df.copy()
    df = df.dropna(subset=["warehouse_number"])
    df["warehouse_number"] = (
        pd.to_numeric(df["warehouse_number"], errors="coerce")
        .astype("Int64").astype(str)
    )
    df = df[df["warehouse_number"] != ""]

    for col in POS_NORMALIZE_COLS:
        if col not in df.columns:
            original = _friendly(col)
            if original in df.columns:
                df[col] = _normalize_col(df[original])
            else:
                # OMS columns may legitimately not exist on this run
                df[col] = pd.NA
        else:
            df[col] = _normalize_col(df[col])
    return df


# ==============================================================
# BIDIRECTIONAL CONTAINS — business-name field only
# ==============================================================
def _bidir_contains(lead_vals: pd.Series, pos_vals: pd.Series) -> np.ndarray:
    """
    Bidirectional substring match for two aligned Series:
    a hit is when the lead value is contained in the POS value
    OR the POS value is contained in the lead value.

    Both sides must be non-null, non-empty strings. The empty-string
    guard is essential: "" is a substring of every string, so without
    it every pair with one blank side would register a spurious hit.
    Values are already normalized (strip + lowercase) upstream by
    _normalize_col, so this is a plain casefold-free substring test.

    Returns a boolean ndarray aligned to the input index.
    """
    both = (lead_vals.notna() & pos_vals.notna()).to_numpy()
    lv = lead_vals.to_numpy(dtype=object)
    pv = pos_vals.to_numpy(dtype=object)
    out = np.zeros(len(lead_vals), dtype=bool)
    for i in range(len(lead_vals)):
        if not both[i]:
            continue
        a, b = lv[i], pv[i]
        if not isinstance(a, str):
            a = str(a)
        if not isinstance(b, str):
            b = str(b)
        if a == "" or b == "":
            continue
        out[i] = (a in b) or (b in a)
    return out


# ==============================================================
# SCORE A PAIR TABLE — per-set scoring, pick max
# ==============================================================
def _score_pairs(
    pairs: pd.DataFrame,
    pos_primary_overlap: set,
) -> pd.DataFrame:
    """
    Score an already-built candidate-pair table against all six sets.

    `pairs` must contain lead_id, pos_id, the lead columns suffixed
    __l, and the POS columns (overlapping primaries suffixed __p, OMS
    columns unsuffixed). For each pair we:
      1. Compute a per-set score by summing the points for each field
         that matches between the lead and the set's POS column.
      2. Pick the set with the highest score (ties → lowest set #).
      3. Record which fields hit in the winning set.

    Returns DataFrame[lead_id, pos_id, similarity_score, winning_set,
    matched_fields] (NOT yet thresholded — the caller filters by
    MINIMUM_SCORE).
    """
    n = len(pairs)
    empty_cols = [
        "lead_id", "pos_id", "similarity_score",
        "winning_set", "matched_fields",
    ]
    if n == 0:
        return pd.DataFrame(columns=empty_cols)

    def _pos_col(name):
        """Resolve a normalized-column name to its post-rename form."""
        return f"{name}__p" if name in pos_primary_overlap else name

    set_ids   = sorted(SETS.keys())  # [1, 2, 3, 4, 5, 6]
    n_sets    = len(set_ids)
    set_index = {sid: i for i, sid in enumerate(set_ids)}

    # Per-set score matrix [n_pairs, n_sets].
    set_scores = np.zeros((n, n_sets), dtype=np.int64)

    # Per-(set, field) hit masks, kept around so we can recover the
    # winning set's matched-field list after we pick the max.
    field_hits = {}  # (set_id, field) -> bool ndarray of length n

    for set_id in set_ids:
        mapping = SETS[set_id]
        col_idx = set_index[set_id]
        for field, points in FIELD_SCORES.items():
            lead_col = f"{LEAD_COL[field]}__l"
            if lead_col not in pairs.columns:
                continue
            pos_norm = f"{mapping[field]}_normalized"
            pos_col  = _pos_col(pos_norm)
            if pos_col not in pairs.columns:
                continue

            lead_vals = pairs[lead_col]
            pos_vals  = pairs[pos_col]
            if field == "business":
                # Bidirectional substring match for business name only:
                # lead-name contained in POS-name OR POS-name contained
                # in lead-name. Replaces the previous exact-equality test
                # for this field; every other field stays exact-equal.
                hit = _bidir_contains(lead_vals, pos_vals)
            else:
                hit = (
                    lead_vals.notna()
                    & pos_vals.notna()
                    & (lead_vals == pos_vals)
                ).to_numpy()

            if hit.any():
                set_scores[:, col_idx] += hit.astype(np.int64) * points
                field_hits[(set_id, field)] = hit
            else:
                field_hits[(set_id, field)] = hit  # all-False, still record

    # Pick winning set per pair. argmax returns the FIRST max-index, so
    # ties go to the lower set number (POS over OMS, primary over
    # secondary), which matches the documented intent.
    winning_col = set_scores.argmax(axis=1)
    winning_set = np.array([set_ids[i] for i in winning_col], dtype=np.int64)
    winning_score = set_scores[np.arange(n), winning_col]

    # Build matched_fields list for the winning set per row.
    matched_fields = [[] for _ in range(n)]
    for set_id in set_ids:
        in_this_set = (winning_set == set_id)
        if not in_this_set.any():
            continue
        for field in FIELD_SCORES:
            hits = field_hits.get((set_id, field))
            if hits is None:
                continue
            rows = np.where(in_this_set & hits)[0]
            for idx in rows:
                matched_fields[idx].append(field)

    return pd.DataFrame({
        "lead_id":          pairs["lead_id"].values,
        "pos_id":           pairs["pos_id"].values,
        "similarity_score": winning_score,
        "winning_set":      winning_set,
        "matched_fields":   matched_fields,
    })


# ==============================================================
# CANDIDATE GENERATION — blocking via equi-joins
# ==============================================================
def _generate_candidates(
    leads_small: pd.DataFrame,
    sales_small: pd.DataFrame,
    pos_primary_overlap: set,
) -> pd.DataFrame:
    """
    Build the deduplicated candidate (lead_id, pos_id) set by
    equi-joining on (warehouse_number, value) for every blocking
    key/variant.

    A pair is emitted only when the lead and POS row share a warehouse
    AND agree on the value of some blocking field — never a cartesian
    product. Column names are already suffixed (__l on leads, __p on
    overlapping POS primaries; OMS cols unsuffixed).

    Returns DataFrame[lead_id, pos_id].
    """
    def _pos_col(name):
        return f"{name}__p" if name in pos_primary_overlap else name

    candidate_frames = []
    for lead_col, pos_variants in BLOCKING_KEYS:
        lcol = f"{lead_col}__l"
        if lcol not in leads_small.columns:
            continue

        # Lead side: only rows where the blocking value is present.
        lblock = (
            leads_small[["lead_id", "warehouse_number", lcol]]
            .dropna(subset=[lcol])
            .rename(columns={lcol: "_blockval"})
        )
        if lblock.empty:
            continue

        for variant in pos_variants:
            pcol = _pos_col(variant)
            if pcol not in sales_small.columns:
                continue

            pblock = (
                sales_small[["pos_id", "warehouse_number", pcol]]
                .dropna(subset=[pcol])
                .rename(columns={pcol: "_blockval"})
            )
            if pblock.empty:
                continue

            # Equi-join on warehouse + shared blocking value.
            merged = lblock.merge(
                pblock, on=["warehouse_number", "_blockval"], how="inner"
            )[["lead_id", "pos_id"]]

            if not merged.empty:
                candidate_frames.append(merged)

            del pblock, merged

    if not candidate_frames:
        return pd.DataFrame(columns=["lead_id", "pos_id"])

    return (
        pd.concat(candidate_frames, ignore_index=True)
        .drop_duplicates(subset=["lead_id", "pos_id"])
    )


# ==============================================================
# SCORE ONE GROUP — blocking candidate generation + scoring
# ==============================================================
def _score_group(
    sales_slice: pd.DataFrame,
    active_leads: pd.DataFrame,
) -> pd.DataFrame:
    """
    Score every qualifying (lead, POS) pair from this chronological POS
    group against the currently active leads.

    Strategy — blocking, not cartesian:
      1. Generate candidate pairs by equi-joining on (warehouse, value)
         for each blocking key/variant.
      2. Hydrate those candidate pairs with the columns needed for
         scoring.
      3. Run _score_pairs (set-based scoring, pick highest set) and
         threshold at MINIMUM_SCORE.

    Returns:
        DataFrame[lead_id, pos_id, similarity_score, winning_set,
                  matched_fields]
        filtered to similarity_score >= MINIMUM_SCORE.
    """
    empty = pd.DataFrame(columns=[
        "lead_id", "pos_id", "similarity_score",
        "winning_set", "matched_fields",
    ])
    if active_leads.empty or sales_slice.empty:
        return empty

    # Suffix lead and overlapping POS columns so they don't collide
    # when the two sides share a name (e.g. business_name_normalized).
    pos_primary_overlap = set(LEAD_NORMALIZE_COLS) & set(POS_NORMALIZE_COLS)
    lead_rename = {c: f"{c}__l" for c in LEAD_NORMALIZE_COLS}
    pos_rename  = {c: f"{c}__p" for c in pos_primary_overlap}

    leads_small = (
        active_leads[["lead_id", "warehouse_number"] + LEAD_NORMALIZE_COLS]
        .rename(columns=lead_rename)
    )
    sales_small = (
        sales_slice[["pos_id", "warehouse_number"] + POS_NORMALIZE_COLS]
        .rename(columns=pos_rename)
    )

    # 1) Candidate pairs via blocking equi-joins (exact agreement).
    candidates = _generate_candidates(leads_small, sales_small, pos_primary_overlap)
    if candidates.empty:
        return empty

    # 2) Hydrate candidate pairs with the columns needed for scoring.
    #    Drop warehouse_number from both sides before the join — it has
    #    served its purpose as a blocking key.
    pairs = (
        candidates
        .merge(leads_small.drop(columns=["warehouse_number"]), on="lead_id", how="left")
        .merge(sales_small.drop(columns=["warehouse_number"]), on="pos_id", how="left")
    )
    del candidates

    # 3) Identical scoring kernel + threshold.
    scored = _score_pairs(pairs, pos_primary_overlap)
    del pairs

    scored = scored[scored["similarity_score"] >= MINIMUM_SCORE]
    return scored.drop_duplicates(subset=["lead_id", "pos_id"]).copy()


# ==============================================================
# OUTPUT COLUMNS
# ==============================================================
def _output_columns() -> list:
    return [
        # Matching / classification
        "lead_id",
        "pos_id",
        "match_result",
        "similarity_score",
        "winning_set",
        "match_type",
        "primary_transaction",
        "matched_by",
        "matching_comments",
        "closed_existing_flag",

        # POS dominant
        "account_number",
        "transaction_count",
        "business_name_transaction",
        "membership_number",
        "warehouse_number",
        "sales_reference_id",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "shop_type",
        "bd_industry",
        "order_amount",
        "industry_description",

        # POS customer details (originals — for ServiceNow payload)
        "first_name",
        "last_name",
        "address_line_one",
        "address_line_two",
        "city",
        "state",
        "zip_code",
        "email",
        "phone",

        # ServiceNow
        "u_matched_lead_number",
        "u_order_amount",
        "u_order_amount_rounded",
        "updated_date",
    ]


# ==============================================================
# WAREHOUSE BATCHING (memory bounding for the match run)
# ==============================================================
# NOTE: run_batched_classification below is now SUPERSEDED by
# run_streaming_classification (in streaming_partition.py), which avoids
# loading the full 20M-row sales file into memory at all. It is kept here
# unused for reference / rollback; primary_classification no longer calls
# it. Safe to delete once the streaming path is confirmed in PRD.
WAREHOUSE_BATCH_ROW_BUDGET = 150_000


def _norm_wh_series(s: pd.Series) -> pd.Series:
    """Normalize warehouse the same way preprocess_* does, for batching."""
    return pd.to_numeric(s, errors="coerce").astype("Int64").astype("string")


def _plan_warehouse_batches(wh_row_counts: dict, budget: int) -> list:
    """
    Group whole warehouses into batches whose summed sales-row count stays
    under `budget`. A warehouse exceeding the budget alone is its own
    batch (never split). Returns list[list[warehouse]].
    """
    batches = []
    cur, cur_rows = [], 0
    for wh, cnt in sorted(wh_row_counts.items(), key=lambda x: -x[1]):
        if cnt >= budget:
            batches.append([wh])
            continue
        if cur and cur_rows + cnt > budget:
            batches.append(cur)
            cur, cur_rows = [], 0
        cur.append(wh)
        cur_rows += cnt
    if cur:
        batches.append(cur)
    return batches


def run_batched_classification(
    file_leads: pd.DataFrame,
    file_sales: pd.DataFrame,
    budget: int = WAREHOUSE_BATCH_ROW_BUDGET,
):
    """
    SUPERSEDED — see run_streaming_classification in streaming_partition.py.

    Run classify_matches over whole-warehouse batches to bound memory.

    Returns (final_df, processed_pos_ids):
      • final_df: concatenation of each batch's classify_matches output.
      • processed_pos_ids: every POS id SCANNED this run — i.e. every
        transaction whose warehouse had >=1 lead, match or not.
    """
    fa = file_leads.copy()
    fb = file_sales.copy()
    fa["_wh"] = _norm_wh_series(fa["warehouse_number"])
    fb["_wh"] = _norm_wh_series(fb["warehouse_number"])

    lead_whs = {w for w in fa["_wh"].dropna().unique() if w not in (None, "<NA>")}

    scanned = fb[fb["_wh"].isin(lead_whs)]
    processed_pos_ids = scanned["pos_id"].astype(str).tolist()

    wh_counts = scanned["_wh"].value_counts().to_dict()
    batches = _plan_warehouse_batches(wh_counts, budget)
    log.info(
        "Warehouse batching: %d scanned warehouse(s), %d transaction(s) → %d batch(es)",
        len(wh_counts), len(processed_pos_ids), len(batches),
    )

    out_cols = _output_columns()
    results = []
    for bi, batch_whs in enumerate(batches, start=1):
        bw = set(batch_whs)
        la = fa[fa["_wh"].isin(bw)].drop(columns=["_wh"])
        sa = fb[fb["_wh"].isin(bw)].drop(columns=["_wh"])
        if la.empty or sa.empty:
            continue
        log.info("Batch %d/%d — %d warehouse(s), %d POS, %d leads",
                 bi, len(batches), len(bw), len(sa), len(la))
        res = classify_matches(la, sa)
        if not res.empty:
            results.append(res)
        del la, sa, res
        gc.collect()

    final_df = (
        pd.concat(results, ignore_index=True) if results
        else pd.DataFrame(columns=out_cols)
    )
    return final_df, processed_pos_ids


def _write_processed_manifest(storage_config, processed_pos_ids: list) -> str:
    """
    Write the scanned-this-run POS ids to a manifest CSV in
    temporary_folder. update_cloud_sql reads this and flips
    is_processed=true on those transactions after the match write
    commits, excluding them from future cycles.
    """
    bucket_name = storage_config.output_bucket_name
    folder      = storage_config.temporary_folder
    object_path = f"{folder}/processed_pos_ids.csv"

    client = storage.Client()
    bucket = client.get_bucket(bucket_name)
    blob   = bucket.blob(object_path)

    ids = pd.Series(pd.unique(pd.Series(processed_pos_ids, dtype="object")))
    csv_text = "pos_id\n" + "\n".join(ids.astype(str).tolist()) + "\n"
    blob.upload_from_string(csv_text, content_type="text/csv")

    uri = f"gs://{bucket_name}/{object_path}"
    log.info("Wrote processed-pos manifest: %s (%d ids)", uri, len(ids))
    return uri


# ==============================================================
# CLASSIFY MATCHES
# ==============================================================
def classify_matches(
    file_leads: pd.DataFrame,
    file_sales: pd.DataFrame,
) -> pd.DataFrame:
    """
    Chronological matching with windowed Closed-Existing detection and
    6-set scoring (the set with the highest score wins, ties → lower set #).

    For each (fiscal_year, fiscal_period, week) POS group in
    chronological order, every qualified pair (score ≥ MINIMUM_SCORE)
    falls into one of three buckets:

      • POS at or after lead creation
            → normal Match / Potential output

      • POS prior, gap > CE_PERIOD_WINDOW periods
            → IGNORED (not a match, not CE).
            The lead pre-dated the POS, so it isn't a real "match",
            but the gap is too large for CE either. The lead stays
            active and the search continues to subsequent POS groups.

      • POS prior, gap <= CE_PERIOD_WINDOW periods
            → Closed-Existing: lead is removed from the active set,
              emit a stub row, and skip forever.

    A "match" requires the lead to have been created BEFORE the
    transaction. Prior-but-not-CE transactions are noise — they
    neither halt the search nor produce output.
    """
    leads = preprocess_leads(file_leads)
    sales = preprocess_sales(file_sales)

    # Warehouse pre-filter
    lead_warehouses = leads["warehouse_number"].dropna().unique()
    sales_before = len(sales)
    sales = sales[sales["warehouse_number"].isin(lead_warehouses)].copy()
    log.info(
        "Warehouse pre-filter: %d → %d POS rows (%d dropped)",
        sales_before, len(sales), sales_before - len(sales),
    )

    if sales.empty:
        log.warning("No POS rows share a warehouse with any lead.")
        return pd.DataFrame(columns=_output_columns())

    # Lead fiscal lookup — used for CE check (year, period, AND week).
    _lead_fiscal_cols = ["lead_id", "fiscal_year_lead", "fiscal_period_lead"]
    if "week" in leads.columns:
        lead_fiscal = (
            leads[_lead_fiscal_cols + ["week"]]
            .rename(columns={"week": "week_lead"})
            .drop_duplicates(subset=["lead_id"])
            .set_index("lead_id")
        )
    else:
        log.warning(
            "Leads file has no 'week' column — week-level CE check disabled "
            "(falling back to year/period only)."
        )
        lead_fiscal = (
            leads[_lead_fiscal_cols]
            .drop_duplicates(subset=["lead_id"])
            .set_index("lead_id")
        )
        lead_fiscal["week_lead"] = pd.NA

    # Sort POS into chronological groups
    sales_sorted = sales.sort_values(
        by=["fiscal_year_transaction", "fiscal_period_transaction", "week"],
        ascending=True,
    )
    groups = sales_sorted.groupby(
        ["fiscal_year_transaction", "fiscal_period_transaction", "week"],
        sort=False,
    )
    group_keys = list(groups.groups.keys())
    log.info("Processing %d chronological POS groups", len(group_keys))

    # State across groups
    ce_lead_ids        = set()
    normal_pair_frames = []
    active_lead_ids    = set(leads["lead_id"].unique())

    for gkey in group_keys:
        fy, fp, wk = gkey
        if not active_lead_ids:
            log.info(
                "All leads resolved — stopping early at group %s/%s/wk%s",
                fy, fp, wk,
            )
            break

        sales_slice  = groups.get_group(gkey)
        active_leads = leads[leads["lead_id"].isin(active_lead_ids)].copy()

        qualified = _score_group(sales_slice, active_leads)
        if qualified.empty:
            del active_leads, sales_slice
            continue

        # Attach fiscal columns for CE check
        qualified["fiscal_year_transaction"]   = fy
        qualified["fiscal_period_transaction"] = fp
        qualified["week"]                      = wk
        qualified = qualified.join(lead_fiscal, on="lead_id", how="left")

        # Coerce comparison columns to nullable Int so < / == are numeric.
        for _col in (
            "fiscal_year_transaction", "fiscal_period_transaction", "week",
            "fiscal_year_lead", "fiscal_period_lead", "week_lead",
        ):
            qualified[_col] = pd.to_numeric(
                qualified[_col], errors="coerce"
            ).astype("Int64")

        # Chronological "prior" check at (year, period, week) granularity.
        # This identifies pairs where the POS is strictly older than the lead;
        # whether they're CE depends ALSO on the period-gap check below.
        prior_mask = (
            (qualified["fiscal_year_transaction"] < qualified["fiscal_year_lead"])
            | (
                (qualified["fiscal_year_transaction"] == qualified["fiscal_year_lead"])
                & (qualified["fiscal_period_transaction"] < qualified["fiscal_period_lead"])
            )
            | (
                (qualified["fiscal_year_transaction"] == qualified["fiscal_year_lead"])
                & (qualified["fiscal_period_transaction"] == qualified["fiscal_period_lead"])
                & (qualified["week"] < qualified["week_lead"])
            )
        )
        prior_mask = prior_mask.fillna(False).astype(bool)

        # Period gap (lead - pos), measured in fiscal periods. Used to
        # decide whether a prior POS is "close enough" to be Closed-Existing
        # or "old enough" to be treated as a normal historical match.
        # Same period earlier-week → gap 0 → still within window → CE.
        # Week is intentionally NOT in the gap calc; only the prior_mask
        # uses week. NA-safe via fillna(False).
        period_gap = (
            (qualified["fiscal_year_lead"]
             - qualified["fiscal_year_transaction"]) * PERIODS_PER_YEAR
            + (qualified["fiscal_period_lead"]
               - qualified["fiscal_period_transaction"])
        )
        within_ce_window = (
            (period_gap <= CE_PERIOD_WINDOW).fillna(False).astype(bool)
        )

        # Three-way classification per pair:
        #   prior AND gap <= CE_PERIOD_WINDOW → Closed-Existing
        #       (lead marked CE, removed from active set, stub row)
        #   prior AND gap >  CE_PERIOD_WINDOW → IGNORED
        #       (not a match output, not CE; the lead stays active and
        #        the chronological search continues to other POS groups)
        #   not prior                           → Match candidate
        #       (POS is at or after lead creation → goes to scoring output)
        ce_mask = prior_mask & within_ce_window

        new_ce = set(qualified.loc[ce_mask, "lead_id"].unique())
        if new_ce:
            ce_lead_ids.update(new_ce)
            active_lead_ids -= new_ce
            log.info(
                "Group %s/%s/wk%s — %d new CE lead(s) (within %d periods): %s",
                fy, fp, wk, len(new_ce), CE_PERIOD_WINDOW, list(new_ce)[:10],
            )

        # Visibility: prior pairs older than the CE window are silently
        # dropped — they don't become matches (the lead pre-dated the POS,
        # so they're not a real "match"), but they also don't mark the
        # lead CE. The lead remains active and may still match against
        # POS transactions that occur AFTER its creation.
        old_prior_count = int((prior_mask & ~within_ce_window).sum())
        if old_prior_count:
            log.info(
                "Group %s/%s/wk%s — %d prior pair(s) older than %d periods "
                "ignored (no match, no CE; lead stays active)",
                fy, fp, wk, old_prior_count, CE_PERIOD_WINDOW,
            )

        # Only POST-LEAD pairs become matches. Both close-prior (already
        # flagged CE above) and old-prior pairs are excluded from the
        # normal output by ~prior_mask. A "match" requires the lead to
        # have been created before the transaction.
        surviving = qualified[~prior_mask].copy()
        if not surviving.empty:
            normal_pair_frames.append(
                surviving[[
                    "lead_id", "pos_id", "similarity_score",
                    "winning_set", "matched_fields",
                ]].copy()
            )

        del qualified, surviving, active_leads, sales_slice
        gc.collect()

    log.info(
        "Chronological pass complete — CE leads: %d | normal pair batches: %d",
        len(ce_lead_ids), len(normal_pair_frames),
    )

    # ==========================================================
    # BUILD CE STUB ROWS
    # ==========================================================
    ce_stubs = (
        pd.DataFrame({"lead_id": list(ce_lead_ids), "closed_existing_flag": True})
        if ce_lead_ids
        else pd.DataFrame(columns=["lead_id", "closed_existing_flag"])
    )

    out_cols = _output_columns()

    if not normal_pair_frames:
        log.warning("No normal pairs to process.")
        final_df = ce_stubs.reindex(columns=out_cols)
        log.info("Final output — CE stubs only: %d", len(final_df))
        return final_df

    normal_qualified = (
        pd.concat(normal_pair_frames, ignore_index=True)
        .drop_duplicates(subset=["lead_id", "pos_id"])
    )
    log.info("Normal pairs carried forward: %d", len(normal_qualified))

    # Quick visibility into winning-set distribution (helps diagnose
    # which OMS variants are pulling weight in production).
    try:
        log.info(
            "Winning-set distribution: %s",
            normal_qualified["winning_set"].value_counts().sort_index().to_dict(),
        )
    except Exception:
        pass

    # ==========================================================
    # FINAL MERGE — pull originals needed by every set, plus lead
    # fiscal / updated_date.
    # ==========================================================
    lead_subset = leads[[
        "lead_id",
        "updated_date",
        "fiscal_year_lead",
        "fiscal_period_lead",
    ]]

    # Base (non-substitutable) POS columns always carried to output.
    _sales_base_cols = [
        "pos_id",
        "account_number",
        "transaction_count",
        "membership_number",
        "warehouse_number",
        "fiscal_year_transaction",
        "fiscal_period_transaction",
        "week",
        "shop_type",
        "sales_reference_id",
        "order_amount",
        "bd_industry",
        "first_name",
        "last_name",
        "address_line_two",
        "industry_description",
    ]

    # Every original column referenced by any set, so substitution
    # can read from the winning set's source column.
    _sales_variant_cols = list(ALL_SET_ORIGINAL_COLS)

    # Guard: ensure every column we intend to select exists on `sales`.
    _needed = list(dict.fromkeys(_sales_base_cols + _sales_variant_cols))
    for _c in _needed:
        if _c not in sales.columns:
            sales[_c] = pd.NA

    sales_subset = sales[_needed].copy()

    matched_df = (
        normal_qualified
        .merge(lead_subset,  on="lead_id", how="inner")
        .merge(sales_subset, on="pos_id",  how="inner")
    )

    # ==========================================================
    # OUTPUT SUBSTITUTION — pull values from the winning set's columns
    # ==========================================================
    # For each output field, build a per-row "source column" by mapping
    # winning_set → SETS[winning_set][set_key]. Then gather the value
    # from that source column. We read ALL source values into a dict
    # first, THEN assign, so overwrites of columns like email/phone/
    # zip_code (which appear both as source AND destination) don't
    # corrupt later reads.
    def _gather_by_name(df, name_series, candidate_cols):
        result = pd.Series(pd.NA, index=df.index, dtype=object)
        names = name_series.to_numpy()
        for col in candidate_cols:
            if col not in df.columns:
                continue
            mask = (names == col)
            if mask.any():
                result.loc[mask] = df[col].to_numpy()[mask]
        return result

    substituted = {}
    for out_field, set_key in OUTPUT_FIELD_TO_SET_KEY.items():
        src_per_row = matched_df["winning_set"].map(
            lambda s, k=set_key: SETS[s][k] if s in SETS else None
        )
        candidate_cols = list({SETS[s][set_key] for s in SETS})
        substituted[out_field] = _gather_by_name(
            matched_df, src_per_row, candidate_cols
        )

    for out_field, vals in substituted.items():
        matched_df[out_field] = vals

    # ==========================================================
    # ASSIGN MATCH RESULT
    # ==========================================================
    matched_df["match_result"] = matched_df["similarity_score"].apply(
        lambda x: "Match" if x >= COMPLETE_SCORE else "Potential"
    )
    matched_df["match_type"]           = "Exact"
    matched_df["matched_by"]           = "System"
    matched_df["primary_transaction"]  = False
    matched_df["closed_existing_flag"] = False

    # ==========================================================
    # PRIMARY TRANSACTION LOGIC
    # ==========================================================
    match_only = matched_df[matched_df["match_result"] == "Match"].copy()
    match_only = match_only.sort_values(
        by=["lead_id", "fiscal_year_transaction",
            "fiscal_period_transaction", "week"],
        ascending=True,
    )
    match_only["rank"] = match_only.groupby("lead_id").cumcount() + 1
    primary_idx = match_only[match_only["rank"] == 1].index
    matched_df.loc[primary_idx, "primary_transaction"] = True

    # ==========================================================
    # MATCHING COMMENTS
    # ==========================================================
    matched_df["matching_comments"] = matched_df.apply(
        build_matching_comment, axis=1
    )

    # ==========================================================
    # SERVICENOW MAPPINGS
    # ==========================================================
    matched_df["u_matched_lead_number"]  = matched_df["lead_id"]
    matched_df["u_order_amount"]         = matched_df["order_amount"]
    matched_df["u_order_amount_rounded"] = (
        pd.to_numeric(matched_df["order_amount"], errors="coerce").round(2)
    )
    matched_df["updated_date"] = pd.to_datetime(datetime.now())

    # ==========================================================
    # ASSEMBLE FINAL OUTPUT
    # ==========================================================
    for col in out_cols:
        if col not in matched_df.columns:
            matched_df[col] = None
    matched_df = matched_df[out_cols].copy()

    ce_stubs = ce_stubs.reindex(columns=out_cols)

    final_df = pd.concat(
        [f for f in [matched_df, ce_stubs] if not f.empty],
        ignore_index=True,
    )

    final_df = (
        final_df
        .sort_values(
            by=["similarity_score", "primary_transaction", "match_result"],
            ascending=[False, False, True],
            na_position="last",
        )
        .drop_duplicates(subset=["lead_id", "pos_id"], keep="first")
    )

    log.info(
        "Final output — normal rows: %d | CE stubs: %d | total: %d",
        (~final_df["closed_existing_flag"].fillna(False)).sum(),
        final_df["closed_existing_flag"].fillna(False).sum(),
        len(final_df),
    )

    del matched_df, ce_stubs
    gc.collect()
    return final_df


# ==============================================================
# PRIMARY CLASSIFICATION (orchestrator)
# ==============================================================
def primary_classification(
    match_id: str,
    config_file_path: str,
    file_a_path: str = "",
    file_b_path: str = "",
) -> str:
    """
    Orchestrates the end-to-end leads-to-POS matching pipeline for a
    single match run. Loads config + the (small) leads file, writes an
    InProgress audit row, then STREAMS the (large) POS file one warehouse
    at a time via run_streaming_classification, and archives the output
    to GCS.

    NOTE: the POS/sales file is NEVER loaded fully into memory. It is
    streamed and SPILLED per warehouse to a temp GCS prefix (one chunk in
    RAM at a time), then processed one warehouse at a time (one warehouse
    in RAM at a time). Spill-to-GCS rather than in-memory buffering is
    required because every transaction belongs to a lead-bearing warehouse,
    so buffering all warehouses would equal buffering the whole 20M-row
    file. The audit row's pos_count is backfilled with the true total row
    count after the spill pass (counted in that single pass — no second
    read of the file).
    """
    job_config     = JobConfig(config_file_path)
    storage_config = job_config.storage_config
    db_config      = job_config.db_config

    if file_a_path == "":
        file_a_path = storage_config.temp_leads_path
    if file_b_path == "":
        file_b_path = storage_config.temp_pos_path

    source_bucket_name      = storage_config.source_bucket_name
    source_folder           = storage_config.source_folder_output
    destination_bucket_name = storage_config.destination_bucket_name
    destination_folder      = storage_config.destination_folder_output

    schema     = db_config.schema_name
    table_name = db_config.audit_table_name
    engine     = db_config.get_engine()
    metadata   = MetaData()

    STRING_COLS = {
        "zip_code":            str,
        "zip_code_normalized": str,
        "phone":               str,
        "phone_normalized":    str,
        "warehouse_number":    str,
        "membership_number":   str,
        "account_number":      str,
        # OMS string-typed columns — force string dtype so pandas doesn't
        # infer numeric for things like phone/cell digits and zips. These
        # cover every OMS column (and its _normalized variant) present in
        # the POS CSV; without them, per-chunk dtype inference flips some
        # columns to float and concat → .astype(str) reintroduces the
        # trailing-".0" corruption (same bug already fixed for phone/zip).
        "oms_company":                 str,
        "oms_company_2":               str,
        "oms_email_1":                 str,
        "oms_email_2":                 str,
        "oms_email_3":                 str,
        "oms_phone_1":                 str,
        "oms_phone_2":                 str,
        "oms_phone_3":                 str,
        "oms_cell_1":                  str,
        "oms_cell_2":                  str,
        "oms_address_line_1":          str,
        "oms_city":                    str,
        "oms_state":                   str,
        "oms_zip":                     str,
        "oms_address_line_1_v2":       str,
        "oms_city_2":                  str,
        "oms_state_2":                 str,
        "oms_zip_2":                   str,
        # _normalized variants present in the POS CSV
        "oms_company_normalized":            str,
        "oms_company_2_normalized":          str,
        "oms_email_1_normalized":            str,
        "oms_email_2_normalized":            str,
        "oms_phone_1_normalized":            str,
        "oms_cell_1_normalized":             str,
        "oms_cell_2_normalized":             str,
        "oms_address_line_1_normalized":     str,
        "oms_city_normalized":               str,
        "oms_state_normalized":              str,
        "oms_address_line_1_v2_normalized":  str,
        "oms_city_2_normalized":             str,
        "oms_state_2_normalized":            str,
    }

    log.info("Loading leads file: %s", file_a_path)
    file_a = load_file_from_gcs(file_a_path, dtype=STRING_COLS)

    # CHANGE 2 & 3: the POS file is no longer loaded here — it is streamed
    # by run_streaming_classification. Only the lead count is logged.
    log.info(
        "Lead count: %d | POS file will be streamed (not pre-loaded): %s",
        len(file_a), file_b_path,
    )

    user_table_obj = Table(
        table_name, metadata,
        autoload_with=engine,
        schema=schema,
    )
    # The POS file isn't pre-loaded, so its row count isn't known yet at
    # InProgress time. Insert 0 as a placeholder now, then backfill the
    # real count after streaming (see UPDATE below) — this keeps the audit
    # row's pos_count accurate without a second read of the file.
    stmt = insert(user_table_obj).values(
        match_id=match_id,
        lead_count=len(file_a),
        pos_count=0,
        status="InProgress",
    )
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()

    # CHANGE 4: stream the POS file one warehouse at a time instead of
    # loading it fully and calling run_batched_classification.
    # processed_pos_ids = every txn scanned this run (warehouse had >=1
    # lead), match or not — same contract as before. total_pos_rows =
    # total rows in the file (== old len(file_b)).
    #
    # Temp spill location: per-warehouse part files are written here during
    # the spill pass and deleted as each warehouse is processed. Made unique
    # per run (match_id + timestamp) so concurrent/retried runs don't collide.
    _spill_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_prefix = (
        f"gs://{storage_config.output_bucket_name}/"
        f"{storage_config.temporary_folder}/match_spill/{match_id}_{_spill_ts}"
    )
    log.info("Per-warehouse spill prefix: %s", tmp_prefix)

    final_df, processed_pos_ids, total_pos_rows = run_streaming_classification(
        file_a, file_b_path, classify_matches,
        tmp_prefix=tmp_prefix, sales_dtype=STRING_COLS,
    )
    log.info("POS count (streamed): %d", total_pos_rows)

    # Backfill the real POS count onto the InProgress audit row now that
    # streaming has counted every row. Scoped by match_id (the run key).
    upd = (
        update(user_table_obj)
        .where(user_table_obj.c.match_id == match_id)
        .values(pos_count=total_pos_rows)
    )
    with engine.connect() as conn:
        conn.execute(upd)
        conn.commit()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"primary_match_output_{match_id}_{timestamp}"

    uri = process_and_archive_files(
        source_bucket_name,
        source_folder,
        destination_bucket_name,
        destination_folder,
        final_df,
        base_name,
    )

    log.info("Final output written to: %s", uri)

    # Manifest of scanned transactions — update_cloud_sql marks these
    # is_processed=true (after the match write commits) so they're
    # excluded from future cycles.
    _write_processed_manifest(storage_config, processed_pos_ids)

    # CHANGE 5: file_b no longer exists, so it's dropped from cleanup.
    del file_a, final_df
    gc.collect()

    return uri