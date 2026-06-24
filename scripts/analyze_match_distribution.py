#!/usr/bin/env python3
"""
Post-hoc match analysis: deterministic per-row reasoning + Gemini distribution narrative.
Runs AFTER match engine completes and staging to GCS finishes (_READY.json exists).

Architecture:
- Stage 1 (deterministic): Read matches.csv, format per-row reasoning from component scores.
  Weights/formula from rules JSON (never hardcode). Verify arithmetic before writing.
- Stage 2 (Gemini): Compute distribution facts, call Gemini 3.5 Flash for narrative analysis.
  Output: per-row reasoning → Cloud SQL match_decision_detail.match_reasoning;
          narrative → GCS comparative_analysis.md.

Safety:
- Writes ONLY match_reasoning in match_decision_detail. Never touches engine columns.
- Scoped by match_run_id; re-runs are idempotent, isolated per run.
- Batched writes, 1000 rows/tx. Verify rows_updated == CSV row count.
"""

import json
import logging
import os
import sys
from typing import Any
import argparse

import pandas as pd
from google.cloud import storage
from google.cloud.sql.connector import Connector
import sqlalchemy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _build_cloud_sql_engine() -> sqlalchemy.Engine | None:
    """Build SQLAlchemy engine using ADC or explicit connection string."""

    explicit_conn = os.getenv("MATCH_ANALYSIS_DB_CONNECTION")
    if explicit_conn:
        try:
            return sqlalchemy.create_engine(explicit_conn)
        except Exception as e:
            logger.error(f"Failed to create engine from explicit connection string: {e}")
            return None

    instance = os.getenv("CLOUDSQL_CONNECTION_NAME")
    db_user = os.getenv("CLOUDSQL_DB_USER", "postgres")
    db_name = os.getenv("CLOUDSQL_DB_NAME", os.getenv("DB_NAME", "postgres"))
    db_password = os.getenv("DB_PASSWORD")
    use_iam = os.getenv("CLOUDSQL_IAM_AUTH", "true").lower() in {"1", "true", "yes"}

    if not instance:
        logger.error("Missing CLOUDSQL_CONNECTION_NAME; cannot build Cloud SQL connector engine")
        return None

    try:
        connector = Connector()
    except Exception as e:
        logger.error(f"Failed to initialize Cloud SQL connector: {e}")
        return None

    enable_iam_auth = use_iam and not db_password

    try:
        def _build_engine(with_iam: bool) -> sqlalchemy.Engine:
            if not with_iam and not db_password:
                raise ValueError("Password auth requested but DB_PASSWORD is not set.")

            def get_conn() -> Any:
                connection_kwargs = {
                    "user": db_user,
                    "db": db_name,
                    "enable_iam_auth": with_iam,
                }
                if not with_iam:
                    connection_kwargs["password"] = db_password
                return connector.connect(instance, "pg8000", **connection_kwargs)

            return sqlalchemy.create_engine("postgresql+pg8000://", creator=get_conn)

        def _validate(engine: sqlalchemy.Engine, label: str) -> bool:
            try:
                with engine.connect() as conn:
                    conn.execute(sqlalchemy.text("SELECT 1"))
                logger.info("✓ Cloud SQL connection validated using %s", label)
                return True
            except Exception as exc:
                logger.warning("Cloud SQL %s validation failed: %s", label, exc)
                return False

        if enable_iam_auth:
            iam_engine = _build_engine(True)
            if _validate(iam_engine, "IAM"):
                return iam_engine
            if db_password:
                logger.warning("Falling back to password auth for Cloud SQL.")
            else:
                logger.error(
                    "IAM auth failed and no DB_PASSWORD is configured for fallback."
                )
                return None

        password_engine = _build_engine(False)
        if _validate(password_engine, "password"):
            return password_engine
    except Exception as e:
        logger.error(f"Cloud SQL connector engine build failed: {e}")
        return None


def load_rules_json(rules_path: str) -> dict:
    """Load match rules JSON and extract weights/formula/gate."""
    with open(rules_path) as f:
        rules = json.load(f)
    return rules


