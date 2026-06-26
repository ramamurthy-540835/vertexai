#!/usr/bin/env python3
"""Fuzzy/semantic matching runner using local files instead of Cloud SQL.

Reads leads and POS from local xlsx/csv, generates embeddings via Vertex AI
(gemini-embedding-001), computes cosine similarity in-memory with numpy,
and writes results locally.

Does NOT connect to Cloud SQL, GCS, SPT, or PRD.

Usage:
    python3 lead_match_runtime/fuzzy_file_runner.py \\
      --source-mode files \\
      --leads-file mock_data/115/leads_corrected.xlsx \\
      --pos-file mock_data/115_from_exact/pos_corrected.xlsx \\
      --exact-output-csv mock_data/115_from_exact/primary_match_output_*.csv \\
      --warehouse-number 115 \\
      --output-dir mock_data/115_from_exact \\
      --top-k 20
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from google import genai
from google.genai import types

from lead_match_runtime.business_rules import (
    apply_deterministic_boost,
    build_embedding_text,
    build_pos_variant_texts,
    calculate_semantic_precision_score,
    fiscal_ce_period_window,
    fiscal_periods_per_year,
    fuzzy_max_score,
    fuzzy_qualify_min_score,
    get_tuning_float,
    get_tuning_int,
    get_vertex_location,
    get_vertex_project,
    get_vertex_timeout,
    load_business_rules,
    matching_set_by_id,
    matching_sets,
    normalize_business_identity,
    normalize_fuzzy_final_score,
    resolve_pos_to_single_lead,
    select_primary_transaction,
)

logger = logging.getLogger(__name__)

RULES = load_business_rules()
EMBEDDING_MODEL = RULES["embeddings"]["model"]
EMBEDDING_DIMENSION = int(RULES["embeddings"]["output_dimensionality"])
EMBEDDING_TASK_TYPE = RULES["embeddings"].get("task_type", "SEMANTIC_SIMILARITY")
EMBEDDING_MAX_RETRIES = get_tuning_int(RULES, "embedding_max_retries")
EMBEDDING_RETRY_BASE_DELAY = get_tuning_float(RULES, "embedding_retry_base_delay")
EMBEDDING_RETRY_MAX_DELAY = get_tuning_float(RULES, "embedding_retry_max_delay")
EMBEDDING_REQUEST_SIZE_CAP = get_tuning_int(RULES, "embedding_request_size_cap")

OUTPUT_HEADER = [
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

DEBUG_HEADER = [
    "lead_id", "pos_id", "warehouse_number", "expected_relation",
    "combined_similarity", "name_score", "address_score",
    "email_boost", "phone_boost", "final_score", "winning_set",
    "decision", "reason",
]

BOOST_FIELD_MAP = {
    "email_1_oms": "oms_email_1",
    "phone_1_oms": "oms_phone_1",
    "email_2_oms": "oms_email_2",
    "phone_2_oms": "oms_phone_2",
}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fuzzy file-mode matching runner.")
    p.add_argument("--source-mode", default="files", choices=["files"])
    p.add_argument("--leads-file", required=True)
    p.add_argument("--pos-file", required=True)
    p.add_argument("--exact-output-csv", default=None,
                   help="Exact-match output CSV (supports glob patterns).")
    p.add_argument("--warehouse-number", type=int, required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--top-k", type=int, default=20)
    p.add_argument("--limit-leads", type=int, default=None)
    p.add_argument("--include-exact-leads", action="store_true", default=False)
    p.add_argument("--recall-gate", type=float, default=65.0)
    p.add_argument("--embedding-batch-size", type=int, default=100)
    p.add_argument("--no-cache", action="store_true",
                   help="Force regeneration of embeddings, ignore existing cache.")
    p.add_argument("--cache-dir", default=None,
                   help="Override embedding cache directory (default: <output-dir>/embedding_cache).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════

def _safe(val, default=""):
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return default if s.lower() in ("nan", "none", "<na>") else s


def _read_file(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    return pd.read_csv(p, dtype=str)


def load_leads(path: str, wh: int, limit: int | None = None) -> pd.DataFrame:
    df = _read_file(path)
    df["warehouse_number"] = df["warehouse_number"].astype(str).str.strip()
    df = df[df["warehouse_number"] == str(wh)].copy()
    if limit:
        df = df.head(limit)
    return df


def load_pos(path: str, wh: int) -> pd.DataFrame:
    df = _read_file(path)
    df["warehouse_number"] = df["warehouse_number"].astype(str).str.strip()
    df = df[df["warehouse_number"] == str(wh)].copy()
    renames = {"oms_company": "oms_company_name", "oms_company_2": "oms2_company_name"}
    df.rename(columns={k: v for k, v in renames.items() if k in df.columns}, inplace=True)
    return df


def load_exact_exclusions(csv_path: str | None) -> tuple[set[str], set[str]]:
    if not csv_path:
        return set(), set()
    paths = glob.glob(csv_path)
    if not paths:
        logger.warning("No files matched: %s", csv_path)
        return set(), set()
    lead_ids: set[str] = set()
    pos_ids: set[str] = set()
    for p in paths:
        df = pd.read_csv(p, dtype=str)
        df.columns = df.columns.str.strip().str.lower()
        exact = df[
            (df.get("match_result", pd.Series(dtype=str)).fillna("").str.lower() == "match")
            & (df.get("match_type", pd.Series(dtype=str)).fillna("").str.lower() == "exact")
        ]
        pos_ids.update(exact["pos_id"].dropna().str.strip())
        for col in ["lead_id", "u_matched_lead_number"]:
            if col in exact.columns:
                lead_ids.update(exact[col].dropna().str.strip())
    return lead_ids, pos_ids


# ═══════════════════════════════════════════════════════════════
# Embedding (Vertex AI, returns numpy arrays)
# ═══════════════════════════════════════════════════════════════

def init_vertex_client(config: dict) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=get_vertex_project(config),
        location=get_vertex_location(config),
        http_options=types.HttpOptions(api_version="v1", timeout=get_vertex_timeout(config)),
    )


def _is_retryable(exc: Exception) -> bool:
    err = f"{exc.__class__.__module__}.{exc.__class__.__name__}: {exc}".lower()
    markers = ("429", "500", "502", "503", "504", "deadline", "timeout",
               "timed out", "resource exhausted", "quota", "rate limit",
               "temporarily unavailable", "connection reset")
    return any(m in err for m in markers)


def _l2_normalize(values) -> np.ndarray | None:
    arr = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if not norm or np.isnan(norm) or not np.isfinite(norm):
        return None
    arr = arr / norm
    if not np.all(np.isfinite(arr)) or not np.any(arr):
        return None
    return arr


def _embed_batch_numpy(client, texts: list[str], label: str) -> list[np.ndarray | None]:
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            resp = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=EMBEDDING_TASK_TYPE,
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            embeddings = resp.embeddings or []
            if len(embeddings) != len(texts):
                raise RuntimeError(f"API returned {len(embeddings)} for {len(texts)} texts")
            return [_l2_normalize(e.values) for e in embeddings]
        except Exception as exc:
            if attempt >= EMBEDDING_MAX_RETRIES or not _is_retryable(exc):
                raise
            delay = min(EMBEDDING_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1.5),
                        EMBEDDING_RETRY_MAX_DELAY)
            logger.info("Retry %s attempt=%d delay=%.1fs: %s", label, attempt, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"Embedding failed: {label}")


def embed_texts_numpy(client, texts: list[str | None], batch_size: int,
                      label: str = "emb") -> list[np.ndarray | None]:
    normalized = [(_safe(t) or "").strip() for t in texts]
    results: list[np.ndarray | None] = [None] * len(normalized)
    pending = [(i, t) for i, t in enumerate(normalized) if t]
    if not pending:
        return results

    cap = min(batch_size, EMBEDDING_REQUEST_SIZE_CAP)
    for start in range(0, len(pending), cap):
        chunk = pending[start:start + cap]
        chunk_texts = [t for _, t in chunk]
        vectors = _embed_batch_numpy(client, chunk_texts, f"{label}_{start // cap + 1}")
        for (idx, _), vec in zip(chunk, vectors):
            results[idx] = vec
    return results


# ═══════════════════════════════════════════════════════════════
# Embedding cache (NPZ)
# ═══════════════════════════════════════════════════════════════

_ZERO_VEC = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)


def _save_embeddings_cache(cache_path: Path, emb_dict: dict[str, list],
                           ids: list[str]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {"_ids": np.array(ids, dtype=str)}
    for key, vecs in emb_dict.items():
        data = np.stack([v if v is not None else _ZERO_VEC for v in vecs])
        mask = np.array([v is not None for v in vecs], dtype=bool)
        arrays[key] = data
        arrays[f"{key}_mask"] = mask
    np.savez_compressed(str(cache_path), **arrays)
    logger.info("Saved embedding cache: %s (%d records)", cache_path.name, len(ids))


def _load_embeddings_cache(cache_path: Path, expected_ids: list[str]) -> dict[str, list] | None:
    if not cache_path.exists():
        return None
    try:
        data = np.load(str(cache_path), allow_pickle=False)
    except Exception as exc:
        logger.warning("Failed to load cache %s: %s", cache_path, exc)
        return None

    cached_ids = data.get("_ids")
    if cached_ids is None or list(cached_ids) != expected_ids:
        logger.info("Cache IDs mismatch in %s — regenerating", cache_path.name)
        return None

    result: dict[str, list] = {}
    for key in data.files:
        if key.startswith("_") or key.endswith("_mask"):
            continue
        mask_key = f"{key}_mask"
        if mask_key not in data.files:
            continue
        vectors = data[key]
        mask = data[mask_key]
        result[key] = [vectors[i] if mask[i] else None for i in range(len(vectors))]

    logger.info("Loaded embedding cache: %s (%d records)", cache_path.name, len(expected_ids))
    return result


# ═══════════════════════════════════════════════════════════════
# Record preparation
# ═══════════════════════════════════════════════════════════════

def prepare_lead_record(row: pd.Series) -> dict:
    return {
        "lead_id": _safe(row.get("lead_id")),
        "warehouse_number": _safe(row.get("warehouse_number")),
        "fiscal_year": int(float(_safe(row.get("fiscal_year_lead"), "0") or "0")),
        "fiscal_period": int(float(_safe(row.get("fiscal_period_lead"), "0") or "0")),
        "week": int(float(_safe(row.get("week"), "1") or "1")),
        "business_name": _safe(row.get("business_name")),
        "address_line_one": _safe(row.get("address_line_one")),
        "city": _safe(row.get("city")),
        "state": _safe(row.get("state")),
        "zip_code": _safe(row.get("zip_code")),
        "email": _safe(row.get("email")),
        "phone": _safe(row.get("phone")),
    }


def prepare_pos_record(row: pd.Series) -> dict:
    r: dict = {
        "pos_id": _safe(row.get("pos_id")),
        "warehouse_number": _safe(row.get("warehouse_number")),
        "fiscal_year": int(float(_safe(row.get("fiscal_year_transaction"), "0") or "0")),
        "fiscal_period": int(float(_safe(row.get("fiscal_period_transaction"), "0") or "0")),
        "week": int(float(_safe(row.get("week"), "1") or "1")),
        "business_name": _safe(row.get("business_name")),
        "address_line_one": _safe(row.get("address_line_one")),
        "address_line_two": _safe(row.get("address_line_two")),
        "city": _safe(row.get("city")),
        "state": _safe(row.get("state")),
        "zip_code": _safe(row.get("zip_code")),
        "email": _safe(row.get("email")),
        "phone": _safe(row.get("phone")),
        "account_number": _safe(row.get("account_number")),
        "membership_number": _safe(row.get("membership_number")),
        "sales_reference_id": _safe(row.get("sales_reference_id")),
        "first_name": _safe(row.get("first_name")),
        "last_name": _safe(row.get("last_name")),
        "order_amount": float(_safe(row.get("order_amount"), "0") or "0"),
        "transaction_count": int(float(_safe(row.get("transaction_count"), "1") or "1")),
        "shop_type": _safe(row.get("shop_type")),
        "bd_industry": _safe(row.get("bd_industry")),
        "industry_description": _safe(row.get("industry_description")),
        "updated_date": _safe(row.get("updated_date")),
        "expected_relation": _safe(row.get("expected_relation")),
        "expected_lead_id": _safe(row.get("expected_lead_id")),
        "oms_company_name": _safe(row.get("oms_company_name")),
        "oms2_company_name": _safe(row.get("oms2_company_name")),
        "oms_address_line_1": _safe(row.get("oms_address_line_1")),
        "oms_city": _safe(row.get("oms_city")),
        "oms_state": _safe(row.get("oms_state")),
        "oms_zip": _safe(row.get("oms_zip")),
        "oms_address_line_1_v2": _safe(row.get("oms_address_line_1_v2")),
        "oms_city_2": _safe(row.get("oms_city_2")),
        "oms_state_2": _safe(row.get("oms_state_2")),
        "oms_zip_2": _safe(row.get("oms_zip_2")),
        "oms_email_1": _safe(row.get("oms_email_1")),
        "oms_phone_1": _safe(row.get("oms_phone_1")),
        "oms_email_2": _safe(row.get("oms_email_2")),
        "oms_phone_2": _safe(row.get("oms_phone_2")),
    }
    for rule_name, csv_col in BOOST_FIELD_MAP.items():
        r[rule_name] = r.get(csv_col, "")
    return r


# ═══════════════════════════════════════════════════════════════
# Embedding generation for leads and POS
# ═══════════════════════════════════════════════════════════════

def generate_lead_embeddings(client, records: list[dict], batch_size: int,
                             cache_path: Path | None = None,
                             no_cache: bool = False) -> dict[str, list]:
    ids = [r["lead_id"] for r in records]
    if cache_path and not no_cache:
        cached = _load_embeddings_cache(cache_path, ids)
        if cached is not None:
            return cached

    combined = [build_embedding_text(r, "combined_field") for r in records]
    names = [build_embedding_text(r, "business_name") for r in records]
    addresses = [build_embedding_text(r, "full_address") for r in records]
    logger.info("Embedding %d leads (combined + name + address)...", len(records))
    result = {
        "combined": embed_texts_numpy(client, combined, batch_size, "lead_combined"),
        "name": embed_texts_numpy(client, names, batch_size, "lead_name"),
        "address": embed_texts_numpy(client, addresses, batch_size, "lead_address"),
    }
    if cache_path:
        _save_embeddings_cache(cache_path, result, ids)
    return result


def generate_pos_embeddings(client, records: list[dict], batch_size: int,
                            cache_path: Path | None = None,
                            no_cache: bool = False) -> dict[str, list]:
    ids = [r["pos_id"] for r in records]
    if cache_path and not no_cache:
        cached = _load_embeddings_cache(cache_path, ids)
        if cached is not None:
            return cached

    variant_keys = ["combined_field", "business_name", "full_address",
                    "oms_company_name", "oms2_company_name",
                    "full_oms_address", "full_oms2_address"]
    all_texts: dict[str, list] = {k: [] for k in variant_keys}
    for rec in records:
        variants = build_pos_variant_texts(rec)
        for k in variant_keys:
            all_texts[k].append(variants.get(k))

    logger.info("Embedding %d POS records (7 variant fields)...", len(records))
    result: dict[str, list] = {}
    for key in variant_keys:
        result[key] = embed_texts_numpy(client, all_texts[key], batch_size, f"pos_{key}")

    if cache_path:
        _save_embeddings_cache(cache_path, result, ids)
    return result


# ═══════════════════════════════════════════════════════════════
# Fiscal classification
# ═══════════════════════════════════════════════════════════════

def classify_fiscal(lead: dict, pos: dict, config: dict) -> str:
    ppy = fiscal_periods_per_year(config)
    ce_window = fiscal_ce_period_window(config)
    lead_fy, lead_fp = lead["fiscal_year"], lead["fiscal_period"]
    pos_fy, pos_fp = pos["fiscal_year"], pos["fiscal_period"]
    pos_before = (pos_fy, pos_fp, pos.get("week", 0)) < (lead_fy, lead_fp, lead.get("week", 0))
    if not pos_before:
        return "NORMAL"
    gap = (lead_fy - pos_fy) * ppy + (lead_fp - pos_fp)
    if gap <= ce_window:
        return "CE"
    return "OAF"


# ═══════════════════════════════════════════════════════════════
# Candidate retrieval (in-memory cosine via numpy)
# ═══════════════════════════════════════════════════════════════

def retrieve_candidates(lead_embs: dict, pos_embs: dict,
                        n_leads: int, n_pos: int,
                        top_k: int, recall_gate: float) -> list[tuple[int, int, float]]:
    valid_pos = []
    valid_pos_idx = []
    for i in range(n_pos):
        emb = pos_embs["combined_field"][i]
        if emb is not None:
            valid_pos.append(emb)
            valid_pos_idx.append(i)

    if not valid_pos:
        return []

    pos_matrix = np.stack(valid_pos)
    candidates = []
    gate = recall_gate / 100.0

    for li in range(n_leads):
        l_emb = lead_embs["combined"][li]
        if l_emb is None:
            continue
        scores = pos_matrix @ l_emb
        above = np.where(scores >= gate)[0]
        if len(above) == 0:
            continue
        top_indices = above[np.argsort(-scores[above])[:top_k]]
        for ti in top_indices:
            candidates.append((li, valid_pos_idx[ti], float(scores[ti])))

    return candidates


# ═══════════════════════════════════════════════════════════════
# Six-set scoring
# ═══════════════════════════════════════════════════════════════

SET_NAME_KEY = {
    "business_name": "business_name",
    "oms_company_name": "oms_company_name",
    "oms2_company_name": "oms2_company_name",
}

SET_ADDR_KEY = {
    "full_address": "full_address",
    "full_oms_address": "full_oms_address",
    "full_oms2_address": "full_oms2_address",
}


def _cosine(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    return float(np.dot(a, b))


def score_six_sets(li: int, pi: int,
                   lead_embs: dict, pos_embs: dict,
                   config: dict) -> tuple[float | None, int, float | None, float | None, list[dict]]:
    lead_name = lead_embs["name"][li]
    lead_addr = lead_embs["address"][li]
    if lead_name is None or lead_addr is None:
        return None, 0, None, None, []

    sets = matching_sets(config)
    best_score, best_set, best_name, best_addr = -1.0, 0, None, None
    all_scores: list[dict] = []

    for s in sets:
        sid = int(s["set"])
        name_field = s["name_field"]
        addr_field = s["address_field"]

        pos_name_key = SET_NAME_KEY.get(name_field)
        pos_addr_key = SET_ADDR_KEY.get(addr_field)
        if not pos_name_key or not pos_addr_key:
            continue

        pos_name_emb = pos_embs[pos_name_key][pi]
        pos_addr_emb = pos_embs[pos_addr_key][pi]

        name_cos = _cosine(lead_name, pos_name_emb)
        addr_cos = _cosine(lead_addr, pos_addr_emb)

        if name_cos is None or addr_cos is None:
            all_scores.append({"set": sid, "score": None, "name": None, "addr": None, "skipped": True})
            continue

        name_pct = name_cos * 100
        addr_pct = addr_cos * 100
        score = calculate_semantic_precision_score(addr_pct, name_pct, config=config)
        all_scores.append({"set": sid, "score": score, "name": name_pct, "addr": addr_pct, "skipped": False})

        if score > best_score:
            best_score = score
            best_set = sid
            best_name = name_pct
            best_addr = addr_pct

    if best_score < 0:
        return None, 0, None, None, all_scores
    return best_score, best_set, best_name, best_addr, all_scores


# ═══════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════

def run_pipeline(args: argparse.Namespace) -> dict:
    wh = args.warehouse_number
    config = RULES

    # Load data
    logger.info("Loading leads from %s", args.leads_file)
    leads_df = load_leads(args.leads_file, wh, args.limit_leads)
    logger.info("Loading POS from %s", args.pos_file)
    pos_df = load_pos(args.pos_file, wh)
    exact_leads, exact_pos = load_exact_exclusions(args.exact_output_csv)

    logger.info("Leads: %d, POS: %d, Exact excluded leads: %d, Exact excluded POS: %d",
                len(leads_df), len(pos_df), len(exact_leads), len(exact_pos))

    # Exclude exact-matched
    if exact_pos:
        pos_df = pos_df[~pos_df["pos_id"].astype(str).isin(exact_pos)].copy()
        logger.info("POS after exact exclusion: %d", len(pos_df))
    if not args.include_exact_leads and exact_leads:
        leads_df = leads_df[~leads_df["lead_id"].astype(str).isin(exact_leads)].copy()
        logger.info("Leads after exact exclusion: %d", len(leads_df))

    if leads_df.empty or pos_df.empty:
        logger.warning("No leads or POS to process after filtering.")
        return {"leads_processed": 0, "matches_written": 0}

    # Prepare records
    lead_records = [prepare_lead_record(row) for _, row in leads_df.iterrows()]
    pos_records = [prepare_pos_record(row) for _, row in pos_df.iterrows()]

    # Embedding cache
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(args.output_dir) / "embedding_cache"
    leads_cache = cache_dir / "leads_embeddings.npz"
    pos_cache = cache_dir / "pos_embeddings.npz"

    # Generate embeddings (with cache)
    client = init_vertex_client(config)
    batch_size = args.embedding_batch_size
    lead_embs = generate_lead_embeddings(client, lead_records, batch_size,
                                         cache_path=leads_cache, no_cache=args.no_cache)
    pos_embs = generate_pos_embeddings(client, pos_records, batch_size,
                                       cache_path=pos_cache, no_cache=args.no_cache)

    # Candidate retrieval
    logger.info("Retrieving candidates (top_k=%d, recall_gate=%.1f)...", args.top_k, args.recall_gate)
    candidates = retrieve_candidates(lead_embs, pos_embs, len(lead_records), len(pos_records),
                                     args.top_k, args.recall_gate)
    logger.info("Candidate pairs: %d", len(candidates))

    # Score and classify
    scored: list[dict] = []
    debug_rows: list[dict] = []
    stats = {
        "ce_stubs": 0, "oaf_dropped": 0, "normal_scored": 0,
        "above_threshold": 0, "below_threshold": 0,
    }

    qualify_min = fuzzy_qualify_min_score(config)

    for li, pi, combined_sim in candidates:
        lead = lead_records[li]
        pos = pos_records[pi]

        fiscal = classify_fiscal(lead, pos, config)

        if fiscal == "OAF":
            stats["oaf_dropped"] += 1
            debug_rows.append({
                "lead_id": lead["lead_id"], "pos_id": pos["pos_id"],
                "warehouse_number": wh, "expected_relation": pos.get("expected_relation", ""),
                "combined_similarity": round(combined_sim * 100, 2),
                "name_score": "", "address_score": "",
                "email_boost": 0, "phone_boost": 0, "final_score": "",
                "winning_set": "", "decision": "OAF", "reason": "Pre-lead > 6 periods",
            })
            continue

        if fiscal == "CE":
            stats["ce_stubs"] += 1
            debug_rows.append({
                "lead_id": lead["lead_id"], "pos_id": pos["pos_id"],
                "warehouse_number": wh, "expected_relation": pos.get("expected_relation", ""),
                "combined_similarity": round(combined_sim * 100, 2),
                "name_score": "", "address_score": "",
                "email_boost": 0, "phone_boost": 0, "final_score": "",
                "winning_set": "", "decision": "CE", "reason": "Pre-lead within 6 periods",
            })
            continue

        # NORMAL: score six sets
        stats["normal_scored"] += 1
        best_score, winning_set, name_score, addr_score, _ = score_six_sets(
            li, pi, lead_embs, pos_embs, config)

        if best_score is None:
            debug_rows.append({
                "lead_id": lead["lead_id"], "pos_id": pos["pos_id"],
                "warehouse_number": wh, "expected_relation": pos.get("expected_relation", ""),
                "combined_similarity": round(combined_sim * 100, 2),
                "name_score": "", "address_score": "",
                "email_boost": 0, "phone_boost": 0, "final_score": "",
                "winning_set": "", "decision": "NO_EMBEDDING",
                "reason": "No valid set scored",
            })
            continue

        set_def = matching_set_by_id(config, winning_set)
        boosted, email_boost, phone_boost = apply_deterministic_boost(
            best_score, lead, pos, config, winning_set_def=set_def)

        final = normalize_fuzzy_final_score(boosted, config=config,
                                            lead_id=lead["lead_id"], pos_id=pos["pos_id"])

        decision = "Potential" if final is not None and final >= qualify_min else "No Match"

        debug_rows.append({
            "lead_id": lead["lead_id"], "pos_id": pos["pos_id"],
            "warehouse_number": wh, "expected_relation": pos.get("expected_relation", ""),
            "combined_similarity": round(combined_sim * 100, 2),
            "name_score": round(name_score, 2) if name_score else "",
            "address_score": round(addr_score, 2) if addr_score else "",
            "email_boost": email_boost, "phone_boost": phone_boost,
            "final_score": round(final, 2) if final else "",
            "winning_set": winning_set,
            "decision": decision,
            "reason": f"Set {winning_set}: base={best_score:.2f} + boosts -> {final}",
        })

        if final is None:
            stats["below_threshold"] += 1
            continue

        stats["above_threshold"] += 1
        scored.append({
            "lead_id": lead["lead_id"],
            "pos_id": pos["pos_id"],
            "final_score": final,
            "match_type": "Fuzzy",
            "match_result": "Potential",
            "winning_set": winning_set,
            "name_score": name_score,
            "address_score": addr_score,
            "email_boost": email_boost,
            "phone_boost": phone_boost,
            "combined_sim": combined_sim * 100,
            "fiscal_year": pos["fiscal_year"],
            "fiscal_period": pos["fiscal_period"],
            "week": pos["week"],
            "_pos_record": pos,
        })

    # Resolve POS-to-lead conflicts
    resolved = resolve_pos_to_single_lead(scored, config)
    resolved = select_primary_transaction(resolved, config)

    stats["matches_written"] = len(resolved)
    stats["manual_review"] = sum(1 for r in resolved if r.get("match_type") == "Manual Review")
    stats["primary_transactions"] = sum(1 for r in resolved if r.get("primary_transaction"))
    stats["leads_processed"] = len(lead_records)
    stats["pos_candidates"] = len(pos_records)
    stats["candidate_pairs"] = len(candidates)
    stats["exact_excluded_leads"] = len(exact_leads)
    stats["exact_excluded_pos"] = len(exact_pos)
    stats["max_score"] = max((r["final_score"] for r in resolved), default=0)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Fuzzy File Runner — warehouse {wh}")
    print(f"{'='*60}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"{'='*60}")

    if args.dry_run:
        print("\n[DRY RUN] No output files written.")
        return stats

    # Write output
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_output_csv(resolved, output_dir, wh)
    _write_debug_csv(debug_rows, output_dir)
    _write_summary_json(stats, args, output_dir)

    print(f"\nOutput written to: {output_dir}/")
    print(f"  fuzzy_file_mode_output.csv ({len(resolved)} rows)")
    print(f"  fuzzy_file_mode_debug_candidates.csv ({len(debug_rows)} rows)")
    print(f"  fuzzy_file_mode_summary.json")
    print(f"\nNo Cloud SQL. No GCS. No SPT/PRD.")
    return stats


# ═══════════════════════════════════════════════════════════════
# Output writers
# ═══════════════════════════════════════════════════════════════

def _match_comment(r: dict) -> str:
    parts = [f"Fuzzy match (set {r['winning_set']}, score {r['final_score']:.2f})."]
    if r.get("name_score") is not None:
        parts.append(f"name={r['name_score']:.1f}")
    if r.get("address_score") is not None:
        parts.append(f"addr={r['address_score']:.1f}")
    if r.get("email_boost"):
        parts.append(f"email_boost=+{r['email_boost']:.0f}")
    if r.get("phone_boost"):
        parts.append(f"phone_boost=+{r['phone_boost']:.0f}")
    if r.get("primary_transaction"):
        parts.append("Primary transaction.")
    return " ".join(parts)


def _write_output_csv(results: list[dict], output_dir: Path, wh: int) -> None:
    rows = []
    for r in results:
        pos = r.get("_pos_record", {})
        oa = pos.get("order_amount", 0)
        rows.append({
            "lead_id": r["lead_id"],
            "pos_id": r["pos_id"],
            "match_result": r.get("match_result", "Potential"),
            "similarity_score": r["final_score"],
            "winning_set": r["winning_set"],
            "match_type": r.get("match_type", "Fuzzy"),
            "primary_transaction": r.get("primary_transaction", False),
            "matched_by": "fuzzy_file_runner",
            "matching_comments": _match_comment(r),
            "closed_existing_flag": False,
            "account_number": pos.get("account_number", ""),
            "transaction_count": pos.get("transaction_count", 1),
            "business_name_transaction": pos.get("business_name", ""),
            "membership_number": pos.get("membership_number", ""),
            "warehouse_number": str(wh),
            "sales_reference_id": pos.get("sales_reference_id", ""),
            "fiscal_year_transaction": pos.get("fiscal_year", ""),
            "fiscal_period_transaction": pos.get("fiscal_period", ""),
            "week": pos.get("week", ""),
            "shop_type": pos.get("shop_type", ""),
            "bd_industry": pos.get("bd_industry", ""),
            "order_amount": oa,
            "industry_description": pos.get("industry_description", ""),
            "first_name": pos.get("first_name", ""),
            "last_name": pos.get("last_name", ""),
            "address_line_one": pos.get("address_line_one", ""),
            "address_line_two": pos.get("address_line_two", ""),
            "city": pos.get("city", ""),
            "state": pos.get("state", ""),
            "zip_code": pos.get("zip_code", ""),
            "email": pos.get("email", ""),
            "phone": pos.get("phone", ""),
            "u_matched_lead_number": r["lead_id"],
            "u_order_amount": oa,
            "u_order_amount_rounded": round(float(oa), 2) if oa else 0,
            "updated_date": pos.get("updated_date", ""),
        })
    df = pd.DataFrame(rows, columns=OUTPUT_HEADER)
    df.to_csv(output_dir / "fuzzy_file_mode_output.csv", index=False)


def _write_debug_csv(rows: list[dict], output_dir: Path) -> None:
    df = pd.DataFrame(rows, columns=DEBUG_HEADER)
    df.to_csv(output_dir / "fuzzy_file_mode_debug_candidates.csv", index=False)


def _write_summary_json(stats: dict, args: argparse.Namespace, output_dir: Path) -> None:
    summary = {
        **stats,
        "warehouse_number": args.warehouse_number,
        "top_k": args.top_k,
        "recall_gate": args.recall_gate,
        "embedding_model": EMBEDDING_MODEL,
        "scoring_formula": "(4 * address_score + 3 * name_score) / 7",
        "qualify_min": fuzzy_qualify_min_score(RULES),
        "fuzzy_max": fuzzy_max_score(RULES),
        "generated_at": datetime.now().isoformat(),
        "leads_file": args.leads_file,
        "pos_file": args.pos_file,
        "exact_output_csv": args.exact_output_csv,
    }
    with open(output_dir / "fuzzy_file_mode_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.source_mode != "files":
        print("ERROR: Only --source-mode files is supported.")
        return 1

    try:
        stats = run_pipeline(args)
        if stats.get("max_score", 0) >= 100:
            print("WARNING: Fuzzy score reached 100 — this should not happen.")
            return 1
        return 0
    except Exception as exc:
        logger.exception("Fuzzy file runner failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
