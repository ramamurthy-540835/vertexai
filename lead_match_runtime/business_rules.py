import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).with_name("lead_to_pos_match_rules.json")


@dataclass(frozen=True)
class WarehouseScope:
    values: tuple[int, ...] | None

    @property
    def is_all(self) -> bool:
        return self.values is None


def load_business_rules(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    rules_path = Path(path or os.environ.get("LEAD_POS_RULES_PATH", DEFAULT_RULES_PATH))
    if not rules_path.exists():
        raise FileNotFoundError(
            f"Business rules file not found: {rules_path}. "
            "Set LEAD_POS_RULES_PATH or restore the default rules file."
        )

    try:
        with rules_path.open(encoding="utf-8") as file:
            rules = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in business rules file {rules_path}: {exc}") from exc

    required_keys = {
        "candidate_retrieval",
        "decision_rules",
        "embeddings",
        "environment",
        "fiscal_rules",
        "matching_markers",
        "matching_sets",
        "override_policy",
        "resolution",
        "scoring",
        "warehouse_scope",
    }
    missing = sorted(required_keys - set(rules))
    if missing:
        raise ValueError(f"Business rules config missing required keys: {missing}")
    return rules


def decision_rules(config: dict[str, Any]) -> dict[str, Any]:
    return config["decision_rules"]


def exact_score(config: dict[str, Any]) -> float:
    return float(decision_rules(config)["exact_score"])


def exact_authoritative_score(config: dict[str, Any]) -> float:
    return float(decision_rules(config)["exact_authoritative_score"])


def exact_match_type(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["exact_match_type"])


def exact_match_types(config: dict[str, Any], *, lower: bool = True) -> tuple[str, ...]:
    values = tuple(
        str(value).strip()
        for value in decision_rules(config)["exact_match_types"]
        if str(value).strip()
    )
    return tuple(value.lower() for value in values) if lower else values


def exact_lifecycle_state(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["exact_lifecycle_state"])


def closed_existing_lifecycle_state(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["closed_existing_lifecycle_state"])


def no_match_lifecycle_state(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["no_match_lifecycle_state"])


def fuzzy_match_type(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["fuzzy_match_type"])


def manual_review_match_type(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["manual_review_match_type"])


def fuzzy_match_types(config: dict[str, Any], *, lower: bool = True) -> tuple[str, ...]:
    values = (fuzzy_match_type(config), manual_review_match_type(config))
    return tuple(value.lower() for value in values) if lower else values


def fuzzy_qualify_min_score(config: dict[str, Any]) -> float:
    return float(decision_rules(config)["fuzzy_qualify_min_score"])


def fuzzy_max_score(config: dict[str, Any]) -> float:
    return float(decision_rules(config)["fuzzy_max_score"])


def fuzzy_artifact_score(config: dict[str, Any]) -> float:
    return float(decision_rules(config)["fuzzy_artifact_score"])


def fuzzy_reject_below_floor(config: dict[str, Any]) -> bool:
    return bool(decision_rules(config)["fuzzy_reject_below_floor"])


def fuzzy_score_bands(config: dict[str, Any]) -> list[dict[str, Any]]:
    dr = decision_rules(config)
    lifecycle = str(dr["fuzzy_lifecycle_state"])
    match_type = str(dr["fuzzy_match_type"])
    subtiers = dr.get("optional_confidence_subtiers", {}).get("subtiers", [])
    if subtiers:
        bands = [
            {
                "name": s["name"],
                "min_score": s["min_score"],
                "max_score": s["max_score"],
                "lifecycle_state": lifecycle,
                "match_type": match_type,
            }
            for s in subtiers
        ]
    else:
        bands = [
            {
                "name": lifecycle,
                "min_score": float(dr["fuzzy_qualify_min_score"]),
                "max_score": float(dr["fuzzy_max_score"]),
                "lifecycle_state": lifecycle,
                "match_type": match_type,
            }
        ]
    return sorted(bands, key=lambda band: float(band["min_score"]), reverse=True)


def embedding_field_weight(config: dict[str, Any], field: str) -> float:
    return float(config["embeddings"]["fields"][field]["weight"])


def semantic_precision_weights(config: dict[str, Any]) -> tuple[float, float]:
    fields = config["embeddings"]["fields"]
    return (
        float(fields["address_variant"]["weight"]),
        float(fields["name_variant"]["weight"]),
    )


def precision_score_formula(config: dict[str, Any]) -> str:
    return str(config["scoring"]["precision_score_formula"])


def confidence_bands(config: dict[str, Any]) -> list[dict[str, Any]]:
    dr = decision_rules(config)
    bands = [
        {
            "name": band["name"],
            "state": band["lifecycle_state"],
            "min_score": band["min_score"],
            "max_score": band["max_score"],
        }
        for band in fuzzy_score_bands(config)
    ]
    bands.append(
        {
            "name": str(dr["no_match_lifecycle_state"]),
            "state": str(dr["no_match_lifecycle_state"]),
            "min_score": 0,
            "max_score": float(dr["no_match_max_score"]),
        }
    )
    return sorted(bands, key=lambda band: float(band["min_score"]), reverse=True)


def fuzzy_lifecycle_state(score: float, config: dict[str, Any]) -> str:
    dr = decision_rules(config)
    floor = float(dr["fuzzy_qualify_min_score"])
    if float(score) >= floor:
        return str(dr["fuzzy_lifecycle_state"])
    return str(dr["no_match_lifecycle_state"])


def lifecycle_state_for_match_type(
    match_type: str,
    score: float | None,
    config: dict[str, Any],
) -> str:
    if str(match_type or "").strip().lower() in exact_match_types(config):
        return exact_lifecycle_state(config)
    return fuzzy_lifecycle_state(float(score or 0), config)


def _require(section: dict[str, Any], key: str, section_name: str) -> Any:
    if key not in section:
        raise KeyError(f"Missing '{key}' in '{section_name}' — add it to lead_to_pos_match_rules.json")
    return section[key]


def _env(config: dict[str, Any]) -> dict[str, Any]:
    return _require(config, "environment", "root")


def _tuning(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "tuning", "environment")


def _hnsw(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "hnsw_index", "environment")


def _safety(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "safety_flags", "environment")


def _cloud_sql(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "cloud_sql", "environment")


def _vertex_ai(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "vertex_ai", "environment")


def _models(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "models", "environment")


def _dry_run_controls(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "dry_run_controls", "environment")


def _fiscal_defaults(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "fiscal_defaults", "environment")


def _gcs(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "gcs", "environment")


def _cloud_run(config: dict[str, Any]) -> dict[str, Any]:
    return _require(_env(config), "cloud_run", "environment")


def get_schema(config: dict[str, Any]) -> str:
    return str(_require(_cloud_sql(config), "schema", "environment.cloud_sql"))


def get_project_id(config: dict[str, Any]) -> str:
    return str(_require(_env(config), "project_id", "environment"))


def get_vertex_project(config: dict[str, Any]) -> str:
    return str(_require(_vertex_ai(config), "project_id", "environment.vertex_ai"))


def get_vertex_location(config: dict[str, Any]) -> str:
    return str(_require(_vertex_ai(config), "location", "environment.vertex_ai"))


def get_vertex_timeout(config: dict[str, Any]) -> float:
    return float(_require(_vertex_ai(config), "timeout_seconds", "environment.vertex_ai"))


def get_vertex_timeout_ms(config: dict[str, Any]) -> int:
    """Convert JSON timeout_seconds to milliseconds for google-genai HttpOptions."""
    return int(get_vertex_timeout(config) * 1000)


def get_gemini_model(config: dict[str, Any]) -> str:
    return str(_require(_models(config), "gemini_flash", "environment.models"))


def get_embedding_model(config: dict[str, Any]) -> str:
    return str(_require(_models(config), "embedding", "environment.models"))


def get_report_bucket(config: dict[str, Any]) -> str:
    return str(_require(_gcs(config), "report_bucket", "environment.gcs"))


def get_cloud_run_service(config: dict[str, Any]) -> str:
    return str(_require(_cloud_run(config), "service_name", "environment.cloud_run"))


def get_cloud_run_health_path(config: dict[str, Any]) -> str:
    return str(_require(_cloud_run(config), "health_path", "environment.cloud_run"))


def get_cloudsql_connection_name(config: dict[str, Any]) -> str:
    return str(_require(_cloud_sql(config), "connection_name", "environment.cloud_sql"))


def get_cloudsql_socket_dir(config: dict[str, Any]) -> str:
    return str(_require(_cloud_sql(config), "socket_dir", "environment.cloud_sql"))


def get_db_host(config: dict[str, Any]) -> str:
    return str(_require(_cloud_sql(config), "host", "environment.cloud_sql"))


def get_db_port(config: dict[str, Any]) -> int:
    return int(_require(_cloud_sql(config), "port", "environment.cloud_sql"))


def get_db_name(config: dict[str, Any]) -> str:
    return str(_require(_cloud_sql(config), "database", "environment.cloud_sql"))


def get_env_label(config: dict[str, Any]) -> str:
    return str(_require(_env(config), "label", "environment"))


def get_safety_flag(config: dict[str, Any], flag: str) -> bool:
    return bool(_require(_safety(config), flag, "environment.safety_flags"))


def get_dry_run(config: dict[str, Any]) -> bool:
    return bool(_require(_safety(config), "dry_run", "environment.safety_flags"))


def get_dry_run_match_row_limit(config: dict[str, Any]) -> int:
    return int(_require(_dry_run_controls(config), "match_row_limit", "environment.dry_run_controls"))


def get_dry_run_writeback(config: dict[str, Any]) -> bool:
    return bool(_require(_dry_run_controls(config), "writeback_business_tables", "environment.dry_run_controls"))


def get_fiscal_year(config: dict[str, Any]) -> int:
    return int(_require(_fiscal_defaults(config), "fiscal_year", "environment.fiscal_defaults"))


def get_fiscal_period(config: dict[str, Any]) -> int:
    return int(_require(_fiscal_defaults(config), "fiscal_period", "environment.fiscal_defaults"))


def get_tuning_int(config: dict[str, Any], key: str) -> int:
    return int(_require(_tuning(config), key, "environment.tuning"))


def get_tuning_float(config: dict[str, Any], key: str) -> float:
    return float(_require(_tuning(config), key, "environment.tuning"))


def get_tuning_bool(config: dict[str, Any], key: str) -> bool:
    return bool(_require(_tuning(config), key, "environment.tuning"))


def get_hnsw_m(config: dict[str, Any]) -> int:
    return int(_require(_hnsw(config), "m", "environment.hnsw_index"))


def get_hnsw_ef_construction(config: dict[str, Any]) -> int:
    return int(_require(_hnsw(config), "ef_construction", "environment.hnsw_index"))


def get_hnsw_ef_search(config: dict[str, Any]) -> int:
    return int(_require(_hnsw(config), "ef_search", "environment.hnsw_index"))


def get_hnsw_maintenance_work_mem(config: dict[str, Any]) -> str:
    return str(_require(_hnsw(config), "maintenance_work_mem", "environment.hnsw_index"))


def get_warehouse_scope(config: dict[str, Any]) -> WarehouseScope:
    scope_cfg = config["warehouse_scope"]
    # WAREHOUSE is the per-run override used by Cloud Workflows and preflight
    # smoke checks. It must win over a Cloud Run Job default like WAREHOUSE_SCOPE=ALL.
    raw = (
        os.environ.get(scope_cfg["fallback_env_var"])
        or os.environ.get(scope_cfg["env_var"])
        or scope_cfg["default"]
    )
    raw = raw.strip()
    if not raw or raw.upper() == "ALL":
        return WarehouseScope(values=None)

    values = []
    for token in raw.split(","):
        token = token.strip()
        if not token.isdigit():
            raise ValueError(f"Invalid warehouse scope value: {raw!r}")
        values.append(int(token))
    return WarehouseScope(values=tuple(values))


def normalize_state(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text[:2]


def normalize_zip(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:5]


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def normalize_text(value: Any, uppercase: bool = False) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text.upper() if uppercase else text


def normalize_address(value: Any) -> str:
    text = normalize_text(value)
    replacements = {
        r"\bSTREET\b": "ST",
        r"\bAVENUE\b": "AVE",
        r"\bROAD\b": "RD",
        r"\bDRIVE\b": "DR",
        r"\bLANE\b": "LN",
        r"\bBOULEVARD\b": "BLVD",
    }
    upper = text.upper()
    for pattern, replacement in replacements.items():
        upper = re.sub(pattern, replacement, upper)
    return upper


def normalize_business_identity(record: dict[str, Any]) -> dict[str, str]:
    address_parts = [
        normalize_address(record.get("address_line_one")),
        normalize_text(record.get("city"), uppercase=True),
        normalize_state(record.get("state")),
        normalize_zip(record.get("zip_code")),
    ]
    full_address = " ".join(part for part in address_parts if part)
    business_name = normalize_text(record.get("business_name"), uppercase=True)
    return {
        "business_name": business_name,
        "full_address": full_address,
        "combined_field": " ".join(part for part in (business_name, full_address) if part),
        "email": normalize_text(record.get("email"), uppercase=True),
        "phone": normalize_phone(record.get("phone")),
    }


def build_embedding_text(record: dict[str, Any], field: str) -> str | None:
    identity = normalize_business_identity(record)
    value = identity.get(field)
    return value or None


def apply_blocking_rules(lead: dict[str, Any], pos: dict[str, Any]) -> bool:
    return lead.get("warehouse_number") == pos.get("warehouse_number")


def classify_fiscal_relationship(
    lead: dict[str, Any],
    pos: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    rules = config or load_business_rules()
    lead_year = int(lead.get("fiscal_year") or 0)
    lead_period = int(lead.get("fiscal_period") or 0)
    lead_week = int(lead.get("week") or 0)
    pos_year = int(pos.get("fiscal_year") or 0)
    pos_period = int(pos.get("fiscal_period") or 0)
    pos_week = int(pos.get("week") or 0)

    if (pos_year, pos_period, pos_week) < (lead_year, lead_period, lead_week):
        return closed_existing_lifecycle_state(rules)
    return exact_lifecycle_state(rules)


def calculate_semantic_precision_score(
    full_address_score: float,
    business_name_score: float,
    config: dict[str, Any] | None = None,
) -> float:
    rules = config or load_business_rules()
    address_weight, business_weight = semantic_precision_weights(rules)
    total_weight = address_weight + business_weight
    if total_weight <= 0:
        raise ValueError("Semantic precision score weights must sum to a positive value")
    return ((address_weight * full_address_score) + (business_weight * business_name_score)) / total_weight


def normalize_fuzzy_final_score(
    raw_score: float,
    *,
    config: dict[str, Any] | None = None,
    lead_id: Any | None = None,
    pos_id: Any | None = None,
    reject_below_floor: bool | None = None,
) -> float | None:
    rules = config or load_business_rules()
    floor = fuzzy_qualify_min_score(rules)
    ceiling = fuzzy_max_score(rules)
    artifact_threshold = fuzzy_artifact_score(rules)
    reject = fuzzy_reject_below_floor(rules) if reject_below_floor is None else reject_below_floor
    score = round(float(raw_score), 2)
    if score < floor:
        return None if reject else score
    if score >= artifact_threshold:
        logger.warning(
            "Raw fuzzy score %.3f exceeded fuzzy ceiling for lead_id=%s pos_id=%s; clamping to %.2f",
            score,
            lead_id,
            pos_id,
            ceiling,
        )
    return min(ceiling, score)


def calculate_fuzzy_final_score(
    full_address_score: float,
    business_name_score: float,
    *,
    config: dict[str, Any] | None = None,
    lead_id: Any | None = None,
    pos_id: Any | None = None,
) -> float | None:
    raw_score = calculate_semantic_precision_score(full_address_score, business_name_score, config=config)
    return normalize_fuzzy_final_score(raw_score, config=config, lead_id=lead_id, pos_id=pos_id)


def apply_deterministic_boost(
    score: float,
    lead: dict[str, Any],
    pos: dict[str, Any],
    config: dict[str, Any],
    winning_set_def: dict[str, Any] | None = None,
) -> tuple[float, float, float]:
    boosts = config["scoring"]["deterministic_boosts"]
    boosted = score
    lead_identity = lead if "combined_field" in lead else normalize_business_identity(lead)
    email_field = (winning_set_def or {}).get("email_field", "email")
    phone_field = (winning_set_def or {}).get("phone_field", "phone")
    lead_email = normalize_email(lead_identity.get("email", ""))
    lead_phone = normalize_phone(lead_identity.get("phone", ""))
    pos_email = normalize_email(pos.get(email_field, ""))
    pos_phone = normalize_phone(pos.get(phone_field, ""))
    email_boost = float(boosts["email_exact_match"]) if lead_email and lead_email == pos_email else 0.0
    phone_boost = float(boosts["phone_exact_match"]) if lead_phone and lead_phone == pos_phone else 0.0
    boosted += email_boost + phone_boost
    return min(boosted, float(boosts["cap"])), email_boost, phone_boost


def assign_confidence_band(score: float, config: dict[str, Any]) -> dict[str, Any]:
    bands = sorted(
        confidence_bands(config),
        key=lambda band: float(band["min_score"]),
        reverse=True,
    )
    if not bands:
        raise ValueError("Business rules config has no confidence bands")

    numeric_score = float(score)
    for band in bands:
        if float(band["min_score"]) <= numeric_score <= float(band["max_score"]):
            return band
    if numeric_score > max(float(band["max_score"]) for band in bands):
        logger.warning(
            "Score %.3f exceeds highest band max_score %.3f; defaulting to highest band.",
            numeric_score, float(bands[0]["max_score"])
        )
        return bands[0]
    return bands[-1]


def resolve_pos_to_single_lead(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    ambiguity_delta = config["resolution"]["ambiguity_delta"]
    resolved = []
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["pos_id"], []).append(candidate)

    for rows in grouped.values():
        if not rows:
            continue
        ordered = sorted(rows, key=lambda row: row["final_score"], reverse=True)
        winner = dict(ordered[0])
        if len(ordered) > 1 and winner["final_score"] - ordered[1]["final_score"] <= ambiguity_delta:
            winner["match_type"] = config["resolution"]["ambiguity_match_type"]
            winner["match_result"] = config["resolution"]["ambiguity_state"]
            winner["manual_review_reason"] = "ambiguous_pos_candidate"
            logger.info(
                "Ambiguous POS candidate detected for pos_id=%s (delta=%.3f <= threshold=%.3f)",
                winner.get("pos_id"), winner["final_score"] - ordered[1]["final_score"], ambiguity_delta
            )
        resolved.append(winner)
    return resolved


def select_primary_transaction(matches: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    qualify_min = config["resolution"]["primary_transaction"]["qualifying_min_score"]
    results = []
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for match in matches:
        record = dict(match)
        record["primary_transaction"] = False
        results.append(record)
        if record.get("final_score", 0) >= qualify_min:
            grouped.setdefault(record.get("lead_id"), []).append(record)

    for rows in grouped.values():
        if not rows:
            continue
        ordered = sorted(
            rows,
            key=lambda row: (
                row.get("fiscal_year") or 0,
                row.get("fiscal_period") or 0,
                row.get("week") or 0,
            ),
        )
        ordered[0]["primary_transaction"] = True
    return results


def _result_score(result: dict[str, Any]) -> float:
    for key in ("score", "final_score", "similarity_score", "match_score"):
        value = result.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def apply_override_policy(
    exact_result: dict[str, Any] | None,
    semantic_result: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    exact_score = _result_score(exact_result) if exact_result else 0.0
    exact_min = exact_authoritative_score(config)
    
    if exact_result and exact_score >= exact_min:
        logger.info(
            "Exact override applied (score=%.3f met threshold=%.3f)",
            exact_score, exact_min
        )
        return exact_result
        
    logger.debug("Semantic/default result selected.")
    return semantic_result or exact_result


def assign_lifecycle_state(match: dict[str, Any], config: dict[str, Any]) -> str:
    if match.get("closed_existing_flag"):
        return closed_existing_lifecycle_state(config)
    score = float(match.get("final_score") or match.get("similarity_score") or 0)
    return assign_confidence_band(score, config)["state"]


# ─────────────────────────────────────────────────────────────
# v2.3 helpers: six-set matching, fiscal rules, OMS variants
# ─────────────────────────────────────────────────────────────

def matching_sets(config: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        list(config["matching_sets"]["sets"]),
        key=lambda item: int(item["set"]),
    )


def matching_set_definitions(config: dict[str, Any]) -> list[dict[str, Any]]:
    return matching_sets(config)


def matching_set_by_id(config: dict[str, Any], set_id: int) -> dict[str, Any] | None:
    for item in matching_sets(config):
        if int(item.get("set", -1)) == int(set_id):
            return dict(item)
    return None


def matching_sets_fields(config: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for item in matching_sets(config):
        fields.add(str(item.get("name_field", "")).strip())
        fields.add(str(item.get("address_field", "")).strip())
        fields.add(str(item.get("email_field", "")).strip())
        fields.add(str(item.get("phone_field", "")).strip())
    return fields


def matching_set_email_fields(config: dict[str, Any]) -> list[str]:
    fields = [str(item.get("email_field", "")).strip() for item in matching_sets(config)]
    return list(dict.fromkeys(field for field in fields if field))


def matching_set_phone_fields(config: dict[str, Any]) -> list[str]:
    fields = [str(item.get("phone_field", "")).strip() for item in matching_sets(config)]
    return list(dict.fromkeys(field for field in fields if field))


def matching_set_name_fields(config: dict[str, Any]) -> list[str]:
    fields = [str(item.get("name_field", "")).strip() for item in matching_sets(config)]
    return list(dict.fromkeys(field for field in fields if field))


def matching_set_address_fields(config: dict[str, Any]) -> list[str]:
    fields = [str(item.get("address_field", "")).strip() for item in matching_sets(config)]
    return list(dict.fromkeys(field for field in fields if field))


def closed_existing_lifecycle_state_from_fiscal_rules(config: dict[str, Any]) -> str:
    classification = config["fiscal_rules"].get("classification", [])
    if classification:
        for rule in classification:
            if str(rule.get("name", "")).lower().startswith("closed"):
                state = rule.get("lifecycle_state")
                if state:
                    return str(state)
    return closed_existing_lifecycle_state(config)


def fuzzy_boost_rule(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config["scoring"].get("deterministic_boosts", {}))


def fuzzy_denom(config: dict[str, Any]) -> float:
    return float(
        semantic_precision_weights(config)[0] + semantic_precision_weights(config)[1]
    )


def pos_embedding_variant_field_aliases(config: dict[str, Any]) -> dict[str, str]:
    """Map business rule variant names to current pos_embeddings vector columns."""
    return dict(config["matching_sets"]["embedding_column_mapping"])


def pos_transaction_field_aliases(config: dict[str, Any]) -> dict[str, str]:
    return dict(config["matching_sets"]["transaction_field_mapping"])


def matching_set_selection_rule(config: dict[str, Any]) -> str:
    return str(config["matching_sets"]["selection_rule"])


def matching_set_scoring_formula(config: dict[str, Any]) -> str:
    return str(config["matching_sets"]["scoring_formula"])


def skip_set_if_variant_blank(config: dict[str, Any]) -> bool:
    return bool(config["matching_sets"]["skip_set_if_variant_blank"])


def fiscal_periods_per_year(config: dict[str, Any]) -> int:
    return int(config["fiscal_rules"]["periods_per_year"])


def fiscal_ce_period_window(config: dict[str, Any]) -> int:
    return int(config["fiscal_rules"]["ce_period_window"])


def name_variant_sources(config: dict[str, Any]) -> list[str]:
    return list(config["matching_markers"]["name_variant_sources"])


def address_variant_sources(config: dict[str, Any]) -> list[str]:
    return list(config["matching_markers"]["address_variant_sources"])


def fuzzy_lifecycle_state_label(config: dict[str, Any]) -> str:
    return str(decision_rules(config)["fuzzy_lifecycle_state"])


def confidence_subtier(score: float, config: dict[str, Any]) -> str | None:
    subtiers = decision_rules(config).get("optional_confidence_subtiers", {}).get("subtiers", [])
    numeric = float(score)
    for s in sorted(subtiers, key=lambda x: float(x["min_score"]), reverse=True):
        if float(s["min_score"]) <= numeric <= float(s["max_score"]):
            return str(s["name"])
    return None


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def build_oms_address(record: dict[str, Any], prefix: str = "oms") -> str:
    if prefix == "oms":
        parts = [
            record.get("oms_address_line_1"),
            record.get("oms_city"),
            record.get("oms_state"),
            record.get("oms_zip"),
        ]
    else:
        parts = [
            record.get("oms_address_line_1_v2"),
            record.get("oms_city_2"),
            record.get("oms_state_2"),
            record.get("oms_zip_2"),
        ]
    raw = " ".join(str(p) for p in parts if p and str(p).strip())
    return normalize_address(raw) if raw.strip() else ""


def build_pos_variant_texts(record: dict[str, Any]) -> dict[str, str | None]:
    identity = normalize_business_identity(record)
    oms_company = normalize_text(record.get("oms_company_name"), uppercase=True)
    oms2_company = normalize_text(record.get("oms2_company_name"), uppercase=True)
    oms_addr = build_oms_address(record, "oms")
    oms2_addr = build_oms_address(record, "oms2")
    return {
        "combined_field": identity.get("combined_field") or None,
        "business_name": identity.get("business_name") or None,
        "full_address": identity.get("full_address") or None,
        "oms_company_name": oms_company or None,
        "oms2_company_name": oms2_company or None,
        "full_oms_address": oms_addr or None,
        "full_oms2_address": oms2_addr or None,
    }
