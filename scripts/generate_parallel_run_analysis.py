#!/usr/bin/env python3
"""
Parallel Run Analysis: per-warehouse analysis docs + project-level rollup.

For each warehouse with a completed run, produces:
  - analysis.md   (deterministic facts + data-integrity flags + Gemini narrative)
  - analysis_facts.json  (structured facts for downstream consumption)

Then produces a project-level rollup comparing all warehouses:
  - project_parallel_run_analysis.md  (comparison table + Gemini comparative + fleet summary)

Read-only on existing GCS artifacts (summary.json, matches.csv).
No Cloud SQL writes, no matching/rules/data changes.
"""

import argparse
import io
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google.cloud import storage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_match_distribution import (
    ensure_band_column,
    load_rules_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

GUARDRAIL_SYSTEM_INSTRUCTION = (
    "The scoring rules are FIXED and authoritative: floor 70, fuzzy ceiling 99.999, "
    "bands 90-99.999 / 85-89.999 / 70-84.999, formula (4*address+3*name)/7. "
    "Do NOT propose changing the floor, ceiling, formula, or band boundaries. "
    "Do NOT call score clustering an 'artifact' — clustering near a cutoff is "
    "expected from the weighted formula. You may note operational impact (e.g. "
    "'most rows fall in Potential, large review queue') and recommend ONLY: "
    "calibrate thresholds against a human-labeled validation set. Never recommend "
    "an auto-reject or auto-promote cutoff that contradicts the floor of 70."
)


# ---------------------------------------------------------------------------
# Phase 1 — Discovery & Download
# ---------------------------------------------------------------------------

def discover_warehouse_runs(
    gcs_client: storage.Client,
    bucket_name: str,
    project_id: str,
    warehouses: list[str] | None = None,
) -> dict[str, dict]:
    """Find the latest non-dry-run run per warehouse.

    Returns {warehouse: {"run_id": ..., "summary": ..., "prefix": ...}}
    """
    bucket = gcs_client.bucket(bucket_name)
    base_prefix = f"reports/lead_match/{project_id}/"

    if warehouses:
        wh_prefixes = [f"{base_prefix}{wh}/" for wh in warehouses]
    else:
        wh_blobs = list(gcs_client.list_blobs(bucket, prefix=base_prefix, delimiter="/"))
        _ = wh_blobs  # consume iterator so prefixes populate
        iterator = gcs_client.list_blobs(bucket, prefix=base_prefix, delimiter="/")
        list(iterator)
        wh_prefixes = sorted(iterator.prefixes)

    results = {}
    for wh_prefix in wh_prefixes:
        wh = wh_prefix.rstrip("/").split("/")[-1]
        if wh.startswith("_"):
            continue

        run_iterator = gcs_client.list_blobs(bucket, prefix=wh_prefix, delimiter="/")
        list(run_iterator)
        run_prefixes = sorted(run_iterator.prefixes)

        best_run = None
        best_time = None

        for run_prefix in run_prefixes:
            summary_path = f"{run_prefix}summary.json"
            blob = bucket.blob(summary_path)
            if not blob.exists():
                continue

            try:
                summary = json.loads(blob.download_as_text())
            except Exception:
                continue

            if summary.get("dry_run", False):
                continue
            if summary.get("report_type") == "post_fix_verification":
                continue

            generated = summary.get("generated_at", "")
            if not best_time or generated > best_time:
                best_time = generated
                best_run = {
                    "run_id": summary.get("match_run_id", run_prefix.rstrip("/").split("/")[-1]),
                    "summary": summary,
                    "prefix": run_prefix,
                }

        if best_run:
            results[wh] = best_run
            logger.info("Warehouse %s: selected run %s", wh, best_run["run_id"])
        else:
            logger.warning("Warehouse %s: no valid run found", wh)

    return results


def download_matches_csv(
    gcs_client: storage.Client,
    bucket_name: str,
    run_prefix: str,
) -> pd.DataFrame:
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(f"{run_prefix}matches.csv")
    return pd.read_csv(io.StringIO(blob.download_as_text()))


# ---------------------------------------------------------------------------
# Phase 2 — Per-Warehouse Analysis
# ---------------------------------------------------------------------------

def compute_deterministic_facts(
    summary: dict,
    df: pd.DataFrame,
    rules: dict,
) -> dict:
    """Compute all deterministic facts from summary.json + matches.csv."""
    df = ensure_band_column(df, rules)

    exact_df = df[df["match_type"] == "Exact"]
    fuzzy_df = df[df["match_type"].isin(["Fuzzy", "Manual Review"])]

    # --- Counts ---
    counts = {
        "leads": int(summary.get("lead_rows", 0)),
        "pos": int(summary.get("pos_rows", 0)),
        "lead_embeddings": int(summary.get("lead_embedding_rows", 0)),
        "pos_embeddings": int(summary.get("pos_embedding_rows", 0)),
        "exact": int(summary.get("match_type_counts", {}).get("Exact", 0)),
        "fuzzy": int(summary.get("match_type_counts", {}).get("Fuzzy", 0)),
        "manual_review": int(summary.get("match_type_counts", {}).get("Manual Review", 0)),
        "total_rows": int(summary.get("match_rows", len(df))),
        "primary_transactions": int(summary.get("primary_transaction_count", 0)),
    }

    # --- Band counts from matches.csv ---
    band_counts = {}
    for band_name, count in df["band"].value_counts().items():
        band_counts[str(band_name)] = int(count)

    band_summary = {
        "matching_high_90_99": int(
            ((df["final_score"] >= 90) & (df["final_score"] <= 99.999) & (df["match_type"] != "Exact")).sum()
        ),
        "potential_medium_85_89": int(
            ((df["final_score"] >= 85) & (df["final_score"] < 90) & (df["match_type"] != "Exact")).sum()
        ),
        "potential_low_70_84": int(
            ((df["final_score"] >= 70) & (df["final_score"] < 85) & (df["match_type"] != "Exact")).sum()
        ),
    }

    # --- Score stats for non-exact rows ---
    non_exact_scores = pd.to_numeric(
        fuzzy_df["final_score"], errors="coerce"
    ).dropna()

    if len(non_exact_scores) > 0:
        score_stats = {
            "min": round(float(non_exact_scores.min()), 3),
            "max": round(float(non_exact_scores.max()), 3),
            "mean": round(float(non_exact_scores.mean()), 3),
            "median": round(float(non_exact_scores.median()), 3),
            "std": round(float(non_exact_scores.std()), 3),
        }
        hist, _ = pd.cut(
            non_exact_scores, bins=range(70, 102, 1), right=False, retbins=True
        )
        hist_counts = hist.value_counts().sort_index()
        histogram = {str(k): int(v) for k, v in hist_counts.items()}
        peak_bin = str(hist_counts.idxmax()) if len(hist_counts) > 0 else None
        peak_count = int(hist_counts.max()) if len(hist_counts) > 0 else 0
        score_stats["peak_bin"] = peak_bin
        score_stats["peak_count"] = peak_count
    else:
        score_stats = {}
        histogram = {}

    # --- Lifecycle split: Closed-Match = Exact + Matching-High-fuzzy ---
    closed_match_total = int(
        summary.get("lifecycle_state_counts", {}).get("Closed - Match", 0)
    )
    closed_match_exact = int(len(exact_df))
    closed_match_fuzzy_high = int(
        ((df["match_type"] != "Exact") & (df["lifecycle_state"] == "Closed - Match")).sum()
    )
    lifecycle_split = {
        "closed_match_total": closed_match_total,
        "closed_match_exact": closed_match_exact,
        "closed_match_fuzzy_high": closed_match_fuzzy_high,
        "potential_total": int(
            summary.get("lifecycle_state_counts", {}).get("Potential", 0)
        ),
    }

    # --- Review workload ---
    review_rows = int(
        ((df["final_score"] >= 70) & (df["final_score"] < 90) & (df["match_type"] != "Exact")).sum()
    )
    review_pct = round(100 * review_rows / len(df), 2) if len(df) > 0 else 0

    return {
        "counts": counts,
        "band_counts": band_counts,
        "band_summary": band_summary,
        "score_stats": score_stats,
        "histogram": histogram,
        "lifecycle_split": lifecycle_split,
        "review_workload_rows": review_rows,
        "review_workload_pct": review_pct,
    }


def compute_data_integrity_flags(
    summary: dict,
    df: pd.DataFrame,
) -> dict:
    """Check embedding coverage vs exact-claimed exclusions."""
    exact_claimed_leads = int(df[df["match_type"] == "Exact"]["lead_id"].nunique())
    exact_claimed_pos = int(df[df["match_type"] == "Exact"]["pos_id"].nunique())

    lead_rows = int(summary.get("lead_rows", 0))
    pos_rows = int(summary.get("pos_rows", 0))
    lead_emb = int(summary.get("lead_embedding_rows", 0))
    pos_emb = int(summary.get("pos_embedding_rows", 0))

    actual_unembedded_leads = lead_rows - lead_emb
    actual_unembedded_pos = pos_rows - pos_emb

    lead_gap = actual_unembedded_leads - exact_claimed_leads
    pos_gap = actual_unembedded_pos - exact_claimed_pos

    flags = []
    if lead_gap > 0:
        flags.append(
            f"LEAD EMBEDDING GAP: {lead_gap} leads un-embedded beyond exact exclusion "
            f"(actual unembedded={actual_unembedded_leads}, exact-claimed={exact_claimed_leads})"
        )
    if pos_gap > 0:
        flags.append(
            f"POS EMBEDDING GAP: {pos_gap} POS un-embedded beyond exact exclusion "
            f"(actual unembedded={actual_unembedded_pos}, exact-claimed={exact_claimed_pos})"
        )

    return {
        "exact_claimed_leads": exact_claimed_leads,
        "exact_claimed_pos": exact_claimed_pos,
        "actual_unembedded_leads": actual_unembedded_leads,
        "actual_unembedded_pos": actual_unembedded_pos,
        "expected_unembedded_leads": exact_claimed_leads,
        "expected_unembedded_pos": exact_claimed_pos,
        "lead_gap": max(lead_gap, 0),
        "pos_gap": max(pos_gap, 0),
        "status": "suspect" if flags else "clean",
        "flags": flags,
    }


def verify_gemini_model(project: str, location: str, model_name: str) -> str | None:
    """Verify model resolves in the target project/region. Returns model name or None."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        model_info = client.models.get(model=model_name)
        logger.info("Gemini model verified: %s", model_info.name)
        return model_name
    except Exception as e:
        logger.error("Gemini model verification failed for %s: %s", model_name, e)
        return None


def call_gemini_warehouse_narrative(
    facts: dict,
    integrity: dict,
    rules: dict,
    warehouse: str,
    project: str,
    location: str,
    model_name: str,
) -> str:
    """Call Gemini 3.5 Flash with guardrailed system instruction for per-warehouse narrative."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "# Analysis Narrative\n\n*Gemini model client not available (google-genai not installed).*"

    semantic_defs = json.dumps(rules.get("semantic_definitions", {}), indent=2)
    facts_json = json.dumps(facts, indent=2)
    integrity_json = json.dumps(integrity, indent=2)

    prompt = f"""You are a lead-to-POS match scoring analyst reviewing warehouse {warehouse}.

DETERMINISTIC FACTS:
{facts_json}

DATA INTEGRITY FLAGS:
{integrity_json}

SEMANTIC DEFINITIONS (authoritative):
{semantic_defs}

Write a concise markdown narrative covering:

1. **Distribution Interpretation**: Where the peak sits, what the shape tells you (normal, skewed, bimodal, flat). Note central tendency (mean/median) and spread (std).

2. **Post-Identification Signals** (4 signals):
   - **Threshold sensitivity**: Does the peak sit within 2 points of a cutoff (85 or 90)? If so, small score shifts move many rows between bands. Describe the operational impact.
   - **Tail quality**: How many rows in the 70-84.999 range? Thin tail = clean data; fat tail = edge quality concern.
   - **Score clustering**: Note where mass concentrates relative to cutoffs. Clustering near cutoffs is EXPECTED from the weighted (4*addr + 3*name)/7 formula — describe the operational impact but do NOT call it an artifact or bug.
   - **Review workload**: How many rows fall in Potential + Manual Review (70-89.999)? That is the human review queue.

3. **Lifecycle Split**: Closed-Match = Exact + Matching-High-fuzzy. Break this down. Never report combined Closed-Match without this split.

4. **Data Integrity**: If flags are present, surface them prominently and note the analysis may be affected.

5. **Recommended Actions**: Limited to "calibrate thresholds against a labeled validation set" and "audit embedding pipeline for flagged gaps." Never recommend changing the floor, bands, or formula.

Keep it concise and data-driven. Only write what the facts show."""

    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=GUARDRAIL_SYSTEM_INSTRUCTION,
                temperature=0.2,
            ),
        )
        text = getattr(response, "text", "") or str(response)
        if text.startswith("```"):
            text = text.split("```", 1)[1].rsplit("```", 1)[0].strip()
        return text or "*Gemini returned an empty response.*"
    except Exception as e:
        logger.error("Gemini warehouse narrative failed: %s", e)
        return f"*Gemini call failed: {e}*"


