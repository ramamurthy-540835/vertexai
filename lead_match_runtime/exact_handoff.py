"""Read-only adapter for external deterministic exact-match output CSV.

Validates the 36-column schema, classifies rows into final_exact /
deterministic_potential / closed_existing / invalid, handles duplicate POS
ownership, and checks source-ID alignment against raw lead/POS files.

Does NOT modify input files, connect to Cloud SQL, or write to GCS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = [
    "lead_id", "pos_id", "match_result", "similarity_score", "winning_set",
    "match_type", "primary_transaction", "matched_by", "matching_comments",
    "closed_existing_flag", "account_number", "transaction_count",
    "business_name_transaction", "membership_number", "warehouse_number",
    "sales_reference_id", "fiscal_year_transaction", "fiscal_period_transaction",
    "week", "shop_type", "bd_industry", "order_amount", "industry_description",
    "first_name", "last_name", "address_line_one", "address_line_two",
    "city", "state", "zip_code", "email", "phone",
    "u_matched_lead_number", "u_order_amount", "u_order_amount_rounded",
    "updated_date",
]

DEFAULT_CONFIG = {
    "source_schema": "primary_match_output_36_column_v1",
    "source_score_max": 150,
    "final_match_min_score": 100,
    "deterministic_potential_min_score": 70,
    "deterministic_potential_max_score": 99.999,
    "final_match_result": "Match",
    "potential_match_result": "Potential",
    "source_match_type": "Exact",
    "closed_existing_flag_column": "closed_existing_flag",
    "deterministic_potential_policy": "semantic_rescore",
    "preserve_source_scores": True,
    "do_not_compare_score_scales": True,
    "require_source_id_alignment": True,
}


@dataclass
class ExactHandoffResult:
    source_uri: str
    row_count: int
    final_exact_rows: pd.DataFrame
    deterministic_potential_rows: pd.DataFrame
    closed_existing_rows: pd.DataFrame
    invalid_rows: pd.DataFrame
    final_exact_lead_ids: set[str]
    final_exact_pos_ids: set[str]
    deterministic_potential_lead_ids: set[str]
    deterministic_potential_pos_ids: set[str]
    closed_existing_lead_ids: set[str]
    duplicate_pos_groups: pd.DataFrame
    ambiguous_exact_pos_ids: set[str]
    summary: dict = field(default_factory=dict)


def _safe_str(val) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "<na>") else s


def _safe_float(val, default: float = 0.0) -> float:
    s = _safe_str(val)
    if not s:
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _safe_bool(val) -> bool:
    s = _safe_str(val).lower()
    return s in ("true", "1", "yes", "t")


def validate_schema(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    actual = list(df.columns)
    if len(actual) != len(EXPECTED_COLUMNS):
        errors.append(f"Column count: expected {len(EXPECTED_COLUMNS)}, got {len(actual)}")
    missing = [c for c in EXPECTED_COLUMNS if c not in actual]
    if missing:
        errors.append(f"Missing columns: {missing}")
    extra = [c for c in actual if c not in EXPECTED_COLUMNS]
    if extra:
        errors.append(f"Extra columns: {extra}")
    for i, (expected, got) in enumerate(zip(EXPECTED_COLUMNS, actual)):
        if expected != got:
            errors.append(f"Column order mismatch at position {i}: expected '{expected}', got '{got}'")
            break
    return errors


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["similarity_score"] = out["similarity_score"].apply(lambda v: _safe_float(v))
    out["winning_set"] = out["winning_set"].apply(lambda v: _safe_float(v))
    out["closed_existing_flag"] = out["closed_existing_flag"].apply(_safe_bool)
    out["primary_transaction"] = out["primary_transaction"].apply(_safe_bool)
    for col in ["fiscal_year_transaction", "fiscal_period_transaction", "week", "transaction_count"]:
        out[col] = out[col].apply(lambda v: _safe_float(v))
    for col in ["lead_id", "pos_id", "match_result", "match_type", "warehouse_number",
                "sales_reference_id", "u_matched_lead_number"]:
        out[col] = out[col].apply(_safe_str)
    out["match_result_lower"] = out["match_result"].str.lower()
    out["match_type_lower"] = out["match_type"].str.lower()
    out["pos_id_populated"] = out["pos_id"].str.strip() != ""
    return out


def classify_rows(df: pd.DataFrame, config: dict | None = None) -> dict[str, pd.DataFrame]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    final_min = float(cfg["final_match_min_score"])
    pot_min = float(cfg["deterministic_potential_min_score"])
    pot_max = float(cfg["deterministic_potential_max_score"])
    final_mr = str(cfg["final_match_result"]).lower()
    pot_mr = str(cfg["potential_match_result"]).lower()

    final_mask = (
        df["pos_id_populated"]
        & (df["match_result_lower"] == final_mr)
        & (df["similarity_score"] >= final_min)
    )

    potential_mask = (
        df["pos_id_populated"]
        & (df["match_result_lower"] == pot_mr)
        & (df["similarity_score"] >= pot_min)
        & (df["similarity_score"] < final_min)
    )

    ce_mask = df["closed_existing_flag"] == True  # noqa: E712

    classified = pd.Series("invalid", index=df.index)
    classified[final_mask] = "final_exact"
    classified[potential_mask & ~final_mask] = "deterministic_potential"
    classified[ce_mask & ~final_mask & ~potential_mask] = "closed_existing"

    df = df.copy()
    df["_classification"] = classified

    return {
        "final_exact": df[df["_classification"] == "final_exact"].copy(),
        "deterministic_potential": df[df["_classification"] == "deterministic_potential"].copy(),
        "closed_existing": df[df["_classification"] == "closed_existing"].copy(),
        "invalid": df[df["_classification"] == "invalid"].copy(),
    }


def find_duplicate_pos(final_df: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    if final_df.empty:
        return pd.DataFrame(), set()

    pos_counts = final_df.groupby("pos_id").agg(
        lead_count=("lead_id", "nunique"),
        top_score=("similarity_score", "max"),
        lead_ids=("lead_id", lambda x: list(x.unique())),
    ).reset_index()

    dupes = pos_counts[pos_counts["lead_count"] > 1].copy()
    if dupes.empty:
        return pd.DataFrame(), set()

    ambiguous: set[str] = set()
    conflict_rows: list[dict] = []
    for _, grp_info in dupes.iterrows():
        pid = grp_info["pos_id"]
        top = grp_info["top_score"]
        rows_for_pos = final_df[final_df["pos_id"] == pid].copy()
        top_rows = rows_for_pos[rows_for_pos["similarity_score"] == top]
        tie_count = top_rows["lead_id"].nunique()

        if tie_count > 1:
            ambiguous.add(pid)

        for _, r in rows_for_pos.iterrows():
            conflict_rows.append({
                "pos_id": pid,
                "sales_reference_id": _safe_str(r.get("sales_reference_id")),
                "lead_id": _safe_str(r.get("lead_id")),
                "deterministic_score_150": r.get("similarity_score", 0),
                "match_result": _safe_str(r.get("match_result")),
                "winning_set": r.get("winning_set", 0),
                "top_score": top,
                "top_tie_count": tie_count,
                "conflict_reason": "tied_top_score" if tie_count > 1 else "multi_lead_claim",
                "recommended_disposition": "Manual Review" if tie_count > 1 else "highest_score_owner",
            })

    return pd.DataFrame(conflict_rows), ambiguous


def check_source_id_alignment(
    result: ExactHandoffResult,
    source_lead_ids: set[str],
    source_pos_ids: set[str],
) -> dict:
    exact_leads = result.final_exact_lead_ids | result.deterministic_potential_lead_ids | result.closed_existing_lead_ids
    exact_pos = result.final_exact_pos_ids | result.deterministic_potential_pos_ids

    lead_overlap = exact_leads & source_lead_ids
    pos_overlap = exact_pos & source_pos_ids

    lead_pct = (len(lead_overlap) / len(exact_leads) * 100) if exact_leads else 0.0
    pos_pct = (len(pos_overlap) / len(exact_pos) * 100) if exact_pos else 0.0

    alignment = {
        "exact_lead_id_count": len(exact_leads),
        "exact_pos_id_count": len(exact_pos),
        "source_lead_id_count": len(source_lead_ids),
        "source_pos_id_count": len(source_pos_ids),
        "matched_lead_id_count": len(lead_overlap),
        "matched_pos_id_count": len(pos_overlap),
        "lead_id_overlap_percentage": round(lead_pct, 2),
        "pos_id_overlap_percentage": round(pos_pct, 2),
        "missing_exact_lead_ids": sorted(exact_leads - source_lead_ids),
        "missing_exact_pos_ids": sorted(exact_pos - source_pos_ids)[:100],
        "pass": lead_pct > 0 and pos_pct > 0,
    }
    return alignment


def load_and_validate(
    csv_path: str,
    warehouse_number: int | None = None,
    config: dict | None = None,
) -> ExactHandoffResult:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Exact output CSV not found: {path}")

    df = pd.read_csv(path, dtype=str)
    df.columns = df.columns.str.strip()

    schema_errors = validate_schema(df)
    if schema_errors:
        for err in schema_errors:
            logger.error("Schema: %s", err)
        raise ValueError(f"Exact output schema validation failed: {schema_errors}")

    df = normalize_df(df)
    total_rows = len(df)

    if warehouse_number is not None:
        wh_str = str(warehouse_number)
        scored_in_wh = df[df["warehouse_number"] == wh_str]
        logger.info("Warehouse %s: %d of %d rows", wh_str, len(scored_in_wh), total_rows)

    classified = classify_rows(df, config)
    final_df = classified["final_exact"]
    potential_df = classified["deterministic_potential"]
    ce_df = classified["closed_existing"]
    invalid_df = classified["invalid"]

    dup_groups, ambiguous_ids = find_duplicate_pos(final_df)

    final_lead_ids = set(final_df["lead_id"].dropna().unique()) - {""}
    final_pos_ids = set(final_df["pos_id"].dropna().unique()) - {""}
    pot_lead_ids = set(potential_df["lead_id"].dropna().unique()) - {""}
    pot_pos_ids = set(potential_df["pos_id"].dropna().unique()) - {""}
    ce_lead_ids = set(ce_df["lead_id"].dropna().unique()) - {""}

    summary = {
        "source_uri": str(path),
        "total_rows": total_rows,
        "final_exact_rows": len(final_df),
        "deterministic_potential_rows": len(potential_df),
        "closed_existing_rows": len(ce_df),
        "invalid_rows": len(invalid_df),
        "final_exact_leads": len(final_lead_ids),
        "final_exact_pos": len(final_pos_ids),
        "deterministic_potential_leads": len(pot_lead_ids),
        "deterministic_potential_pos": len(pot_pos_ids),
        "closed_existing_leads": len(ce_lead_ids),
        "duplicate_pos_groups": len(dup_groups["pos_id"].unique()) if not dup_groups.empty else 0,
        "ambiguous_exact_pos_ids": len(ambiguous_ids),
        "score_range_final": {
            "min": float(final_df["similarity_score"].min()) if not final_df.empty else None,
            "max": float(final_df["similarity_score"].max()) if not final_df.empty else None,
        },
        "score_range_potential": {
            "min": float(potential_df["similarity_score"].min()) if not potential_df.empty else None,
            "max": float(potential_df["similarity_score"].max()) if not potential_df.empty else None,
        },
        "warehouses": sorted(df["warehouse_number"].dropna().unique().tolist()),
    }

    return ExactHandoffResult(
        source_uri=str(path),
        row_count=total_rows,
        final_exact_rows=final_df,
        deterministic_potential_rows=potential_df,
        closed_existing_rows=ce_df,
        invalid_rows=invalid_df,
        final_exact_lead_ids=final_lead_ids,
        final_exact_pos_ids=final_pos_ids,
        deterministic_potential_lead_ids=pot_lead_ids,
        deterministic_potential_pos_ids=pot_pos_ids,
        closed_existing_lead_ids=ce_lead_ids,
        duplicate_pos_groups=dup_groups,
        ambiguous_exact_pos_ids=ambiguous_ids,
        summary=summary,
    )