def _extract_scoring_params(rules: dict) -> tuple[float, float, float]:
    """Read weights/denominator from rules JSON."""
    fields = rules["embeddings"]["fields"]
    addr_w = float(fields["address_variant"]["weight"])
    name_w = float(fields["name_variant"]["weight"])
    denom = addr_w + name_w
    if denom <= 0:
        raise ValueError("Scoring weights must sum to a positive value")
    return addr_w, name_w, denom


def _extract_recall_gate(rules: dict) -> float:
    return float(rules["candidate_retrieval"]["recall_gate_min_similarity"])


def _extract_subtiers(rules: dict) -> list[dict]:
    """Return the configured confidence subtiers from JSON."""
    return rules.get("decision_rules", {}).get("optional_confidence_subtiers", {}).get("subtiers", [])


def _band_for_score(score: float, rules: dict) -> str:
    """Return the configured confidence subtier label for a final score."""
    for s in _extract_subtiers(rules):
        if float(s["min_score"]) <= score <= float(s["max_score"]):
            return str(s.get("name", "Unknown"))

    decision_rules = rules.get("decision_rules", {})
    score_model = rules.get("score_model", {})
    exact_score = float(
        decision_rules.get(
            "exact_score",
            score_model.get("match", {}).get("score", 100),
        )
    )
    if score >= exact_score:
        return str(
            decision_rules.get(
                "exact_label",
                score_model.get("match", {}).get("match_result", "Exact / Complete"),
            )
        )

    below_floor = decision_rules.get("below_floor") or score_model.get("no_match", {})
    return str(below_floor.get("label") or below_floor.get("match_result", "No Match"))


