#!/usr/bin/env python3
"""Read-only audit of exact-match handoff against lead/POS source files.

Validates schema, classifies rows, checks source-ID alignment,
identifies duplicate POS conflicts, and writes local audit reports.

No embedding calls. No Cloud SQL. No GCS writes.

Usage:
    python3 scripts/audit_exact_handoff.py \\
      --exact-output-csv reports/exact_matching/exact_matching.csv \\
      --leads-file mock_data/115/leads_corrected.xlsx \\
      --pos-file mock_data/115/pos_corrected.xlsx \\
      --warehouse-number 115 \\
      --output-dir reports/exact_handoff_audit/115
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lead_match_runtime.exact_handoff import (
    ExactHandoffResult,
    check_source_id_alignment,
    load_and_validate,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit exact-match handoff (read-only).")
    p.add_argument("--exact-output-csv", required=True)
    p.add_argument("--leads-file", required=True)
    p.add_argument("--pos-file", required=True)
    p.add_argument("--warehouse-number", type=int, required=True)
    p.add_argument("--output-dir", default="reports/exact_handoff_audit")
    return p.parse_args()


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


def load_source_ids(leads_path: str, pos_path: str, wh: int) -> tuple[set[str], set[str]]:
    leads_p = Path(leads_path)
    if leads_p.suffix in (".xlsx", ".xls"):
        leads_df = pd.read_excel(leads_p)
    else:
        leads_df = pd.read_csv(leads_p, dtype=str)

    pos_p = Path(pos_path)
    if pos_p.suffix in (".xlsx", ".xls"):
        pos_df = pd.read_excel(pos_p)
    else:
        pos_df = pd.read_csv(pos_p, dtype=str)

    lead_ids = set(leads_df["lead_id"].apply(_safe_str).unique()) - {""}
    pos_ids = set(pos_df["pos_id"].apply(_safe_str).unique()) - {""}

    return lead_ids, pos_ids


def write_audit(result: ExactHandoffResult, alignment: dict, args: argparse.Namespace) -> None:
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    full_summary = {
        **result.summary,
        "warehouse_filter": args.warehouse_number,
        "leads_file": args.leads_file,
        "pos_file": args.pos_file,
        "source_id_alignment": {k: v for k, v in alignment.items() if k != "missing_exact_lead_ids" and k != "missing_exact_pos_ids"},
        "EXACT_SOURCE_ID_ALIGNMENT_PASS": alignment["pass"],
        "EXACT_HANDOFF_READY": alignment["pass"],
    }

    with open(out / "exact_handoff_summary.json", "w") as f:
        json.dump(full_summary, f, indent=2, default=str)

    if not result.duplicate_pos_groups.empty:
        result.duplicate_pos_groups.to_csv(out / "exact_handoff_conflicts.csv", index=False)
    else:
        pd.DataFrame(columns=[
            "pos_id", "sales_reference_id", "lead_id", "deterministic_score_150",
            "match_result", "winning_set", "top_score", "top_tie_count",
            "conflict_reason", "recommended_disposition",
        ]).to_csv(out / "exact_handoff_conflicts.csv", index=False)

    missing_leads = alignment.get("missing_exact_lead_ids", [])
    pd.DataFrame({"lead_id": missing_leads}).to_csv(out / "exact_missing_lead_ids.csv", index=False)

    missing_pos = alignment.get("missing_exact_pos_ids", [])
    pd.DataFrame({"pos_id": missing_pos}).to_csv(out / "exact_missing_pos_ids.csv", index=False)

    s = result.summary
    a = alignment
    md = f"""# Exact Handoff Audit — Warehouse {args.warehouse_number}

## Source
- **Exact CSV:** `{args.exact_output_csv}`
- **Leads file:** `{args.leads_file}`
- **POS file:** `{args.pos_file}`

## Row Classification

| Category | Rows | Leads | POS |
|----------|------|-------|-----|
| Final Exact (Match, score>=100) | {s['final_exact_rows']:,} | {s['final_exact_leads']:,} | {s['final_exact_pos']:,} |
| Deterministic Potential (70-99) | {s['deterministic_potential_rows']:,} | {s['deterministic_potential_leads']:,} | {s['deterministic_potential_pos']:,} |
| Closed Existing | {s['closed_existing_rows']:,} | {s['closed_existing_leads']:,} | — |
| Invalid / Unknown | {s['invalid_rows']:,} | — | — |
| **Total** | **{s['total_rows']:,}** | | |

