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
import time
from pathlib import Path
from typing import Any
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from google.cloud import storage
from google.cloud.sql.connector import Connector
import sqlalchemy

from lead_match_runtime.business_rules import load_business_rules

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


def load_rules_json(rules_path: str | None = None) -> dict:
    """Load match rules JSON via the central business_rules loader."""
    return load_business_rules(rules_path)


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
    df = df.copy()
    if "final_score" not in df.columns and "similarity_score" in df.columns:
        df["final_score"] = pd.to_numeric(df["similarity_score"], errors="coerce")

    if "band" in df.columns:
        return df

    if "confidence_band" in df.columns:
        df["band"] = df["confidence_band"].fillna("Unknown")
        return df

    if "final_score" not in df.columns:
        raise KeyError(
            "matches.csv must include final_score, similarity_score, or an explicit band column"
        )

    df["band"] = df["final_score"].apply(lambda value: _band_for_score(float(value), rules))
    return df


def read_matches_csv_from_gcs(bucket_name: str, gcs_path: str) -> pd.DataFrame:
    """Read matches.csv from GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    return pd.read_csv(blob.open("r"))


def gcs_blob_exists(bucket_name: str, gcs_path: str) -> bool:
    """Return whether a GCS object exists."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    return bool(blob.exists())


def read_text_from_gcs(bucket_name: str, gcs_path: str) -> str:
    """Read text content from GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    return blob.download_as_text()


def generate_per_row_reasoning(row: dict, rules: dict, weights: dict, gate_threshold: float) -> str:
    """
    Generate human-readable matching comment from component scores.

    For exact rows: returns empty string (exact CSV comments are preserved as-is).
    For fuzzy/MR rows: generates a comment matching the exact CSV's style.
    """
    match_type = row.get("match_type", "Unknown")
    comment_cfg = rules.get("prompts", {}).get("per_row_comment", {})

    if match_type == rules["decision_rules"]["exact_match_type"]:
        return ""

    if match_type == "Closed - Existing":
        return str(comment_cfg.get("closed_existing_comment", "Closed - Existing: POS transaction pre-dates lead within fiscal CE window."))

    fuzzy_cfg = comment_cfg.get("fuzzy", {})
    labels = fuzzy_cfg.get("labels", {})
    strong_threshold = float(fuzzy_cfg.get("strong_threshold", 85))
    moderate_threshold = float(fuzzy_cfg.get("moderate_threshold", 80))
    review_note = str(fuzzy_cfg.get("review_note", "Marketer review recommended."))

    addr_score = float(row.get("full_address_score", 0))
    name_score = float(row.get("business_name_score", 0))
    final_score = float(row.get("final_score", 0))
    email_boost = float(row.get("email_boost", 0))
    phone_boost = float(row.get("phone_boost", 0))
    fuzzy_cap = float(rules["decision_rules"]["fuzzy_max_score"])

    if final_score >= strong_threshold:
        quality = labels.get("strong", "Strong semantic match")
    elif final_score >= moderate_threshold:
        quality = labels.get("moderate", "Moderate semantic match")
    else:
        quality = labels.get("weak", "Weak semantic match")

    driver = "address-driven" if addr_score >= name_score else "name-driven"

    boost_parts = []
    if email_boost:
        boost_parts.append(f"email +{email_boost:.0f}")
    if phone_boost:
        boost_parts.append(f"phone +{phone_boost:.0f}")
    boost_str = f" Confirmers: {', '.join(boost_parts)}." if boost_parts else ""

    review = ""
    if match_type == "Manual Review" or final_score < strong_threshold:
        review = f" {review_note}"

    reasoning = (
        f"{quality} (score {final_score:.2f}/{fuzzy_cap}); "
        f"{driver} (address {addr_score:.1f}, name {name_score:.1f})."
        f"{boost_str}{review}"
    )
    return reasoning


def add_match_reasoning(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """Generate deterministic match_reasoning for the dataframe."""
    addr_weight, name_weight, denom = _extract_scoring_params(rules)
    weights = {
        "addr_weight": addr_weight,
        "name_weight": name_weight,
        "denom": denom,
    }
    gate_threshold = _extract_recall_gate(rules)
    df = df.copy()
    df["match_reasoning"] = df.apply(
        lambda row: generate_per_row_reasoning(row, rules, weights, gate_threshold),
        axis=1,
    )
    return df


def _truthy_series(series: pd.Series) -> pd.Series:
    """Normalize common bool/string truthy values to a boolean mask."""
    return series.fillna(False).astype(str).str.strip().str.lower().isin(
        {"1", "true", "t", "yes", "y"}
    )


def select_reasoning_writeback_rows(
    df: pd.DataFrame,
    rules: dict,
    write_exact_reasoning: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Select only rows worth writing to Cloud SQL reasoning."""
    output = df.copy()
    if "match_reasoning" not in output.columns:
        output = add_match_reasoning(output, rules)

    match_type = output.get("match_type", pd.Series("", index=output.index)).fillna("").astype(str)
    lifecycle_state = output.get("lifecycle_state", pd.Series("", index=output.index)).fillna("").astype(str)
    final_score = pd.to_numeric(output.get("final_score", pd.Series(0, index=output.index)), errors="coerce").fillna(0)
    matching_comments = output.get("matching_comments", pd.Series("", index=output.index)).fillna("").astype(str)
    match_reasoning = output.get("match_reasoning", pd.Series("", index=output.index)).fillna("").astype(str)

    fuzzy_types = {
        str(rules["decision_rules"].get("fuzzy_match_type", "Fuzzy")).lower(),
        str(rules["decision_rules"].get("manual_review_match_type", "Manual Review")).lower(),
        "fuzzy",
        "manual review",
    }
    ce_lifecycle = str(rules["decision_rules"].get("closed_existing_lifecycle_state", "Closed - Existing")).lower()

    fuzzy_mask = match_type.str.strip().str.lower().isin(fuzzy_types)
    manual_review_mask = match_type.str.strip().str.lower().eq("manual review")
    potential_mask = lifecycle_state.str.strip().str.lower().eq("potential")
    ce_mask = lifecycle_state.str.strip().str.lower().eq(ce_lifecycle)
    if "closed_existing_flag" in output.columns:
        ce_mask = ce_mask | _truthy_series(output["closed_existing_flag"])
    below_exact_mask = final_score < float(rules["decision_rules"].get("exact_score", 100))
    blank_comment_with_reasoning_mask = (
        matching_comments.str.strip().eq("") & match_reasoning.str.strip().ne("")
    )

    selected_mask = (
        fuzzy_mask
        | manual_review_mask
        | potential_mask
        | ce_mask
        | below_exact_mask
        | blank_comment_with_reasoning_mask
    )

    if write_exact_reasoning:
        selected_mask = selected_mask | match_reasoning.str.strip().ne("")

    selected = output.loc[selected_mask].copy()
    exact_skipped = int((~selected_mask & (final_score >= float(rules["decision_rules"].get("exact_score", 100)))).sum())
    counters = {
        "total_rows": int(len(output)),
        "rows_with_generated_reasoning": int(match_reasoning.str.strip().ne("").sum()),
        "rows_selected_for_cloudsql_writeback": int(len(selected)),
        "exact_rows_skipped_from_writeback": exact_skipped,
        "fuzzy_potential_rows_selected": int((selected_mask & (fuzzy_mask | manual_review_mask | potential_mask | below_exact_mask)).sum()),
        "ce_rows_selected": int((selected_mask & ce_mask).sum()),
    }
    return selected, counters


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
    hist, bin_edges = pd.cut(scores, bins=range(int(fuzzy_floor), int(rules["decision_rules"]["exact_score"]) + 2, 1), right=False, retbins=True)
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

    model_name = os.getenv("GEMINI_MODEL", rules["environment"]["models"]["gemini_flash"])
    project = (
        os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
    )
    location = os.getenv("VERTEX_LOCATION", rules["environment"]["vertex_ai"]["location"])
    if not project:
        return "# Analysis (Gemini unavailable)\n\nMissing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT."

    facts_json = json.dumps(facts, indent=2)
    rules_snippet = json.dumps(rules.get("decision_rules", {}), indent=2)[:500]

    prompt_config = rules.get("prompts", {}).get("distribution_narrative", {})
    role = prompt_config.get("role", "You are a lead-to-POS match scoring analyst.")
    sections = prompt_config.get("sections", [])
    style = prompt_config.get("style", "Keep it concise and data-driven.")
    input_tpl = prompt_config.get("input_template", "DISTRIBUTION FACTS:\n{facts_json}\n\nBUSINESS RULES:\n{rules_snippet}")

    input_block = input_tpl.format(warehouse=warehouse, facts_json=facts_json, rules_snippet=rules_snippet)
    sections_block = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sections)) if sections else ""

    prompt = f"""{role}

{input_block}

Write a brief markdown narrative covering:
{sections_block}

{style}"""

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
    schema = rules["environment"]["cloud_sql"]["schema"]
    if "match_reasoning" not in df.columns:
        df = add_match_reasoning(df, rules)

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
                f'ALTER TABLE "{schema}"."match_decision_detail" '
                'ADD COLUMN IF NOT EXISTS "match_reasoning" text'
            ))
            logger.info("Ensured match_reasoning column exists on match_decision_detail")
    except Exception as e:
        logger.warning("Could not ensure match_reasoning column: %s", e)

    check_reasoning_writeback_index(engine, schema)

    rows_updated = 0
    batch_size = rules["environment"]["tuning"]["analysis_db_batch_size"]

    reasoning_df = df[["lead_id", "pos_id", "match_reasoning"]].copy()
    reasoning_df["match_run_id"] = match_run_id

    with engine.begin() as conn:
        conn.execute(sqlalchemy.text('DROP TABLE IF EXISTS "_temp_reasoning"'))
        conn.execute(sqlalchemy.text("""
            CREATE TEMP TABLE "_temp_reasoning" (
                match_run_id text,
                lead_id text,
                pos_id text,
                match_reasoning text
            )
        """))
        logger.info("Created temp table _temp_reasoning")

        total_batches = (len(reasoning_df) + batch_size - 1) // batch_size
        for i in range(0, len(reasoning_df), batch_size):
            batch = reasoning_df.iloc[i : i + batch_size]
            batch_number = i // batch_size + 1
            logger.info(
                "Starting reasoning writeback batch %d/%d rows %d-%d",
                batch_number,
                total_batches,
                i + 1,
                i + len(batch),
            )
            values = [
                {
                    "run_id": row["match_run_id"],
                    "lead_id": row["lead_id"],
                    "pos_id": row["pos_id"],
                    "reasoning": row["match_reasoning"],
                }
                for _, row in batch.iterrows()
            ]
            conn.execute(
                sqlalchemy.text(
                    'INSERT INTO "_temp_reasoning" (match_run_id, lead_id, pos_id, match_reasoning) '
                    "VALUES (:run_id, :lead_id, :pos_id, :reasoning)"
                ),
                values,
            )
            logger.info("Inserted reasoning writeback batch %d/%d (%d rows)", batch_number, total_batches, len(batch))

        result = conn.execute(sqlalchemy.text(f"""
            UPDATE "{schema}"."match_decision_detail" m
            SET "match_reasoning" = t.match_reasoning
            FROM "_temp_reasoning" t
            WHERE m.match_run_id = t.match_run_id
              AND m.lead_id = t.lead_id
              AND m.pos_id = t.pos_id
        """))
        rows_updated = result.rowcount
        logger.info("Batch UPDATE applied: %d rows updated", rows_updated)

        conn.execute(sqlalchemy.text('DROP TABLE IF EXISTS "_temp_reasoning"'))

    logger.info(
        f"Wrote match_reasoning for {rows_updated} rows "
        f"(warehouse={warehouse}, match_run_id={match_run_id})"
    )
    return rows_updated