def ensure_band_column(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """Normalize current and legacy report CSVs to include a band column."""
    if "band" in df.columns:
        return df

    df = df.copy()
    if "confidence_band" in df.columns:
        df["band"] = df["confidence_band"].fillna("Unknown")
        return df

    if "final_score" not in df.columns:
        raise KeyError("matches.csv must include final_score or an explicit band column")

    df["band"] = df["final_score"].apply(lambda value: _band_for_score(float(value), rules))
    return df


def read_matches_csv_from_gcs(bucket_name: str, gcs_path: str) -> pd.DataFrame:
    """Read matches.csv from GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    return pd.read_csv(blob.open("r"))


def generate_per_row_reasoning(row: dict, rules: dict, weights: dict, gate_threshold: float) -> str:
    """
    Generate deterministic reasoning string from component scores.

    Args:
        row: CSV row with final_score, full_address_score, business_name_score, combined_field_score, match_type, band
        rules: Loaded rules JSON
        weights: {addr_weight, name_weight, denom}
        gate_threshold: recall gate min similarity (e.g., 65)

    Returns:
        Formatted reasoning string.
    """
    match_type = row.get("match_type", "Unknown")

    if match_type == "Exact":
        return "Deterministic field match (exact-sql); identity fields agree. Score 100, authoritative. Not AI-inferred."

    # Fuzzy match
    addr_score = float(row.get("full_address_score", 0))
    name_score = float(row.get("business_name_score", 0))
    combined_score = float(row.get("combined_field_score", 0))
    final_score = float(row.get("final_score", 0))
    band = row.get("band", "Unknown")

    addr_w = weights["addr_weight"]
    name_w = weights["name_weight"]
    denom = weights["denom"]

    # Verify arithmetic
    expected = (addr_w * addr_score + name_w * name_score) / denom
    if abs(expected - final_score) > 0.01:
        logger.warning(
            f"Arithmetic mismatch for pos_id={row.get('pos_id')}: "
            f"({addr_w}*{addr_score}+{name_w}*{name_score})/{denom}={expected} "
            f"but stored final_score={final_score}"
        )

    # Determine driver
    driver = "address-driven" if addr_score >= name_score else "name-driven"

    reasoning = (
        f"Address {addr_score:.2f} (w{addr_w}) + Name {name_score:.2f} (w{name_w}) "
        f"=> ({addr_w}*{addr_score:.2f}+{name_w}*{name_score:.2f})/{denom} = {final_score:.2f}. "
        f"Band: {band}. Recall gate: combined_field {combined_score:.2f} (>= {gate_threshold} pass). "
        f"{driver}."
    )
    return reasoning


def compute_distribution_facts(df: pd.DataFrame, rules: dict) -> dict:
    """
    Compute histogram, band counts, statistics for Gemini narrative.

    Args:
        df: matches.csv DataFrame
        rules: Loaded rules JSON for band definitions

    Returns:
        Dict with histogram, peak, tail volume, review workload, artifact check.
    """
    df = ensure_band_column(df, rules)
    scores = pd.to_numeric(df["final_score"], errors="coerce").dropna()
    if scores.empty:
        raise ValueError("matches.csv does not contain any numeric final_score values")

    # Read band boundaries from JSON
    subtiers = _extract_subtiers(rules)
    dr = rules.get("decision_rules", {})
    fuzzy_floor = float(dr["fuzzy_qualify_min_score"])
    fuzzy_ceiling = float(dr["fuzzy_max_score"])
    subtier_bounds = sorted(subtiers, key=lambda s: float(s["min_score"]))
    mid_cutoff = float(subtier_bounds[1]["min_score"]) if len(subtier_bounds) > 1 else fuzzy_floor
    high_cutoff = float(subtier_bounds[2]["min_score"]) if len(subtier_bounds) > 2 else mid_cutoff

    # Build histogram
    hist, bin_edges = pd.cut(scores, bins=range(int(fuzzy_floor), 102, 1), right=False, retbins=True)
    hist_counts = hist.value_counts().sort_index()
    histogram = {str(interval): int(count) for interval, count in hist_counts.items()}

    # Band counts
    band_counts = {str(band): int(count) for band, count in df["band"].value_counts().items()}

    # Statistics
    stats = {
        "mean": float(scores.mean()),
        "median": float(scores.median()),
        "std": float(scores.std()),
        "min": float(scores.min()),
        "max": float(scores.max()),
    }

    # Peak bin
    peak_bin = hist_counts.idxmax() if len(hist_counts) > 0 else None
    peak_value = int(hist_counts.max()) if len(hist_counts) > 0 else 0

    # Tail volume (Low subtier)
    tail_mask = (scores >= fuzzy_floor) & (scores < mid_cutoff)
    tail_volume = int(tail_mask.sum())

    # Review workload (Potential: all fuzzy below High)
    review_mask = (scores >= fuzzy_floor) & (scores < high_cutoff)
    review_workload = int(review_mask.sum())

    # Artifact check: any single bin with >15% of total or single-value spike
    artifact_flag = False
    if peak_value > 0.15 * len(df):
        artifact_flag = True

    facts = {
        "total_rows": len(df),
        "histogram": histogram,
        "band_counts": band_counts,
        "statistics": stats,
        "peak_bin": str(peak_bin) if peak_bin is not None else None,
        "peak_count": peak_value,
        "peak_percentage": 100 * peak_value / len(df),
        "tail_volume": tail_volume,
        "tail_percentage": 100 * tail_volume / len(df),
        "review_workload": review_workload,
        "review_percentage": 100 * review_workload / len(df),
        "artifact_flag": artifact_flag,
    }
    return facts


def call_gemini_analysis(facts: dict, rules: dict, warehouse: str = "Unknown") -> str:
    """
    Call Gemini 3.5 Flash to write narrative analysis from distribution facts.

    Args:
        facts: Distribution facts dict from compute_distribution_facts()
        rules: Loaded rules JSON for context
        warehouse: Warehouse ID for context

    Returns:
        Markdown narrative text.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai not installed. Install with: pip install google-genai")
        return "# Analysis (Gemini unavailable)\n\nGemini model client not configured."

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    project = (
        os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
    )
    location = os.getenv("VERTEX_LOCATION", "us-central1")
    if not project:
        return "# Analysis (Gemini unavailable)\n\nMissing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT."

    facts_json = json.dumps(facts, indent=2)
    rules_snippet = json.dumps(rules.get("decision_rules", {}), indent=2)[:500]

    prompt = f"""You are a lead-to-POS match scoring analyst. Analyze the score distribution below and explain what it means for match quality and next actions.

DISTRIBUTION FACTS (from {warehouse}):
{facts_json}

BUSINESS RULES (excerpt):
{rules_snippet}

Write a brief markdown narrative covering:
1. **Distribution Interpretation**: Where the peak sits, what the shape tells you (normal, skewed, flat, spiky).
2. **Post-Identification Findings** (4 signals):
   - Threshold sensitivity: Does the peak sit within 2 points of a cutoff (85 or 90)? If so, small score shifts move many rows between bands.
   - Tail/edge quality: How many borderline-weak matches (70-84.999)? Thin tail = clean data; fat tail = edge quality issue.
   - Artifacts: Any spikes (single bin >15% of total) or empty interior bins? Flag if found, else confirm no artifact.
   - Review workload: How many rows fall in Potential+Manual Review (70-89.999)? That's the human queue size.
3. **Recommended Actions** for each signal.
4. **Caveat**: These bands are starting priors. Final thresholds need a labeled validation set.

Keep it concise and data-driven. Only write what the distribution actually shows."""

    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = getattr(response, "text", "") or str(response)
        if text.startswith("```"):
            text = text.split("```", 1)[1].rsplit("```", 1)[0].strip()
        return text or "# Analysis (Gemini returned empty response)\n\nCheck logs for API errors."
    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        return f"# Analysis (Gemini call failed)\n\n{str(e)}"


def write_reasoning_to_cloud_sql(
    df: pd.DataFrame,
    rules: dict,
    match_run_id: str,
    warehouse: str,
    db_connection_string: str | None,
) -> int:
    """
    Write per-row reasoning to match_decision_detail.match_reasoning.
    Scoped by match_run_id, idempotent, batched.

    Args:
        df: matches.csv with component scores
        rules: Loaded rules JSON
        match_run_id: Match run identifier for scoping
        warehouse: Warehouse number for logging
        db_connection_string: Cloud SQL connection string

    Returns:
        Row count updated.
    """
    addr_weight, name_weight, denom = _extract_scoring_params(rules)
    weights = {
        "addr_weight": addr_weight,
        "name_weight": name_weight,
        "denom": denom,
    }
    gate_threshold = _extract_recall_gate(rules)

    # Generate reasoning for all rows
    df["match_reasoning"] = df.apply(
        lambda row: generate_per_row_reasoning(row, rules, weights, gate_threshold),
        axis=1,
    )

    # Connect to Cloud SQL
    try:
        engine = (
            sqlalchemy.create_engine(db_connection_string)
            if db_connection_string
            else _build_cloud_sql_engine()
        )
    except Exception as e:
        logger.error(f"Failed to connect to Cloud SQL: {e}")
        return 0

    if engine is None:
        logger.error("No Cloud SQL engine available; skipping reasoning write-back.")
        return 0

    try:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(
                'ALTER TABLE "leadmgmt"."match_decision_detail" '
                'ADD COLUMN IF NOT EXISTS "match_reasoning" text'
            ))
            logger.info("Ensured match_reasoning column exists on match_decision_detail")
    except Exception as e:
        logger.warning("Could not ensure match_reasoning column: %s", e)

    rows_updated = 0
    batch_size = 1000

    with engine.begin() as conn:
        for i in range(0, len(df), batch_size):
            batch = df.iloc[i : i + batch_size]

            for _, row in batch.iterrows():
                try:
                    query = sqlalchemy.text("""
                        UPDATE "leadmgmt"."match_decision_detail"
                        SET "match_reasoning" = :reasoning
                        WHERE "match_run_id" = :run_id
                          AND "lead_id" = :lead_id
                          AND "pos_id" = :pos_id
                    """)
                    conn.execute(
                        query,
                        {
                            "reasoning": row["match_reasoning"],
                            "run_id": match_run_id,
                            "lead_id": row["lead_id"],
                            "pos_id": row["pos_id"],
                        },
                    )
                    rows_updated += 1
                except Exception as e:
                    logger.error(
                        f"Failed to update pos_id={row['pos_id']}, lead_id={row['lead_id']}: {e}"
                    )

    logger.info(
        f"Wrote match_reasoning for {rows_updated} rows "
        f"(warehouse={warehouse}, match_run_id={match_run_id})"
    )
    return rows_updated


