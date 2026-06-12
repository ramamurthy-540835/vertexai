import gc
import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import MetaData, Table, insert

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.util.apputil import (
    load_file_from_gcs,
    process_and_archive_files,
)

log = logging.getLogger(__name__)

# ==============================================================
# FAMILY CONFIGURATION
# ==============================================================
# A "family" is a logical matching field on the lead side that
# can be satisfied by ANY of several variant columns on the POS
# side. The family is scored AT MOST ONCE per (lead, POS) pair —
# the variant that hit first (or in the case of address bundles,
# the highest-scoring one) is recorded in matched_key_fields for
# the matching_comments output.
#
# All column names below are the *_normalized variants; the
# preprocess step creates any missing ones.

KEY_FAMILIES = {
    "business_name": (
        "business_name_normalized",
        [
            "business_name_normalized",
            "oms_company_normalized",
            "oms_company_2_normalized",
        ],
        40,
    ),
    "email": (
        "email_normalized",
        [
            "email_normalized",
            "oms_email_1_normalized",
            "oms_email_2_normalized",
            "oms_email_3_normalized",
        ],
        30,
    ),
    "phone": (
        "phone_normalized",
        [
            "phone_normalized",
            "oms_phone_1_normalized",
            "oms_phone_2_normalized",
            "oms_phone_3_normalized",
            "oms_cell_1_normalized",
            "oms_cell_2_normalized",
        ],
        20,
    ),
}

# Address bundles: each bundle binds ONE address_line_1 variant
# with its OWN zip/city/state. When an address line matches, only
# THAT bundle's geo fields are consulted for supplementary scoring
# (zip 10, city 5, state 5). The highest-scoring bundle wins.
ADDRESS_BUNDLES = [
    {
        "name":  "primary",
        "line":  "address_line_one_normalized",
        "zip":   "zip_code_normalized",
        "city":  "city_normalized",
        "state": "state_normalized",
    },
    {
        "name":  "oms",
        "line":  "oms_address_line_1_normalized",
        "zip":   "oms_zip_normalized",
        "city":  "oms_city_normalized",
        "state": "oms_state_normalized",
    },
    {
        "name":  "oms_v2",
        "line":  "oms_address_line_1_v2_normalized",
        "zip":   "oms_zip_2_normalized",
        "city":  "oms_city_2_normalized",
        "state": "oms_state_2_normalized",
    },
]

ADDRESS_LINE_SCORE  = 40
ADDRESS_ZIP_SCORE   = 10
ADDRESS_CITY_SCORE  = 5
ADDRESS_STATE_SCORE = 5

# Supplementary fallback — used ONLY when no address bundle scores.
# Each family is the same OR-of-variants pattern as KEY_FAMILIES.
SUPP_FALLBACK_FAMILIES = {
    "zip_code": (
        "zip_code_normalized",
        [
            "zip_code_normalized",
            "oms_zip_normalized",
            "oms_zip_2_normalized",
        ],
        ADDRESS_ZIP_SCORE,
    ),
    "city": (
        "city_normalized",
        [
            "city_normalized",
            "oms_city_normalized",
            "oms_city_2_normalized",
        ],
        ADDRESS_CITY_SCORE,
    ),
    "state": (
        "state_normalized",
        [
            "state_normalized",
            "oms_state_normalized",
            "oms_state_2_normalized",
        ],
        ADDRESS_STATE_SCORE,
    ),
}

# Columns to normalize: lead side has only the 7 primary cols,
# POS side has primary + every OMS variant referenced above.
LEAD_NORMALIZE_COLS = [
    "business_name_normalized",
    "email_normalized",
    "phone_normalized",
    "address_line_one_normalized",
    "zip_code_normalized",
    "city_normalized",
    "state_normalized",
]

POS_NORMALIZE_COLS = list(
    {col for fam in KEY_FAMILIES.values() for col in fam[1]}
    | {b["line"]  for b in ADDRESS_BUNDLES}
    | {b["zip"]   for b in ADDRESS_BUNDLES}
    | {b["city"]  for b in ADDRESS_BUNDLES}
    | {b["state"] for b in ADDRESS_BUNDLES}
)

