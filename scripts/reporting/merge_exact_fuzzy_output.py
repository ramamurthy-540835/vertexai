#!/usr/bin/env python3
"""Merge exact-match CSV and fuzzy pipeline CSV into a single clean output.

Rule: for each (lead_id, pos_id) pair, keep the row with the higher score.
The output is a superset — all exact matches + all fuzzy-only matches.

Usage:
    python3 scripts/reporting/merge_exact_fuzzy_output.py \
        --exact-csv reports/exact_matching/exact_matching.csv \
        --fuzzy-csv reports/lead_match/ctoteam/115/<run_id>/matches.csv \
        --warehouse 115 \
        --output-csv reports/final/exact_fuzzy_combined_matches.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "lead_match_runtime" / "lead_to_pos_match_rules.json"


def load_merge_config(rules_path=None):
    path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    merge_rule = rules.get("override_policy", {}).get("merge_rule", {})
    return {
        "strategy": merge_rule.get("strategy", "highest_score_wins"),
        "comment_carry_forward": merge_rule.get("comment_carry_forward", True),
    }

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


def safe_float(val, default=0.0):
    try:
        s = str(val or "").strip()
        if not s or s.lower() in ("nan", "none", ""):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge(exact_rows, fuzzy_rows, warehouse=None, merge_config=None):
    cfg = merge_config or load_merge_config()
    carry_forward = cfg.get("comment_carry_forward", True)

    if warehouse:
        exact_rows = [r for r in exact_rows if str(r.get("warehouse_number", "")).strip() == str(warehouse)]

    best = {}
    source_tag = {}
    exact_comments = {}

    for r in exact_rows:
        lead = r.get("lead_id", "").strip()
        pos = r.get("pos_id", "").strip()
        if not lead:
            continue
        key = (lead, pos) if pos else (lead, f"__ce__{lead}")
        score = safe_float(r.get("similarity_score"))
        comment = r.get("matching_comments", "").strip()
        if comment:
            exact_comments[key] = comment
        if key not in best or score > safe_float(best[key].get("similarity_score")):
            best[key] = dict(r)
            source_tag[key] = "exact"

    for r in fuzzy_rows:
        lead = r.get("lead_id", "").strip()
        pos = r.get("pos_id", "").strip()
        if not lead or not pos:
            continue
        key = (lead, pos)
        score = safe_float(r.get("similarity_score"))
        if key not in best or score > safe_float(best[key].get("similarity_score")):
            best[key] = dict(r)
            source_tag[key] = "fuzzy"

    carried = 0
    if carry_forward:
        for key, row in best.items():
            if not row.get("matching_comments", "").strip() and key in exact_comments:
                row["matching_comments"] = exact_comments[key]
                carried += 1

    if carried:
        print(f"  Carried forward {carried} comments (comment_carry_forward={carry_forward} from rules JSON)")

    rows = list(best.values())
    rows.sort(key=lambda r: (-safe_float(r.get("similarity_score")), r.get("lead_id", ""), r.get("pos_id", "")))

    sources = Counter(source_tag.values())
    return rows, sources


def print_summary(exact_rows, fuzzy_rows, merged_rows, sources, warehouse):
    exact_wh = [r for r in exact_rows if str(r.get("warehouse_number", "")).strip() == str(warehouse)] if warehouse else exact_rows
    print(f"\n{'='*60}")
    print(f"MERGE SUMMARY — warehouse {warehouse or 'all'}")
    print(f"{'='*60}")
    print(f"  Exact CSV input:     {len(exact_wh):,} rows")
    print(f"  Fuzzy CSV input:     {len(fuzzy_rows):,} rows")
    print(f"  Merged output:       {len(merged_rows):,} rows")
    print(f"  Winner source:       exact={sources.get('exact',0):,}  fuzzy={sources.get('fuzzy',0):,}")
    print()

    mt = Counter(r.get("match_type", "") for r in merged_rows)
    mr = Counter(r.get("match_result", "") for r in merged_rows)
    leads = len(set(r.get("lead_id", "") for r in merged_rows if r.get("lead_id", "").strip()))
    pos = len(set(r.get("pos_id", "") for r in merged_rows if r.get("pos_id", "").strip()))
    print(f"  Unique leads:  {leads:,}")
    print(f"  Unique POS:    {pos:,}")
    print(f"  match_type:    {dict(mt)}")
    print(f"  match_result:  {dict(mr)}")

    scores = [safe_float(r.get("similarity_score")) for r in merged_rows if safe_float(r.get("similarity_score")) > 0]
    if scores:
        exact_scores = [s for s in scores if s >= 100]
        fuzzy_scores = [s for s in scores if 0 < s < 100]
        if exact_scores:
            print(f"  Exact scores:  [{min(exact_scores):.1f}, {max(exact_scores):.1f}]  count={len(exact_scores)}")
        if fuzzy_scores:
            print(f"  Fuzzy scores:  [{min(fuzzy_scores):.1f}, {max(fuzzy_scores):.1f}]  count={len(fuzzy_scores)}")

    pt = sum(1 for r in merged_rows if str(r.get("primary_transaction", "")).strip().lower() in ("true", "1"))
    print(f"  Primary txns:  {pt}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Merge exact and fuzzy match CSVs")
    parser.add_argument("--exact-csv", required=True, help="Path to exact-match output CSV")
    parser.add_argument("--fuzzy-csv", required=True, help="Path to fuzzy pipeline matches.csv or matches_enriched.csv")
    parser.add_argument("--warehouse", type=str, help="Filter to warehouse number")
    parser.add_argument("--output-csv", required=True, help="Output merged CSV path")
    parser.add_argument("--rules-json", default=str(DEFAULT_RULES_PATH), help="Path to business rules JSON")
    args = parser.parse_args()

    cfg = load_merge_config(args.rules_json)
    print(f"Merge config from rules JSON: strategy={cfg['strategy']}, comment_carry_forward={cfg['comment_carry_forward']}")

    exact_rows = load_csv(args.exact_csv)
    fuzzy_rows = load_csv(args.fuzzy_csv)

    merged_rows, sources = merge(exact_rows, fuzzy_rows, args.warehouse, merge_config=cfg)
    print_summary(exact_rows, fuzzy_rows, merged_rows, sources, args.warehouse)

    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPECTED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged_rows)

    print(f"Written: {out}  ({len(merged_rows):,} rows)")


if __name__ == "__main__":
    main()