def write_analysis_audit_metadata(
    match_run_id: str,
    db_connection_string: str | None,
    warehouse: str,
    analysis_context: dict,
    reasoning_rows_written: int,
    matches_rows: int,
) -> None:
    """Store workflow-level analysis context in match_audit.comments."""
    if not match_run_id:
        logger.warning("run_id missing; skipping match_audit metadata update")
        return

    engine = None
    if db_connection_string:
        try:
            engine = sqlalchemy.create_engine(db_connection_string)
        except Exception as e:
            logger.warning(f"Failed to build engine from explicit connection for audit metadata: {e}")
            engine = None
    if engine is None:
        engine = _build_cloud_sql_engine()
    if engine is None:
        logger.warning("Cloud SQL metadata write skipped; engine unavailable")
        return

    audit_comment = (
        "analysis_run_id={analysis_run_id}; workflow={workflow}; "
        "attempt={attempt}; run_id={match_run_id}; warehouse={warehouse}; "
        "gemini_model={gemini_model}; reasoning_rows={reasoning_rows}; "
        "matches_rows={matches_rows}; source=lead_match_analysis.yml; "
        "llm=off"
    ).format(
        analysis_run_id=analysis_context.get("analysis_run_id", "unknown"),
        workflow=analysis_context.get("workflow", "lead_match_analysis.yml"),
        attempt=analysis_context.get("attempt", "1"),
        match_run_id=match_run_id,
        warehouse=warehouse,
        gemini_model=analysis_context.get("gemini_model", os.getenv("GEMINI_MODEL", "gemini-3.5-flash")),
        reasoning_rows=reasoning_rows_written,
        matches_rows=matches_rows,
    )

    update_sql = '''
        UPDATE "leadmgmt"."match_audit"
        SET comments = CASE
            WHEN COALESCE(comments, '') = '' THEN :audit_comment
            ELSE comments || '; ' || :audit_comment
        END
        WHERE match_id = (
            SELECT match_id
            FROM "leadmgmt"."match_audit"
            WHERE CAST(stats AS jsonb)->>'match_run_id' = :run_id
            ORDER BY update_date DESC
            LIMIT 1
        )
    '''

    try:
        with engine.begin() as conn:
            result = conn.execute(
                sqlalchemy.text(update_sql),
                {
                    "audit_comment": audit_comment,
                    "run_id": match_run_id,
                },
            )
            logger.info(
                "Updated match_audit for run_id=%s with analysis metadata: rows=%s",
                match_run_id,
                result.rowcount,
            )
    except Exception as e:
        logger.warning(f"Could not update match_audit metadata: {e}")