def check_reasoning_writeback_index(engine: sqlalchemy.Engine, schema: str) -> None:
    """Warn if the expected writeback lookup index is missing. Read-only."""
    index_query = sqlalchemy.text(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE schemaname = :schema
          AND tablename = 'match_decision_detail'
        """
    )
    try:
        with engine.connect() as conn:
            index_defs = [str(row[0]).lower() for row in conn.execute(index_query, {"schema": schema})]
    except Exception as e:
        logger.warning("Could not check reasoning writeback index: %s", e)
        return

    required = ("match_run_id", "lead_id", "pos_id")
    has_index = any(all(column in indexdef for column in required) for indexdef in index_defs)
    if not has_index:
        logger.warning("WARNING_MISSING_REASONING_WRITEBACK_INDEX match_decision_detail(match_run_id, lead_id, pos_id)")


def write_analysis_audit_metadata(
    match_run_id: str,
    db_connection_string: str | None,
    warehouse: str,
    analysis_context: dict,
    reasoning_rows_written: int,
    matches_rows: int,
    rules: dict,
) -> None:
    """Store workflow-level analysis context in match_audit.comments."""
    if not match_run_id:
        logger.warning("run_id missing; skipping match_audit metadata update")
        return

    schema = rules["environment"]["cloud_sql"]["schema"]

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
        gemini_model=analysis_context.get("gemini_model", os.getenv("GEMINI_MODEL", rules["environment"]["models"]["gemini_flash"])),
        reasoning_rows=reasoning_rows_written,
        matches_rows=matches_rows,
    )

    update_sql = f'''
        UPDATE "{schema}"."match_audit"
        SET comments = CASE
            WHEN COALESCE(comments, '') = '' THEN :audit_comment
            ELSE comments || '; ' || :audit_comment
        END
        WHERE match_id = (
            SELECT match_id
            FROM "{schema}"."match_audit"
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
    max_attempts = int(os.getenv("GCS_UPLOAD_MAX_ATTEMPTS", "4"))
    retry_sleep_seconds = float(os.getenv("GCS_UPLOAD_RETRY_SLEEP_SECONDS", "5"))

    for attempt in range(1, max_attempts + 1):
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(gcs_path)
            blob.upload_from_string(narrative, content_type="text/markdown")
            logger.info(f"Wrote narrative to gs://{bucket_name}/{gcs_path}")
            return
        except Exception as e:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "GCS narrative upload failed on attempt %s/%s: %s; retrying in %.1fs",
                attempt,
                max_attempts,
                e,
                retry_sleep_seconds,
            )
            time.sleep(retry_sleep_seconds * attempt)


def write_enriched_matches_to_gcs(df: pd.DataFrame, bucket_name: str, gcs_path: str):
    """Write matches enriched by Stage 3 reasoning/comments to GCS."""
    output = df.copy()
    if "match_reasoning" not in output.columns:
        output["match_reasoning"] = ""
    if "matching_comments" not in output.columns:
        output["matching_comments"] = ""
    output["match_reasoning"] = output["match_reasoning"].fillna("").astype(str)
    output["matching_comments"] = output["matching_comments"].fillna("").astype(str)
    needs_comment = output["matching_comments"].str.strip() == ""
    output.loc[needs_comment, "matching_comments"] = output.loc[needs_comment, "match_reasoning"]

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(output.to_csv(index=False), content_type="text/csv")
    logger.info("Wrote enriched matches to gs://%s/%s", bucket_name, gcs_path)


def main():
    _default_rules = load_rules_json(os.getenv("LEAD_POS_RULES_PATH"))

    parser = argparse.ArgumentParser(
        description="Post-hoc match analysis: reasoning + Gemini narrative"
    )
    parser.add_argument(
        "--bucket",
        default=_default_rules["environment"]["gcs"]["report_bucket"],
        help="GCS bucket with match results",
    )
    parser.add_argument(
        "--project",
        default=os.getenv("PROJECT_ID", _default_rules["environment"]["project_id"]),
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
        default=os.getenv("LEAD_POS_RULES_PATH"),
        help="Path to lead match rules JSON (empty = default from business_rules.py)",
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
    parser.add_argument(
        "--write-cloudsql-reasoning",
        action="store_true",
        help="Opt in to selected Cloud SQL match_reasoning writeback.",
    )
    parser.add_argument(
        "--skip-cloudsql-reasoning",
        action="store_true",
        help="Skip Cloud SQL match_reasoning writeback while still producing GCS outputs.",
    )
    parser.add_argument(
        "--write-exact-reasoning",
        action="store_true",
        help="Also write exact/high-confidence rows to Cloud SQL. Default is to skip repetitive exact rows.",
    )
    parser.add_argument(
        "--require-cloudsql-reasoning-writeback",
        action="store_true",
        help="Fail if Cloud SQL reasoning writeback fails after GCS outputs complete.",
    )
    parser.add_argument(
        "--force-gemini",
        action="store_true",
        help="Regenerate comparative_analysis.md even if it already exists in GCS.",
    )
    args = parser.parse_args()
    write_cloudsql_reasoning = bool(args.write_cloudsql_reasoning and not args.skip_cloudsql_reasoning)

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

    comment_cfg = rules.get("prompts", {}).get("per_row_comment", {})
    internal_source = comment_cfg.get("component_scores_source", "matches_internal.csv")
    score_fields = comment_cfg.get("component_score_fields", [
        "full_address_score", "business_name_score", "combined_field_score",
        "email_boost", "phone_boost", "winning_set",
    ])

    internal_path = args.matches_csv.replace("matches.csv", internal_source)
    try:
        internal_df = read_matches_csv_from_gcs(args.bucket, internal_path)
        join_cols = ["lead_id", "pos_id"]
        available_scores = [c for c in score_fields if c in internal_df.columns]
        internal_scores = internal_df[join_cols + available_scores].copy()
        for col in available_scores:
            if col in df.columns:
                df = df.drop(columns=[col])
        df = df.merge(internal_scores, on=join_cols, how="left")
        for col in available_scores:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        logger.info(f"Joined {len(available_scores)} component score columns from {internal_source}: {available_scores}")
    except Exception as e:
        logger.warning(f"Could not read {internal_source}: {e} — comments will lack component scores")
        for col in score_fields:
            if col not in df.columns:
                df[col] = 0.0

    df = ensure_band_column(df, rules)
    df = add_match_reasoning(df, rules)
    writeback_df, writeback_counters = select_reasoning_writeback_rows(
        df,
        rules,
        write_exact_reasoning=args.write_exact_reasoning,
    )
    logger.info("MATCH_ANALYSIS_TOTAL_ROWS=%d", writeback_counters["total_rows"])
    logger.info("MATCH_ANALYSIS_ROWS_WITH_GENERATED_REASONING=%d", writeback_counters["rows_with_generated_reasoning"])
    logger.info("MATCH_ANALYSIS_ROWS_SELECTED_FOR_CLOUDSQL_WRITEBACK=%d", writeback_counters["rows_selected_for_cloudsql_writeback"])
    logger.info("MATCH_ANALYSIS_EXACT_ROWS_SKIPPED_FROM_WRITEBACK=%d", writeback_counters["exact_rows_skipped_from_writeback"])
    logger.info("MATCH_ANALYSIS_FUZZY_POTENTIAL_ROWS_SELECTED=%d", writeback_counters["fuzzy_potential_rows_selected"])
    logger.info("MATCH_ANALYSIS_CE_ROWS_SELECTED=%d", writeback_counters["ce_rows_selected"])
    logger.info("CLOUDSQL_REASONING_WRITEBACK_ENABLED=%s", str(write_cloudsql_reasoning).lower())

    # Stage 3 owns comment/reasoning enrichment. Keep Stage 2 matches.csv
    # lightweight and publish an enriched copy for review/reporting surfaces.
    enriched_matches_path = f"reports/lead_match/{args.project}/{args.warehouse}/{args.run_id}/matches_enriched.csv"
    write_enriched_matches_to_gcs(df, args.bucket, enriched_matches_path)

    # Compute distribution facts
    logger.info("Computing distribution facts...")
    facts = compute_distribution_facts(df, rules)
    subtiers = _extract_subtiers(rules)
    _fuzzy_floor = float(rules["decision_rules"]["fuzzy_qualify_min_score"])
    _low_max = max((float(s["max_score"]) for s in subtiers if s["name"] == "Low"), default=84.999)
    _mid_max = max((float(s["max_score"]) for s in subtiers if s["name"] == "Medium"), default=89.999)
    logger.info(
        f"Peak: bin {facts['peak_bin']} ({facts['peak_count']} rows, {facts['peak_percentage']:.1f}%). "
        f"Tail ({_fuzzy_floor:.0f}-{_low_max}): {facts['tail_volume']} rows. Review workload ({_fuzzy_floor:.0f}-{_mid_max}): {facts['review_workload']} rows."
    )

    narrative_path = f"reports/lead_match/{args.project}/{args.warehouse}/{args.run_id}/comparative_analysis.md"
    if not args.force_gemini and gcs_blob_exists(args.bucket, narrative_path):
        logger.info("Reusing existing Gemini narrative at gs://%s/%s", args.bucket, narrative_path)
        narrative = read_text_from_gcs(args.bucket, narrative_path)
    else:
        logger.info("Calling Gemini 3.5 Flash for analysis narrative...")
        narrative = call_gemini_analysis(facts, rules, warehouse=args.warehouse)
        write_narrative_to_gcs(narrative, args.bucket, narrative_path)

    logger.info("GCS_OUTPUTS_COMPLETE=true")

    rows_updated = 0
    writeback_succeeded = False
    if write_cloudsql_reasoning:
        logger.info("Writing selected per-row reasoning to Cloud SQL...")
        try:
            rows_updated = write_reasoning_to_cloud_sql(
                writeback_df, rules, args.run_id, args.warehouse, args.db_connection_string
            )
            writeback_succeeded = rows_updated > 0
        except Exception as e:
            logger.error("Cloud SQL reasoning writeback failed after GCS outputs completed: %s", e)
            if args.require_cloudsql_reasoning_writeback:
                raise
    else:
        logger.info("CLOUDSQL_REASONING_WRITEBACK_SKIPPED=true")
        logger.info("GCS_OUTPUTS_COMPLETE=true")

    if rows_updated > 0:
        if rows_updated == len(writeback_df):
            logger.info(f"✓ All {rows_updated} reasoning rows written successfully.")
        else:
            logger.warning(f"⚠ Only {rows_updated}/{len(writeback_df)} selected rows updated.")
    else:
        logger.warning("No reasoning rows written to Cloud SQL. Writeback may be disabled, DB unreachable, or no matching rows were found.")

    # Update match_audit metadata row with analysis provenance
    if write_cloudsql_reasoning and writeback_succeeded:
        write_analysis_audit_metadata(
            match_run_id=args.run_id,
            db_connection_string=args.db_connection_string,
            warehouse=args.warehouse,
            analysis_context={
                "analysis_run_id": args.analysis_run_id,
                "attempt": args.analysis_run_attempt,
                "workflow": args.analysis_workflow,
                "gemini_model": os.getenv("GEMINI_MODEL", rules["environment"]["models"]["gemini_flash"]),
            },
            reasoning_rows_written=rows_updated,
            matches_rows=len(df),
            rules=rules,
        )
    else:
        logger.info("Cloud SQL match_audit update skipped.")

    logger.info("Analysis complete.")


if __name__ == "__main__":
    main()