def build_analysis_md(
    warehouse: str,
    run_id: str,
    facts: dict,
    integrity: dict,
    narrative: str,
    generated_at: str,
) -> str:
    """Build the per-warehouse analysis.md document."""
    lines = [f"# Parallel Run Analysis — Warehouse {warehouse}"]
    lines.append("")
    lines.append(f"**Run:** `{run_id}`")
    lines.append(f"**Generated:** {generated_at}")
    lines.append("")

    # Data integrity — FIRST if suspect
    if integrity["status"] == "suspect":
        lines.append("---")
        lines.append("")
        lines.append("## DATA INTEGRITY WARNING")
        lines.append("")
        for flag in integrity["flags"]:
            lines.append(f"- {flag}")
        lines.append("")
        lines.append(
            "> This warehouse has un-embedded records beyond what exact-match exclusion "
            "explains. Score distributions below may be affected by missing embeddings. "
            "Investigate the embedding pipeline before trusting fuzzy match quality."
        )
        lines.append("")

    lines.append("---")
    lines.append("")

    # Counts
    c = facts["counts"]
    lines.append("## Match Counts")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Leads | {c['leads']:,} |")
    lines.append(f"| POS transactions | {c['pos']:,} |")
    lines.append(f"| Lead embeddings | {c['lead_embeddings']:,} |")
    lines.append(f"| POS embeddings | {c['pos_embeddings']:,} |")
    lines.append(f"| Total match rows | {c['total_rows']:,} |")
    lines.append(f"| Exact | {c['exact']:,} |")
    lines.append(f"| Fuzzy | {c['fuzzy']:,} |")
    lines.append(f"| Manual Review | {c['manual_review']:,} |")
    lines.append(f"| Primary transactions | {c['primary_transactions']:,} |")
    lines.append("")

    # Band counts
    b = facts["band_summary"]
    lines.append("## Confidence Bands (non-exact)")
    lines.append("")
    lines.append(f"| Band | Range | Count |")
    lines.append(f"|------|-------|-------|")
    lines.append(f"| Matching High | 90 – 99.999 | {b['matching_high_90_99']:,} |")
    lines.append(f"| Potential Medium | 85 – 89.999 | {b['potential_medium_85_89']:,} |")
    lines.append(f"| Potential Low | 70 – 84.999 | {b['potential_low_70_84']:,} |")
    lines.append("")

    # Lifecycle split
    ls = facts["lifecycle_split"]
    lines.append("## Lifecycle Split")
    lines.append("")
    lines.append(
        f"**Closed - Match** ({ls['closed_match_total']:,} total): "
        f"**{ls['closed_match_exact']:,} Exact** (proven) + "
        f"**{ls['closed_match_fuzzy_high']:,} Matching High fuzzy** (AI-inferred)"
    )
    lines.append(f"**Potential**: {ls['potential_total']:,}")
    lines.append("")

    # Score stats
    if facts["score_stats"]:
        s = facts["score_stats"]
        lines.append("## Score Statistics (non-exact)")
        lines.append("")
        lines.append(f"| Stat | Value |")
        lines.append(f"|------|-------|")
        lines.append(f"| Min | {s['min']} |")
        lines.append(f"| Max | {s['max']} |")
        lines.append(f"| Mean | {s['mean']} |")
        lines.append(f"| Median | {s['median']} |")
        lines.append(f"| Std Dev | {s['std']} |")
        lines.append(f"| Peak bin | {s.get('peak_bin', 'N/A')} ({s.get('peak_count', 0):,} rows) |")
        lines.append("")

    # Review workload
    lines.append("## Review Workload")
    lines.append("")
    lines.append(
        f"Rows in review queue (70 – 89.999): **{facts['review_workload_rows']:,}** "
        f"({facts['review_workload_pct']}% of all match rows)"
    )
    lines.append("")

    # Data integrity details
    lines.append("## Data Integrity Check")
    lines.append("")
    lines.append(f"| Metric | Leads | POS |")
    lines.append(f"|--------|-------|-----|")
    lines.append(f"| Total records | {c['leads']:,} | {c['pos']:,} |")
    lines.append(f"| Embedded | {c['lead_embeddings']:,} | {c['pos_embeddings']:,} |")
    lines.append(f"| Un-embedded (actual) | {integrity['actual_unembedded_leads']:,} | {integrity['actual_unembedded_pos']:,} |")
    lines.append(f"| Exact-claimed (expected skip) | {integrity['expected_unembedded_leads']:,} | {integrity['expected_unembedded_pos']:,} |")
    lines.append(f"| Unexplained gap | {integrity['lead_gap']:,} | {integrity['pos_gap']:,} |")
    lines.append(f"| Status | {'SUSPECT' if integrity['status'] == 'suspect' else 'Clean'} | {'SUSPECT' if integrity['status'] == 'suspect' else 'Clean'} |")
    lines.append("")

    # Gemini narrative
    lines.append("---")
    lines.append("")
    lines.append("## Distribution Analysis (Gemini 3.5 Flash)")
    lines.append("")
    lines.append(narrative)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3 — Project Rollup