## Score Ranges

| Category | Min | Max |
|----------|-----|-----|
| Final Exact | {s['score_range_final']['min']} | {s['score_range_final']['max']} |
| Deterministic Potential | {s['score_range_potential']['min']} | {s['score_range_potential']['max']} |

## Duplicate POS Ownership

| Metric | Count |
|--------|-------|
| Duplicate POS groups | {s['duplicate_pos_groups']:,} |
| Ambiguous (tied top score) | {s['ambiguous_exact_pos_ids']:,} |

## Source ID Alignment

| Metric | Value |
|--------|-------|
| Exact lead IDs | {a['exact_lead_id_count']:,} |
| Source lead IDs | {a['source_lead_id_count']:,} |
| Lead overlap | {a['matched_lead_id_count']:,} ({a['lead_id_overlap_percentage']:.1f}%) |
| Exact POS IDs | {a['exact_pos_id_count']:,} |
| Source POS IDs | {a['source_pos_id_count']:,} |
| POS overlap | {a['matched_pos_id_count']:,} ({a['pos_id_overlap_percentage']:.1f}%) |

### Alignment Result

```
EXACT_SOURCE_ID_ALIGNMENT_PASS={a['pass']}
EXACT_LEAD_ID_OVERLAP_PCT={a['lead_id_overlap_percentage']}
EXACT_POS_ID_OVERLAP_PCT={a['pos_id_overlap_percentage']}
EXACT_HANDOFF_READY={a['pass']}
```

"""
    if not a["pass"]:
        md += """### MISMATCH DETECTED

The exact-output file and lead/POS source records do not originate from
the same dataset or ID namespace. The fuzzy pipeline must NOT proceed
until the data sources are aligned.
"""

    with open(out / "exact_handoff_audit.md", "w") as f:
        f.write(md)


def main() -> int:
    args = parse_args()

    print(f"Loading exact output: {args.exact_output_csv}")
    result = load_and_validate(args.exact_output_csv, args.warehouse_number)

    print(f"Loading source IDs: leads={args.leads_file}, pos={args.pos_file}")
    source_leads, source_pos = load_source_ids(args.leads_file, args.pos_file, args.warehouse_number)

    alignment = check_source_id_alignment(result, source_leads, source_pos)

    write_audit(result, alignment, args)

    s = result.summary
    a = alignment
    print(f"\n{'='*60}")
    print(f"Exact Handoff Audit — Warehouse {args.warehouse_number}")
    print(f"{'='*60}")
    print(f"  Total rows:                 {s['total_rows']:,}")
    print(f"  Final Exact (Match>=100):   {s['final_exact_rows']:,}  ({s['final_exact_leads']:,} leads, {s['final_exact_pos']:,} POS)")
    print(f"  Deterministic Potential:    {s['deterministic_potential_rows']:,}  ({s['deterministic_potential_leads']:,} leads, {s['deterministic_potential_pos']:,} POS)")
    print(f"  Closed Existing:            {s['closed_existing_rows']:,}  ({s['closed_existing_leads']:,} leads)")
    print(f"  Invalid/Unknown:            {s['invalid_rows']:,}")
    print(f"  Duplicate POS groups:       {s['duplicate_pos_groups']:,}")
    print(f"  Ambiguous tied POS:         {s['ambiguous_exact_pos_ids']:,}")
    print(f"  Score range (final):        {s['score_range_final']}")
    print(f"  Score range (potential):    {s['score_range_potential']}")
    print(f"\n  Source ID Alignment:")
    print(f"    Lead overlap: {a['matched_lead_id_count']:,}/{a['exact_lead_id_count']:,} ({a['lead_id_overlap_percentage']:.1f}%)")
    print(f"    POS overlap:  {a['matched_pos_id_count']:,}/{a['exact_pos_id_count']:,} ({a['pos_id_overlap_percentage']:.1f}%)")
    print(f"    EXACT_SOURCE_ID_ALIGNMENT_PASS={a['pass']}")
    print(f"    EXACT_HANDOFF_READY={a['pass']}")

    if not a["pass"]:
        print(f"\n  MISMATCH: exact IDs and source IDs are from different datasets.")

    print(f"\n  Output: {args.output_dir}/")
    print(f"{'='*60}")

    return 0 if a["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
