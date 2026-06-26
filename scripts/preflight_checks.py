#!/usr/bin/env python3
"""
Preflight validation for lead_to_pos_match_rules.json.

Tier 1: JSON structure (must pass — blocks pipeline)
Tier 2: Connectivity (Vertex AI, Cloud SQL, GCS)
Tier 3: Tuning sanity (warns, doesn't block)

Usage:
    python scripts/preflight_checks.py [--skip-connectivity]
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "lead_match_runtime" / "lead_to_pos_match_rules.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_results" / "env_testing"


def _check(name, status, detail=""):
    return {"name": name, "status": status, "detail": str(detail)}


def _pass(name, detail=""):
    return _check(name, "PASS", detail)


def _fail(name, detail=""):
    return _check(name, "FAIL", detail)


def _warn(name, detail=""):
    return _check(name, "WARN", detail)


# ── Tier 1: JSON Structure ─────────────────────────────────────

REQUIRED_TOP_KEYS = [
    "business_context", "candidate_retrieval", "candidate_selection",
    "decision_rules", "embeddings", "environment", "fiscal_rules",
    "matching_markers", "matching_sets", "normalization", "output_contract",
    "override_policy", "resolution", "score_model", "scoring",
    "semantic_definitions", "warehouse_scope",
]

REQUIRED_ENV_SECTIONS = [
    "project_id", "region", "schema", "label",
    "vertex_ai", "models", "cloud_sql", "gcs", "cloud_run",
    "safety_flags", "tuning", "hnsw_index", "dry_run_controls", "fiscal_defaults",
]

REQUIRED_VERTEX_AI_KEYS = ["project_id", "location", "timeout_seconds"]
REQUIRED_MODELS_KEYS = ["gemini_flash", "embedding"]
REQUIRED_CLOUD_SQL_KEYS = [
    "connection_name", "instance", "socket_dir", "host", "port",
    "database", "schema",
]
REQUIRED_GCS_KEYS = ["report_bucket"]
REQUIRED_CLOUD_RUN_KEYS = ["service_name", "health_path"]
REQUIRED_SAFETY_KEYS = ["allow_client_gcp", "allow_local_db", "allow_production", "dry_run"]
REQUIRED_TUNING_KEYS = [
    "embedding_batch_size", "embedding_batch_workers", "embedding_max_workers",
    "embedding_max_texts_per_request", "embedding_max_retries",
    "embedding_retry_base_delay", "embedding_retry_max_delay",
    "embedding_request_log_every", "embedding_qps_limit", "normalize_embeddings",
    "embedding_lead_batch_size", "embedding_pos_internal_batch_size",
    "embedding_request_size_cap", "pos_embedding_chunk_size",
    "match_batch_size", "match_statement_timeout_ms", "max_workers",
    "match_lead_limit_default", "analysis_db_batch_size",
    "vector_top_k", "explain_fuzzy_plan", "db_write_batch_size", "db_pool_max",
]
REQUIRED_HNSW_KEYS = ["m", "ef_construction", "ef_search", "maintenance_work_mem"]
REQUIRED_DRY_RUN_KEYS = ["match_row_limit", "writeback_business_tables"]
REQUIRED_FISCAL_KEYS = ["fiscal_year", "fiscal_period"]


def _check_keys(section, required, section_name):
    checks = []
    for key in required:
        if key in section:
            checks.append(_pass(f"{section_name}.{key}", section[key]))
        else:
            checks.append(_fail(f"{section_name}.{key}", "MISSING"))
    return checks


def tier1_structure(rules):
    checks = []

    for key in REQUIRED_TOP_KEYS:
        if key in rules:
            checks.append(_pass(f"top_level.{key}"))
        else:
            checks.append(_fail(f"top_level.{key}", "MISSING"))

    env = rules.get("environment", {})
    checks.extend(_check_keys(env, REQUIRED_ENV_SECTIONS, "environment"))
    checks.extend(_check_keys(env.get("vertex_ai", {}), REQUIRED_VERTEX_AI_KEYS, "vertex_ai"))
    checks.extend(_check_keys(env.get("models", {}), REQUIRED_MODELS_KEYS, "models"))
    checks.extend(_check_keys(env.get("cloud_sql", {}), REQUIRED_CLOUD_SQL_KEYS, "cloud_sql"))
    checks.extend(_check_keys(env.get("gcs", {}), REQUIRED_GCS_KEYS, "gcs"))
    checks.extend(_check_keys(env.get("cloud_run", {}), REQUIRED_CLOUD_RUN_KEYS, "cloud_run"))
    checks.extend(_check_keys(env.get("safety_flags", {}), REQUIRED_SAFETY_KEYS, "safety_flags"))
    checks.extend(_check_keys(env.get("tuning", {}), REQUIRED_TUNING_KEYS, "tuning"))
    checks.extend(_check_keys(env.get("hnsw_index", {}), REQUIRED_HNSW_KEYS, "hnsw_index"))
    checks.extend(_check_keys(env.get("dry_run_controls", {}), REQUIRED_DRY_RUN_KEYS, "dry_run_controls"))
    checks.extend(_check_keys(env.get("fiscal_defaults", {}), REQUIRED_FISCAL_KEYS, "fiscal_defaults"))

    emb_model = rules.get("embeddings", {}).get("model", "")
    env_model = env.get("models", {}).get("embedding", "")
    if emb_model and emb_model == env_model:
        checks.append(_pass("embedding_model_consistency", emb_model))
    else:
        checks.append(_fail("embedding_model_consistency", f"embeddings.model={emb_model} != models.embedding={env_model}"))

    dim = rules.get("embeddings", {}).get("output_dimensionality")
    if isinstance(dim, int) and dim > 0:
        checks.append(_pass("embedding_dimension", dim))
    else:
        checks.append(_fail("embedding_dimension", f"expected positive integer, got {dim}"))

    dr = rules.get("decision_rules", {})
    floor = dr.get("fuzzy_qualify_min_score")
    cap = dr.get("fuzzy_max_score")
    if 0 < floor < cap:
        checks.append(_pass("score_floor_cap_consistent", f"floor={floor} < cap={cap}"))
    else:
        checks.append(_fail("score_floor_cap_consistent", f"floor={floor}, cap={cap} — floor must be positive and less than cap"))

    fields = rules.get("embeddings", {}).get("fields", {})
    addr_w = fields.get("address_variant", {}).get("weight", 0)
    name_w = fields.get("name_variant", {}).get("weight", 0)
    total = addr_w + name_w
    if total > 0:
        checks.append(_pass("score_weights_sum", f"address={addr_w} + name={name_w} = {total}"))
    else:
        checks.append(_fail("score_weights_sum", f"address={addr_w} + name={name_w} = {total}, must be positive"))

    safety = env.get("safety_flags", {})
    if safety.get("allow_production") is False:
        checks.append(_pass("safety_allow_production", False))
    else:
        checks.append(_fail("safety_allow_production", f"expected false, got {safety.get('allow_production')}"))
    if safety.get("allow_client_gcp") is False:
        checks.append(_pass("safety_allow_client_gcp", False))
    else:
        checks.append(_fail("safety_allow_client_gcp", f"expected false, got {safety.get('allow_client_gcp')}"))

    db_user = os.environ.get("DB_USER", "")
    db_pass = os.environ.get("DB_PASSWORD", "")
    if db_user:
        checks.append(_pass("env_DB_USER", "set"))
    else:
        checks.append(_fail("env_DB_USER", "NOT SET — required secret"))
    if db_pass:
        checks.append(_pass("env_DB_PASSWORD", "set"))
    else:
        checks.append(_fail("env_DB_PASSWORD", "NOT SET — required secret"))

    # Embedding rules
    emb = rules.get("embeddings", {})
    if emb.get("l2_normalize") is True:
        checks.append(_pass("embeddings.l2_normalize", True))
    else:
        checks.append(_fail("embeddings.l2_normalize", "must be true"))

    if emb.get("skip_empty_text") is True:
        checks.append(_pass("embeddings.skip_empty_text", True))
    else:
        checks.append(_fail("embeddings.skip_empty_text", "must be true"))

    if emb.get("never_write_zero_vectors") is True:
        checks.append(_pass("embeddings.never_write_zero_vectors", True))
    else:
        checks.append(_fail("embeddings.never_write_zero_vectors", "must be true"))

    do_not_embed = set(rules.get("matching_markers", {}).get("do_not_embed", []))
    for field in ["email", "phone", "first_name", "last_name"]:
        if field in do_not_embed:
            checks.append(_pass(f"do_not_embed.{field}"))
        else:
            checks.append(_fail(f"do_not_embed.{field}", f"'{field}' must be in do_not_embed list"))

    # Six matching sets
    sets = rules.get("matching_sets", {}).get("sets", [])
    if len(sets) == 6:
        checks.append(_pass("matching_sets_count", 6))
    else:
        checks.append(_fail("matching_sets_count", f"expected 6, got {len(sets)}"))

    # Scoring rules
    scoring = rules.get("scoring", {})
    boosts = scoring.get("deterministic_boosts", {})
    fuzzy_max = dr.get("fuzzy_max_score")
    if boosts.get("cap") == fuzzy_max:
        checks.append(_pass("boost_cap_matches_fuzzy_max", f"cap={boosts.get('cap')} == fuzzy_max_score={fuzzy_max}"))
    else:
        checks.append(_fail("boost_cap_matches_fuzzy_max", f"cap={boosts.get('cap')} != fuzzy_max_score={fuzzy_max}"))

    if isinstance(boosts.get("email_exact_match"), (int, float)) and boosts["email_exact_match"] > 0:
        checks.append(_pass("email_boost", f"+{boosts['email_exact_match']}"))
    else:
        checks.append(_fail("email_boost", f"expected positive number, got {boosts.get('email_exact_match')}"))

    if boosts.get("phone_exact_match") == 5:
        checks.append(_pass("phone_boost", "+5"))
    else:
        checks.append(_fail("phone_boost", f"expected 5, got {boosts.get('phone_exact_match')}"))

    # Exact owns 100
    dr_exact = dr.get("exact_owns_100")
    if dr_exact is True:
        checks.append(_pass("exact_owns_100", True))
    else:
        checks.append(_fail("exact_owns_100", "must be true"))

    # Candidate retrieval
    cr = rules.get("candidate_retrieval", {})
    if cr.get("method") == "pgvector_hnsw":
        checks.append(_pass("retrieval_method", "pgvector_hnsw"))
    else:
        checks.append(_fail("retrieval_method", f"expected pgvector_hnsw, got {cr.get('method')}"))

    if cr.get("recall_gate_field") == "combined_field":
        checks.append(_pass("recall_gate_field", "combined_field"))
    else:
        checks.append(_fail("recall_gate_field", f"expected combined_field, got {cr.get('recall_gate_field')}"))

    combined_in_score = emb.get("fields", {}).get("combined_field", {}).get("in_final_score")
    if combined_in_score is False:
        checks.append(_pass("combined_not_in_score", "recall gate only"))
    else:
        checks.append(_fail("combined_not_in_score", "combined_field must not be in final score"))

    # Fiscal CE window
    fiscal = rules.get("fiscal_rules", {})
    if fiscal.get("ce_period_window") == 6:
        checks.append(_pass("fiscal_ce_window", 6))
    else:
        checks.append(_fail("fiscal_ce_window", f"expected 6, got {fiscal.get('ce_period_window')}"))

    return checks


# ── Tier 2: Connectivity ───────────────────────────────────────

def tier2_connectivity(rules):
    checks = []
    env = rules["environment"]
    vai = env["vertex_ai"]
    models = env["models"]
    csql = env["cloud_sql"]

    # Vertex AI embedding (via curl — avoids SDK auth timeout issues)
    try:
        import subprocess
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, timeout=30
        ).strip()
        base_url = f"https://{vai['location']}-aiplatform.googleapis.com/v1"
        project = vai["project_id"]
        location = vai["location"]

        expected_dim = rules.get("embeddings", {}).get("output_dimensionality", 768)
        emb_url = f"{base_url}/projects/{project}/locations/{location}/publishers/google/models/{models['embedding']}:predict"
        emb_payload = json.dumps({
            "instances": [{"content": "preflight smoke test", "task_type": "RETRIEVAL_DOCUMENT"}],
            "parameters": {"outputDimensionality": expected_dim},
        })
        emb_result = subprocess.run(
            ["curl", "-sS", "-X", "POST", "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json", "-d", emb_payload, emb_url],
            capture_output=True, text=True, timeout=30,
        )
        emb_data = json.loads(emb_result.stdout)
        values = emb_data["predictions"][0]["embeddings"]["values"]
        dim = len(values)
        if dim == expected_dim:
            checks.append(_pass("vertex_embedding", f"{models['embedding']} returned {dim} dims"))
        else:
            checks.append(_fail("vertex_embedding", f"expected {expected_dim} dims, got {dim}"))
    except Exception as e:
        checks.append(_fail("vertex_embedding", str(e)[:200]))

    # Vertex AI Gemini Flash
    try:
        flash_url = f"{base_url}/projects/{project}/locations/{location}/publishers/google/models/{models['gemini_flash']}:generateContent"
        flash_payload = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": "Reply with one word: OK"}]}],
            "generationConfig": {"maxOutputTokens": 10},
        })
        flash_result = subprocess.run(
            ["curl", "-sS", "-X", "POST", "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json", "-d", flash_payload, flash_url],
            capture_output=True, text=True, timeout=30,
        )
        flash_data = json.loads(flash_result.stdout)
        model_ver = flash_data.get("modelVersion", "unknown")
        checks.append(_pass("vertex_gemini_flash", f"{models['gemini_flash']} (version={model_ver})"))
    except Exception as e:
        checks.append(_fail("vertex_gemini_flash", str(e)[:200]))

    # Cloud SQL
    db_user = os.environ.get("DB_USER", "")
    db_pass = os.environ.get("DB_PASSWORD", "")
    if not db_user or not db_pass:
        checks.append(_fail("cloudsql_connect", "DB_USER/DB_PASSWORD not set"))
    else:
        try:
            import pg8000.dbapi
            conn = pg8000.dbapi.connect(
                host=str(csql["host"]),
                port=int(csql["port"]),
                database=csql["database"],
                user=db_user,
                password=db_pass,
            )
            cur = conn.cursor()
            cur.execute("SELECT 1")
            checks.append(_pass("cloudsql_connect", f"{csql['host']}:{csql['port']}/{csql['database']}"))

            # Schema check
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
                (csql["schema"],),
            )
            if cur.fetchone():
                checks.append(_pass("schema_exists", csql["schema"]))
            else:
                checks.append(_fail("schema_exists", f"schema '{csql['schema']}' not found"))

            # Core tables
            core_tables = ["lead", "transaction", "leads_embeddings", "pos_embeddings", "match_decision_detail"]
            for table in core_tables:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s)",
                    (csql["schema"], table),
                )
                exists = cur.fetchone()[0]
                if exists:
                    checks.append(_pass(f"table.{table}"))
                else:
                    checks.append(_fail(f"table.{table}", "NOT FOUND"))

            cur.close()
            conn.close()
        except Exception as e:
            checks.append(_fail("cloudsql_connect", str(e)[:200]))

    # GCS bucket
    try:
        from google.cloud import storage
        gcs_client = storage.Client(project=vai["project_id"])
        bucket_name = env["gcs"]["report_bucket"]
        bucket = gcs_client.bucket(bucket_name)
        if bucket.exists():
            checks.append(_pass("gcs_bucket", bucket_name))
        else:
            checks.append(_fail("gcs_bucket", f"bucket '{bucket_name}' not found"))
    except Exception as e:
        checks.append(_fail("gcs_bucket", str(e)[:200]))

    return checks


# ── Tier 3: Tuning Sanity ──────────────────────────────────────

def _range_check(name, value, lo, hi):
    if lo <= value <= hi:
        return _pass(name, value)
    return _warn(name, f"{value} outside recommended range [{lo}, {hi}]")


def tier3_tuning(rules):
    checks = []
    env = rules["environment"]
    t = env["tuning"]
    h = env["hnsw_index"]
    f = env["fiscal_defaults"]

    m = h["m"]
    ef_c = h["ef_construction"]
    checks.append(_range_check("hnsw_m", m, 2, 64))
    if ef_c >= 2 * m:
        checks.append(_pass("hnsw_ef_construction", f"{ef_c} >= 2*m({m})"))
    else:
        checks.append(_warn("hnsw_ef_construction", f"{ef_c} < 2*m({m}) — recall may suffer"))
    checks.append(_range_check("hnsw_ef_search", h["ef_search"], 1, 500))

    checks.append(_range_check("embedding_batch_size", t["embedding_batch_size"], 1, 1000))
    checks.append(_range_check("embedding_batch_workers", t["embedding_batch_workers"], 1, 20))
    checks.append(_range_check("embedding_max_workers", t["embedding_max_workers"], 1, 20))
    checks.append(_range_check("embedding_max_retries", t["embedding_max_retries"], 1, 20))
    checks.append(_range_check("embedding_qps_limit", t["embedding_qps_limit"], 1, 100))
    checks.append(_range_check("embedding_lead_batch_size", t["embedding_lead_batch_size"], 10, 1000))
    checks.append(_range_check("pos_embedding_chunk_size", t["pos_embedding_chunk_size"], 100, 10000))
    checks.append(_range_check("embedding_pos_internal_batch_size", t["embedding_pos_internal_batch_size"], 5, 500))
    checks.append(_range_check("embedding_request_size_cap", t["embedding_request_size_cap"], 10, 1000))
    checks.append(_range_check("match_batch_size", t["match_batch_size"], 1, 1000))
    checks.append(_range_check("max_workers", t["max_workers"], 1, 20))
    checks.append(_range_check("match_lead_limit_default", t["match_lead_limit_default"], 1000, 10000000))
    checks.append(_range_check("vector_top_k", t["vector_top_k"], 10, 1000))
    checks.append(_range_check("db_pool_max", t["db_pool_max"], 1, 50))
    checks.append(_range_check("db_write_batch_size", t["db_write_batch_size"], 1, 1000))

    timeout_s = t["match_statement_timeout_ms"] / 1000
    checks.append(_range_check("match_timeout_seconds", timeout_s, 60, 3600))

    vtimeout = env["vertex_ai"]["timeout_seconds"]
    checks.append(_range_check("vertex_timeout_seconds", vtimeout, 10, 600))

    current_year = datetime.now(UTC).year
    fy = f["fiscal_year"]
    if abs(fy - current_year) <= 1:
        checks.append(_pass("fiscal_year_current", fy))
    else:
        checks.append(_warn("fiscal_year_current", f"{fy} is not near {current_year}"))

    if t["normalize_embeddings"] is True:
        checks.append(_pass("normalize_embeddings", True))
    else:
        checks.append(_warn("normalize_embeddings", "should be true for 768-dim gemini-embedding-001"))

    return checks


# ── Main ───────────────────────────────────────────────────────

def run_preflight(skip_connectivity=False):
    rules_path = Path(os.environ.get("LEAD_POS_RULES_PATH", RULES_PATH))
    if not rules_path.exists():
        print(f"FAIL: Rules file not found: {rules_path}")
        sys.exit(1)

    with rules_path.open(encoding="utf-8") as fh:
        rules = json.load(fh)

    project = rules.get("environment", {}).get("project_id", "unknown")
    ts = datetime.now(UTC)

    t1 = tier1_structure(rules)
    t1_fail = any(c["status"] == "FAIL" for c in t1)

    if skip_connectivity:
        t2 = [_check("skipped", "SKIP", "--skip-connectivity flag")]
    elif t1_fail:
        t2 = [_check("skipped", "SKIP", "Tier 1 failed — fix structure first")]
    else:
        t2 = tier2_connectivity(rules)

    t3 = tier3_tuning(rules) if not t1_fail else [_check("skipped", "SKIP", "Tier 1 failed")]

    all_checks = t1 + t2 + t3
    n_pass = sum(1 for c in all_checks if c["status"] == "PASS")
    n_fail = sum(1 for c in all_checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in all_checks if c["status"] == "WARN")
    n_skip = sum(1 for c in all_checks if c["status"] == "SKIP")

    overall = "FAIL" if n_fail > 0 else "PASS"

    report = {
        "timestamp": ts.isoformat(),
        "project": project,
        "rules_file": str(rules_path),
        "status": overall,
        "tier1_structure": {"status": "FAIL" if any(c["status"] == "FAIL" for c in t1) else "PASS", "checks": t1},
        "tier2_connectivity": {"status": "FAIL" if any(c["status"] == "FAIL" for c in t2) else "PASS", "checks": t2},
        "tier3_tuning": {"status": "WARN" if any(c["status"] == "WARN" for c in t3) else "PASS", "checks": t3},
        "summary": {"pass": n_pass, "fail": n_fail, "warn": n_warn, "skip": n_skip},
    }

    # Print summary
    print()
    print("=" * 64)
    print(f"  PREFLIGHT — {project} — {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 64)

    for tier_name, tier_key in [("Tier 1: Structure", "tier1_structure"),
                                 ("Tier 2: Connectivity", "tier2_connectivity"),
                                 ("Tier 3: Tuning", "tier3_tuning")]:
        tier = report[tier_key]
        print(f"\n  {tier_name} [{tier['status']}]")
        print("  " + "-" * 60)
        for c in tier["checks"]:
            icon = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "SKIP": "SKIP"}[c["status"]]
            detail = f"  {c['detail']}" if c["detail"] else ""
            print(f"    {icon:4s}  {c['name']:40s}{detail}")

    print()
    print("=" * 64)
    print(f"  {overall}  pass={n_pass}  fail={n_fail}  warn={n_warn}  skip={n_skip}")
    print("=" * 64)
    print()

    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"preflight_{ts.strftime('%Y%m%d_%H%M%S')}.json"
    with out_file.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"Report saved: {out_file}")

    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preflight checks for lead-match pipeline")
    parser.add_argument("--skip-connectivity", action="store_true", help="Skip Tier 2 connectivity checks")
    args = parser.parse_args()
    sys.exit(run_preflight(skip_connectivity=args.skip_connectivity))