# ---------------------------------------------------------------------------

def call_gemini_comparative(
    all_facts: dict[str, dict],
    rules: dict,
    project: str,
    location: str,
    model_name: str,
) -> str:
    """Call Gemini for cross-warehouse comparative narrative."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "*Gemini model client not available.*"

    compact = {}
    for wh, data in all_facts.items():
        compact[wh] = {
            "score_stats": data["facts"]["score_stats"],
            "band_summary": data["facts"]["band_summary"],
            "review_workload_pct": data["facts"]["review_workload_pct"],
            "lifecycle_split": data["facts"]["lifecycle_split"],
            "integrity_status": data["integrity"]["status"],
            "counts": data["facts"]["counts"],
        }

    prompt = f"""You are a lead-to-POS match scoring analyst comparing results across multiple warehouses.

PER-WAREHOUSE SUMMARY:
{json.dumps(compact, indent=2)}

Compare these warehouses and answer:
1. **Distribution comparison**: Do peaks/means sit in the same place across warehouses? Are shapes similar (all skewed the same way, or do some differ)?
2. **Review workload variation**: Does the review-queue percentage vary significantly? Which warehouse has the highest burden?
3. **Threshold portability**: Based on the score distributions, would one global threshold (the current 90/85/70 bands) work equally well for all warehouses, or does one warehouse's distribution suggest per-warehouse calibration is needed?
4. **Conclusion**: State clearly whether one global threshold fits the fleet or per-warehouse calibration is recommended, and why.