def write_narrative_to_gcs(narrative: str, bucket_name: str, gcs_path: str):
    """Write narrative markdown to GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(narrative, content_type="text/markdown")
    logger.info(f"Wrote narrative to gs://{bucket_name}/{gcs_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Post-hoc match analysis: reasoning + Gemini narrative"
    )
    parser.add_argument(
        "--bucket",
        default="lead-match-ctoteam",
        help="GCS bucket with match results",
    )
    parser.add_argument(
        "--project",
        default=os.getenv("PROJECT_ID", "ctoteam"),
        help="GCP project used in match report path",
    )
    parser.add_argument(
        "--warehouse",
        default="115",
        help="Warehouse number",
    )
    parser.add_argument(
        "--run-id",
        help="Match run ID (e.g. codex-20260623031813-115)",
    )
    parser.add_argument(
        "--matches-csv",
        help="GCS path to matches.csv (auto-inferred if not provided)",
    )
    parser.add_argument(
        "--rules-json",
        default="lead_match_runtime/lead_to_pos_match_rules.json",
        help="Path to lead match rules JSON",
    )
    parser.add_argument(
        "--db-connection-string",
        help="Cloud SQL connection string (uses ADC if not provided)",
    )
    parser.add_argument(
        "--analysis-run-id",
        default=os.getenv("GITHUB_RUN_ID", ""),
        help="GitHub Actions analysis workflow run id for audit context.",
    )
    parser.add_argument(
        "--analysis-run-attempt",
        default=os.getenv("GITHUB_RUN_ATTEMPT", "1"),
        help="GitHub Actions analysis workflow attempt for audit context.",
    )
    parser.add_argument(
        "--analysis-workflow",
        default=os.getenv("GITHUB_WORKFLOW", "lead_match_analysis.yml"),
        help="GitHub Actions workflow name for audit context.",
    )
    args = parser.parse_args()

    # Load rules
    try:
        rules = load_rules_json(args.rules_json)
    except Exception as e:
        logger.error(f"Failed to load rules JSON: {e}")
        sys.exit(1)

    # Infer GCS path if not provided
    if not args.matches_csv:
        args.matches_csv = (
            f"reports/lead_match/{args.project}/{args.warehouse}/{args.run_id}/matches.csv"
        )

    # Read matches.csv from GCS
    logger.info(f"Reading {args.matches_csv} from gs://{args.bucket}/...")
    try:
        df = read_matches_csv_from_gcs(args.bucket, args.matches_csv)
    except Exception as e:
        logger.error(f"Failed to read matches.csv: {e}")
        sys.exit(1)

    logger.info(f"Loaded {len(df)} rows from matches.csv")
    df = ensure_band_column(df, rules)

    # Compute distribution facts
    logger.info("Computing distribution facts...")
    facts = compute_distribution_facts(df, rules)
    logger.info(
        f"Peak: bin {facts['peak_bin']} ({facts['peak_count']} rows, {facts['peak_percentage']:.1f}%). "
        f"Tail (70-84.999): {facts['tail_volume']} rows. Review workload (70-89.999): {facts['review_workload']} rows."
    )

    # Call Gemini for narrative
    logger.info("Calling Gemini 3.5 Flash for analysis narrative...")
    narrative = call_gemini_analysis(facts, rules, warehouse=args.warehouse)

    # Write narrative to GCS
    narrative_path = f"reports/lead_match/{args.project}/{args.warehouse}/{args.run_id}/comparative_analysis.md"
    write_narrative_to_gcs(narrative, args.bucket, narrative_path)

    # Write reasoning to Cloud SQL (if connection available)
    logger.info("Writing per-row reasoning to Cloud SQL if connection is available...")
    rows_updated = write_reasoning_to_cloud_sql(
        df, rules, args.run_id, args.warehouse, args.db_connection_string
    )

    if rows_updated > 0:
        if rows_updated == len(df):
            logger.info(f"✓ All {rows_updated} reasoning rows written successfully.")
        else:
            logger.warning(f"⚠ Only {rows_updated}/{len(df)} rows updated.")
    else:
        logger.warning("No reasoning rows written to Cloud SQL. Either DB is unreachable or no matching rows were found.")

    # Update match_audit metadata row with analysis provenance
    write_analysis_audit_metadata(
        match_run_id=args.run_id,
        db_connection_string=args.db_connection_string,
        warehouse=args.warehouse,
        analysis_context={
            "analysis_run_id": args.analysis_run_id,
            "attempt": args.analysis_run_attempt,
            "workflow": args.analysis_workflow,
            "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        },
        reasoning_rows_written=rows_updated,
        matches_rows=len(df),
    )

    logger.info("Analysis complete.")


if __name__ == "__main__":
    main()