MINIMUM_SCORE  = 80
COMPLETE_SCORE = 100

MAX_POSSIBLE_SCORE = (
    sum(score for _, _, score in KEY_FAMILIES.values())
    + ADDRESS_LINE_SCORE + ADDRESS_ZIP_SCORE
    + ADDRESS_CITY_SCORE + ADDRESS_STATE_SCORE
)


def _friendly(field: str) -> str:
    """Strip the _normalized suffix for human-readable output."""
    return field.replace("_normalized", "")


# ==============================================================
# MATCHING COMMENT BUILDER
# ==============================================================
def build_matching_comment(row: pd.Series) -> str:
    """
    Constructs a human-readable comment explaining which fields
    drove the match. matched_key_fields and matched_supp_fields
    are lists of "family (winning_variant)" strings produced by
    the scoring pipeline, e.g. "business_name (oms_company)".

    ServiceNow displays this comment to marketers so they can
    understand why the system matched a lead to a POS transaction
    without re-running the algorithm.
    """
    score        = row["similarity_score"]
    result       = row["match_result"]
    matched_keys = row.get("matched_key_fields", [])
    matched_supp = row.get("matched_supp_fields", [])

    parts = []
    if result == "Match":
        parts.append(
            f"Complete match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"sufficient key and supplementary fields aligned."
        )
    else:
        parts.append(
            f"Potential match (score {score}/{MAX_POSSIBLE_SCORE}): "
            f"partial field alignment; Marketer review recommended."
        )

    if matched_keys:
        parts.append(f"Key fields matched: {', '.join(matched_keys)}.")
    else:
        parts.append(
            "No individual key fields matched exactly; "
            "match qualified via supplementary fields only."
        )

    if matched_supp:
        parts.append(f"Supplementary fields matched: {', '.join(matched_supp)}.")

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
    """POS-side preprocessing — primary + all OMS variants."""
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
# SCORE ONE GROUP — family/bundle-based per-pair scoring
# ==============================================================
def _score_group(
    sales_slice: pd.DataFrame,
    active_leads: pd.DataFrame,
) -> pd.DataFrame:
    """
    Score every (lead, POS) candidate pair from this chronological
    POS group against the currently active leads.

    Returns:
        DataFrame with columns
            lead_id, pos_id, similarity_score,
            matched_key_fields (list[str]),
            matched_supp_fields (list[str])
        filtered to similarity_score >= MINIMUM_SCORE.
    """
    if active_leads.empty or sales_slice.empty:
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "similarity_score",
            "matched_key_fields", "matched_supp_fields",
        ])

    # -- Build candidate-pair table -----------------------------
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

    pairs = leads_small.merge(sales_small, on="warehouse_number", how="inner")
    if pairs.empty:
        return pd.DataFrame(columns=[
            "lead_id", "pos_id", "similarity_score",
            "matched_key_fields", "matched_supp_fields",
        ])

    def _pos_col(name):
        """Resolve a normalized-column name to its post-rename form."""
        return f"{name}__p" if name in pos_primary_overlap else name

    n = len(pairs)
    total_score  = np.zeros(n, dtype=np.int64)
    matched_key  = [[] for _ in range(n)]
    matched_supp = [[] for _ in range(n)]

    # -- Phase 1: KEY FAMILIES (business_name, email, phone) ----
    for family_name, (lead_col, pos_variants, score) in KEY_FAMILIES.items():
        lead_vals = pairs[f"{lead_col}__l"]
        lead_present = lead_vals.notna()

        family_hit = np.zeros(n, dtype=bool)
        winning_variant = [None] * n

        for variant in pos_variants:
            col = _pos_col(variant)
            if col not in pairs.columns:
                continue
            pos_vals = pairs[col]
            hit = (
                lead_present & pos_vals.notna()
                & (lead_vals == pos_vals) & ~family_hit
            )
            if hit.any():
                family_hit |= hit.values
                for idx in np.where(hit.values)[0]:
                    winning_variant[idx] = variant

        if family_hit.any():
            total_score += family_hit.astype(np.int64) * score
            for idx in np.where(family_hit)[0]:
                matched_key[idx].append(
                    f"{family_name} ({_friendly(winning_variant[idx])})"
                )

    # -- Phase 1: ADDRESS BUNDLES -------------------------------
    # For each pair, compute every bundle's score (line+zip+city+state).
    # Keep ONLY the highest-scoring bundle. The chosen bundle's geo
    # contributes to supplementary scoring; no fallback runs.
    lead_addr  = pairs["address_line_one_normalized__l"]
    lead_zip   = pairs["zip_code_normalized__l"]
    lead_city  = pairs["city_normalized__l"]
    lead_state = pairs["state_normalized__l"]
    lead_addr_present = lead_addr.notna()

    best_score    = np.zeros(n, dtype=np.int64)
    best_line     = [None] * n
    best_zip_hit  = np.zeros(n, dtype=bool)
    best_city_hit = np.zeros(n, dtype=bool)
    best_state_hit = np.zeros(n, dtype=bool)
    best_zip_col   = [None] * n
    best_city_col  = [None] * n
    best_state_col = [None] * n

    for bundle in ADDRESS_BUNDLES:
        line_col  = _pos_col(bundle["line"])
        if line_col not in pairs.columns:
            continue

        zip_col   = _pos_col(bundle["zip"])
        city_col  = _pos_col(bundle["city"])
        state_col = _pos_col(bundle["state"])

        pos_addr = pairs[line_col]
        line_hit = (
            lead_addr_present & pos_addr.notna() & (lead_addr == pos_addr)
        )
        if not line_hit.any():
            continue

        pos_zip   = pairs.get(zip_col,   pd.Series(pd.NA, index=pairs.index))
        pos_city  = pairs.get(city_col,  pd.Series(pd.NA, index=pairs.index))
        pos_state = pairs.get(state_col, pd.Series(pd.NA, index=pairs.index))

        zip_hit   = line_hit & lead_zip.notna()   & pos_zip.notna()   & (lead_zip   == pos_zip)
        city_hit  = line_hit & lead_city.notna()  & pos_city.notna()  & (lead_city  == pos_city)
        state_hit = line_hit & lead_state.notna() & pos_state.notna() & (lead_state == pos_state)

        bundle_total = (
            line_hit.astype(np.int64)   * ADDRESS_LINE_SCORE
            + zip_hit.astype(np.int64)  * ADDRESS_ZIP_SCORE
            + city_hit.astype(np.int64) * ADDRESS_CITY_SCORE
            + state_hit.astype(np.int64) * ADDRESS_STATE_SCORE
        )

        # If this bundle beats the current best for any pair, replace.
        better = bundle_total.values > best_score
        if better.any():
            idxs = np.where(better)[0]
            best_score[idxs] = bundle_total.values[idxs]
            for idx in idxs:
                best_line[idx]      = bundle["line"]
                best_zip_hit[idx]   = bool(zip_hit.iloc[idx])
                best_city_hit[idx]  = bool(city_hit.iloc[idx])
                best_state_hit[idx] = bool(state_hit.iloc[idx])
                best_zip_col[idx]   = bundle["zip"]
                best_city_col[idx]  = bundle["city"]
                best_state_col[idx] = bundle["state"]

    address_hit = best_score > 0
    total_score += best_score
    for idx in np.where(address_hit)[0]:
        matched_key[idx].append(f"address ({_friendly(best_line[idx])})")
        if best_zip_hit[idx]:
            matched_supp[idx].append(f"zip_code ({_friendly(best_zip_col[idx])})")
        if best_city_hit[idx]:
            matched_supp[idx].append(f"city ({_friendly(best_city_col[idx])})")
        if best_state_hit[idx]:
            matched_supp[idx].append(f"state ({_friendly(best_state_col[idx])})")

    # -- Phase 2: SUPPLEMENTARY FALLBACK ------------------------
    # Mirrors the original code's gating: supplementary scoring runs
    # ONLY for pairs that already scored on at least one key family
    # (business_name / email / phone / address bundle). A pair with
    # zero key-field hits is not a candidate and never sees supp
    # scoring — preserving the original Phase 1 → Phase 2 semantics.
    #
    # Within candidates, fallback supp runs only when no address
    # bundle won (otherwise the bundle's own geo already scored).
    is_key_candidate = total_score > 0
    no_address       = ~address_hit
    eligible         = no_address & is_key_candidate

    if eligible.any():
        for family_name, (lead_col, pos_variants, score) in SUPP_FALLBACK_FAMILIES.items():
            lead_vals = pairs[f"{lead_col}__l"]
            lead_present = lead_vals.notna() & eligible

            family_hit = np.zeros(n, dtype=bool)
            winning_variant = [None] * n

            for variant in pos_variants:
                col = _pos_col(variant)
                if col not in pairs.columns:
                    continue
                pos_vals = pairs[col]
                hit = (
                    lead_present & pos_vals.notna()
                    & (lead_vals == pos_vals) & ~family_hit
                )
                if hit.any():
                    family_hit |= hit.values
                    for idx in np.where(hit.values)[0]:
                        winning_variant[idx] = variant

            if family_hit.any():
                total_score += family_hit.astype(np.int64) * score
                for idx in np.where(family_hit)[0]:
                    matched_supp[idx].append(
                        f"{family_name} ({_friendly(winning_variant[idx])})"
                    )

    # -- Assemble & threshold -----------------------------------
    out = pd.DataFrame({
        "lead_id":             pairs["lead_id"].values,
        "pos_id":              pairs["pos_id"].values,
        "similarity_score":    total_score,
        "matched_key_fields":  matched_key,
        "matched_supp_fields": matched_supp,
    })
    qualified = (
        out[out["similarity_score"] >= MINIMUM_SCORE]
        .drop_duplicates(subset=["lead_id", "pos_id"])
        .copy()
    )
    return qualified


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
        "match_type",
        "primary_transaction",
        "matched_by",
        "matching_comments",
        "closed_existing_flag",

        # POS dominant
        "account_number",
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
# CLASSIFY MATCHES
# ==============================================================
def classify_matches(
    file_leads: pd.DataFrame,
    file_sales: pd.DataFrame,
) -> pd.DataFrame:
    """
    Chronological matching with Closed-Existing detection and
    family-based scoring (OMS variants on POS side).

    For each (fiscal_year, fiscal_period, week) POS group in
    chronological order:
      1. Run family-based scoring (Phase 1 key families, address
         bundles, Phase 2 supplementary fallback) against currently
         active leads.
      2. For each qualified pair, check if the transaction is
         strictly before the lead's fiscal year/period.
      3. If yes → mark the lead Closed-Existing, remove it from
         the active set, emit a stub row, and skip forever.
      4. Otherwise the pair survives to the normal Match/Potential
         path.

    Because CE detection happens chronologically inside the loop,
    no separate fiscal filter is needed downstream.
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
    # Lead's `week` is renamed to `week_lead` so it never collides with
    # the POS transaction `week` when the two are merged below.
    # If the leads file predates the week column, synthesize an all-NA
    # week_lead so the week branch of the CE check is simply inert
    # (NA comparisons resolve to False) rather than crashing.
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
            continue

        # Attach fiscal columns for CE check
        qualified["fiscal_year_transaction"]   = fy
        qualified["fiscal_period_transaction"] = fp
        qualified["week"]                      = wk
        qualified = qualified.join(lead_fiscal, on="lead_id", how="left")

        # Coerce all six comparison columns to a nullable integer dtype
        # so the < / == comparisons are numeric, never lexicographic.
        # (e.g. as strings "10" < "9" is True, which would be wrong.)
        # week_lead may be NaN if a lead somehow lacks a week — those
        # rows simply won't satisfy the week branch of the mask.
        for _col in (
            "fiscal_year_transaction", "fiscal_period_transaction", "week",
            "fiscal_year_lead", "fiscal_period_lead", "week_lead",
        ):
            qualified[_col] = pd.to_numeric(
                qualified[_col], errors="coerce"
            ).astype("Int64")

        # CE "prior" check — now three levels deep: year, then period,
        # then week. A transaction is prior (→ Closed-Existing) when:
        #   txn_year  < lead_year                                   OR
        #   same year, txn_period  < lead_period                    OR
        #   same year, same period, txn_week < lead_week
        # All three equal → NOT prior → lead stays for normal matching.
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
        # Int64 comparisons yield pd.NA where either side is NA; treat
        # NA as "not prior" so a missing week never forces CE.
        prior_mask = prior_mask.fillna(False).astype(bool)

        new_ce = set(qualified.loc[prior_mask, "lead_id"].unique())
        if new_ce:
            ce_lead_ids.update(new_ce)
            active_lead_ids -= new_ce
            log.info(
                "Group %s/%s/wk%s — %d new CE lead(s): %s",
                fy, fp, wk, len(new_ce), list(new_ce)[:10],
            )

        # Surviving pairs: only carry the columns needed downstream.
        # Fiscal/POS data comes back via sales_subset merge later,
        # avoiding column-name collisions.
        surviving = qualified[~qualified["lead_id"].isin(new_ce)].copy()
        if surviving.empty:
            continue

        normal_pair_frames.append(
            surviving[[
                "lead_id", "pos_id", "similarity_score",
                "matched_key_fields", "matched_supp_fields",
            ]].copy()
        )

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

    # ==========================================================
    # FINAL MERGE — bring original (non-normalized) POS columns
    # in for the ServiceNow payload, and lead fiscal/updated_date.
    # ==========================================================
    lead_subset = leads[[
        "lead_id",
        "updated_date",
        "fiscal_year_lead",
        "fiscal_period_lead",
    ]]

    sales_subset = (
        sales
        .rename(columns={"business_name": "business_name_transaction"})
        [[
            "pos_id",
            "account_number",
            "business_name_transaction",
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
            "address_line_one",
            "address_line_two",
            "city",
            "state",
            "zip_code",
            "email",
            "phone",
            "industry_description",
        ]]
    )

    matched_df = (
        normal_qualified
        .merge(lead_subset,  on="lead_id", how="inner")
        .merge(sales_subset, on="pos_id",  how="inner")
    )

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
    single match run.

    Workflow:
        1. Loads job configuration (storage + DB) from the provided
           config file path.
        2. Reads the leads and POS Parquet files from GCS (falls back
           to paths defined in config if not explicitly provided).
        3. Inserts an audit record into the DB with status "InProgress"
           before classification begins.
        4. Delegates matching to ``classify_matches()``, which runs the
           chronological group-by-group family-based scoring pass with
           Closed-Existing detection, followed by Match/Potential
           classification and primary transaction assignment.
        5. Writes the final matched DataFrame to GCS via
           ``process_and_archive_files()`` and returns the output URI.

    Args:
        match_id (str): Unique identifier for this match run.
        config_file_path (str): GCS/local path to the YAML/JSON job
            configuration file. Must contain valid storage_config and
            db_config sections.
        file_a_path (str, optional): GCS path to the leads Parquet
            file. Defaults to storage_config.temp_leads_path.
        file_b_path (str, optional): GCS path to the POS Parquet
            file. Defaults to storage_config.temp_pos_path.

    Returns:
        str: GCS URI of the written output file.
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
        # OMS string-typed columns — force string dtype so pandas
        # doesn't infer numeric for things like phone digits/zips.
        "oms_zip":             str,
        "oms_zip_2":           str,
        "oms_phone_1":         str,
        "oms_phone_2":         str,
        "oms_phone_3":         str,
        "oms_cell_1":          str,
        "oms_cell_2":          str,
    }

    log.info("Loading leads file: %s", file_a_path)
    file_a = load_file_from_gcs(file_a_path, dtype=STRING_COLS)

    log.info("Loading POS file: %s", file_b_path)
    file_b = load_file_from_gcs(file_b_path, dtype=STRING_COLS)

    log.info("Lead count: %d | POS count: %d", len(file_a), len(file_b))

    user_table_obj = Table(
        table_name, metadata,
        autoload_with=engine,
        schema=schema,
    )
    stmt = insert(user_table_obj).values(
        match_id=match_id,
        lead_count=len(file_a),
        pos_count=len(file_b),
        status="InProgress",
    )
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()

    final_df = classify_matches(file_a, file_b)

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

    del file_a, file_b, final_df
    gc.collect()

    return uri