Be concise and data-driven. Do not repeat the raw numbers — interpret them."""

    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=GUARDRAIL_SYSTEM_INSTRUCTION,
                temperature=0.2,
            ),
        )
        text = getattr(response, "text", "") or str(response)
        if text.startswith("```"):
            text = text.split("```", 1)[1].rsplit("```", 1)[0].strip()
        return text or "*Gemini returned an empty response.*"
    except Exception as e:
        logger.error("Gemini comparative narrative failed: %s", e)
        return f"*Gemini call failed: {e}*"


def build_project_rollup(
    all_facts: dict[str, dict],
    comparative_narrative: str,
    generated_at: str,
) -> str:
    """Build the project-level rollup markdown."""
    lines = ["# Project Parallel Run Analysis"]
    lines.append("")
    lines.append(f"**Generated:** {generated_at}")
    lines.append("")

    # Surface integrity issues FIRST
    suspect_whs = [
        wh for wh, d in all_facts.items() if d["integrity"]["status"] == "suspect"
    ]
    if suspect_whs:
        lines.append("## DATA INTEGRITY ALERTS")
        lines.append("")
        for wh in suspect_whs:
            for flag in all_facts[wh]["integrity"]["flags"]:
                lines.append(f"- **Warehouse {wh}**: {flag}")
        lines.append("")
        lines.append(
            "> Warehouses flagged above have un-embedded records beyond exact-match exclusion. "
            "Their fuzzy match distributions may be incomplete."
        )
        lines.append("")

    lines.append("---")
    lines.append("")

    # Comparison table
    lines.append("## Warehouse Comparison")
    lines.append("")
    lines.append(
        "| Warehouse | Leads | POS | Exact | Fuzzy | Manual Review "
        "| Matching High | Potential Med | Potential Low | Peak Bin "
        "| Mean Score | Review Queue % | Data Integrity |"
    )
    lines.append(
        "|-----------|-------|-----|-------|-------|---------------"
        "|---------------|--------------|---------------|----------"
        "|------------|----------------|----------------|"
    )

    for wh in sorted(all_facts.keys()):
        d = all_facts[wh]
        c = d["facts"]["counts"]
        b = d["facts"]["band_summary"]
        s = d["facts"]["score_stats"]
        integrity_badge = "SUSPECT" if d["integrity"]["status"] == "suspect" else "Clean"
        peak = s.get("peak_bin", "N/A")
        mean = s.get("mean", "N/A")
        review_pct = d["facts"]["review_workload_pct"]

        lines.append(
            f"| {wh} | {c['leads']:,} | {c['pos']:,} | {c['exact']:,} | {c['fuzzy']:,} "
            f"| {c['manual_review']:,} | {b['matching_high_90_99']:,} "
            f"| {b['potential_medium_85_89']:,} | {b['potential_low_70_84']:,} "
            f"| {peak} | {mean} | {review_pct}% | {integrity_badge} |"
        )

    lines.append("")

    # Lifecycle split summary
    lines.append("## Lifecycle Split by Warehouse")
    lines.append("")
    lines.append(
        "| Warehouse | Closed-Match Total | Exact (proven) | Matching-High Fuzzy (AI) | Potential |"
    )
    lines.append(
        "|-----------|-------------------|----------------|--------------------------|-----------|"
    )
    for wh in sorted(all_facts.keys()):
        ls = all_facts[wh]["facts"]["lifecycle_split"]
        lines.append(
            f"| {wh} | {ls['closed_match_total']:,} | {ls['closed_match_exact']:,} "
            f"| {ls['closed_match_fuzzy_high']:,} | {ls['potential_total']:,} |"
        )
    lines.append("")

    # Fleet summary
    lines.append("## Fleet Summary")
    lines.append("")
    total_leads = sum(d["facts"]["counts"]["leads"] for d in all_facts.values())
    total_pos = sum(d["facts"]["counts"]["pos"] for d in all_facts.values())
    total_matches = sum(d["facts"]["counts"]["total_rows"] for d in all_facts.values())
    total_exact = sum(d["facts"]["counts"]["exact"] for d in all_facts.values())
    total_fuzzy = sum(
        d["facts"]["counts"]["fuzzy"] + d["facts"]["counts"]["manual_review"]
        for d in all_facts.values()
    )
    total_primary = sum(
        d["facts"]["counts"]["primary_transactions"] for d in all_facts.values()
    )

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total warehouses analyzed | {len(all_facts)} |")
    lines.append(f"| Total leads | {total_leads:,} |")
    lines.append(f"| Total POS transactions | {total_pos:,} |")
    lines.append(f"| Total match rows | {total_matches:,} |")
    lines.append(f"| Total Exact (proven) | {total_exact:,} |")
    lines.append(f"| Total AI-fuzzy (Fuzzy + Manual Review) | {total_fuzzy:,} |")
    lines.append(f"| Exact as % of matches | {round(100 * total_exact / total_matches, 1) if total_matches else 0}% |")
    lines.append(f"| Total primary transactions | {total_primary:,} |")
    lines.append("")

    # Gemini comparative narrative
    lines.append("---")
    lines.append("")
    lines.append("## Cross-Warehouse Comparative Analysis (Gemini 3.5 Flash)")
    lines.append("")
    lines.append(comparative_narrative)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_to_gcs(
    gcs_client: storage.Client,
    bucket_name: str,
    gcs_path: str,
    content: str,
    content_type: str = "text/markdown",
) -> None:
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(content, content_type=content_type)
    logger.info("Uploaded gs://%s/%s", bucket_name, gcs_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parallel run analysis: per-warehouse + project rollup"
    )
    parser.add_argument(
        "--bucket", default="lead-match-ctoteam",
    )
    parser.add_argument(
        "--project", default=os.getenv("PROJECT_ID", "ctoteam"),
    )
    parser.add_argument(
        "--warehouses",
        help="Comma-separated warehouse numbers (auto-discover if omitted)",
    )
    parser.add_argument(
        "--rules-json",
        default="lead_match_runtime/lead_to_pos_match_rules.json",
    )
    parser.add_argument(
        "--output-dir",
        help="Local directory for artifacts (uses temp dir if omitted)",
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Skip GCS upload (local-only mode for testing)",
    )
    args = parser.parse_args()

    gemini_project = (
        os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
        or args.project
    )
    gemini_location = os.getenv("VERTEX_LOCATION", "us-central1")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

    rules = load_rules_json(args.rules_json)
    gcs_client = storage.Client()
    generated_at = datetime.now(timezone.utc).isoformat()

    output_dir = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp())
    output_dir.mkdir(parents=True, exist_ok=True)

    wh_list = [w.strip() for w in args.warehouses.split(",")] if args.warehouses else None

    # --- Phase 1: Discover runs ---
    logger.info("Phase 1: Discovering warehouse runs...")
    runs = discover_warehouse_runs(gcs_client, args.bucket, args.project, wh_list)
    if not runs:
        logger.error("No valid warehouse runs found.")
        sys.exit(1)
    logger.info("Found %d warehouses: %s", len(runs), ", ".join(sorted(runs.keys())))

    # --- Verify Gemini model ---
    logger.info("Verifying Gemini model: %s in %s/%s", gemini_model, gemini_project, gemini_location)
    verified_model = verify_gemini_model(gemini_project, gemini_location, gemini_model)
    if not verified_model:
        logger.warning("Gemini model verification failed — narratives will be unavailable")

    # --- Phase 2: Per-warehouse analysis ---
    all_facts: dict[str, dict] = {}

    for wh in sorted(runs.keys()):
        run_info = runs[wh]
        run_id = run_info["run_id"]
        summary = run_info["summary"]
        prefix = run_info["prefix"]

        logger.info("Phase 2: Analyzing warehouse %s (run %s)...", wh, run_id)

        df = download_matches_csv(gcs_client, args.bucket, prefix)
        logger.info("  Loaded %d match rows", len(df))

        facts = compute_deterministic_facts(summary, df, rules)
        integrity = compute_data_integrity_flags(summary, df)

        if integrity["status"] == "suspect":
            for flag in integrity["flags"]:
                logger.warning("  DATA INTEGRITY: %s", flag)
        else:
            logger.info("  Data integrity: clean")

        # Gemini narrative
        if verified_model:
            narrative = call_gemini_warehouse_narrative(
                facts, integrity, rules, wh, gemini_project, gemini_location, verified_model,
            )
        else:
            narrative = "*Gemini model not available — narrative skipped.*"

        # Build analysis.md
        analysis_md = build_analysis_md(wh, run_id, facts, integrity, narrative, generated_at)

        # Build analysis_facts.json
        analysis_facts = {
            "warehouse": wh,
            "run_id": run_id,
            "generated_at": generated_at,
            "facts": facts,
            "data_integrity": integrity,
        }

        # Save locally
        wh_dir = output_dir / wh
        wh_dir.mkdir(parents=True, exist_ok=True)
        (wh_dir / "analysis.md").write_text(analysis_md)
        (wh_dir / "analysis_facts.json").write_text(json.dumps(analysis_facts, indent=2))

        # Upload to GCS
        if not args.skip_upload:
            upload_to_gcs(
                gcs_client, args.bucket, f"{prefix}analysis.md", analysis_md,
            )
            upload_to_gcs(
                gcs_client, args.bucket, f"{prefix}analysis_facts.json",
                json.dumps(analysis_facts, indent=2), "application/json",
            )

        all_facts[wh] = {"facts": facts, "integrity": integrity, "run_id": run_id}
        logger.info("  Warehouse %s complete", wh)

    # --- Phase 3: Project rollup ---
    logger.info("Phase 3: Building project rollup...")

    if verified_model:
        comparative = call_gemini_comparative(
            all_facts, rules, gemini_project, gemini_location, verified_model,
        )
    else:
        comparative = "*Gemini model not available — comparative narrative skipped.*"

    rollup_md = build_project_rollup(all_facts, comparative, generated_at)

    # Save locally
    project_dir = output_dir / "_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project_parallel_run_analysis.md").write_text(rollup_md)

    # Upload to GCS
    if not args.skip_upload:
        rollup_gcs_path = (
            f"reports/lead_match/{args.project}/_project/project_parallel_run_analysis.md"
        )
        upload_to_gcs(gcs_client, args.bucket, rollup_gcs_path, rollup_md)

    # GitHub step summary
    github_summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if github_summary_file:
        with open(github_summary_file, "a") as f:
            f.write(rollup_md)
        logger.info("Wrote project rollup to GITHUB_STEP_SUMMARY")

    logger.info("Analysis complete. Output: %s", output_dir)
    print(f"\nLocal output directory: {output_dir}")
    print(f"Warehouses analyzed: {', '.join(sorted(all_facts.keys()))}")
    for wh, d in sorted(all_facts.items()):
        status = d["integrity"]["status"].upper()
        print(f"  Warehouse {wh}: {d['run_id']} — integrity: {status}")


if __name__ == "__main__":
    main()
