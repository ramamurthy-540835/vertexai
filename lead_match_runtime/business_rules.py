import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_RULES_PATH = Path(__file__).with_name("lead_to_pos_match_rules.json")


@dataclass(frozen=True)
class WarehouseScope:
    values: tuple[int, ...] | None

    @property
    def is_all(self) -> bool:
        return self.values is None


def load_business_rules(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    rules_path = Path(path or os.environ.get("LEAD_POS_RULES_PATH", DEFAULT_RULES_PATH))
    with rules_path.open() as file:
        return json.load(file)


def get_schema(config: dict[str, Any]) -> str:
    return os.environ.get("DB_SCHEMA") or config["environment"]["schema"]


def get_project_id(config: dict[str, Any]) -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT") or config["environment"]["project_id"]


def get_warehouse_scope(config: dict[str, Any]) -> WarehouseScope:
    scope_cfg = config["warehouse_scope"]
    raw = (
        os.environ.get(scope_cfg["env_var"])
        or os.environ.get(scope_cfg["fallback_env_var"])
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
    return re.sub(r"\D", "", str(value or ""))


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


def classify_fiscal_relationship(lead: dict[str, Any], pos: dict[str, Any]) -> str:
    lead_year = int(lead.get("fiscal_year") or 0)
    lead_period = int(lead.get("fiscal_period") or 0)
    lead_week = int(lead.get("week") or 0)
    pos_year = int(pos.get("fiscal_year") or 0)
    pos_period = int(pos.get("fiscal_period") or 0)
    pos_week = int(pos.get("week") or 0)

    if (pos_year, pos_period, pos_week) < (lead_year, lead_period, lead_week):
        return "Closed - Existing"
    return "Closed - Match"


def calculate_semantic_precision_score(
    full_address_score: float,
    business_name_score: float,
    config: dict[str, Any] | None = None,
) -> float:
    return ((4 * full_address_score) + (3 * business_name_score)) / 7


def apply_deterministic_boost(
    score: float,
    lead: dict[str, Any],
    pos: dict[str, Any],
    config: dict[str, Any],
) -> float:
    boosts = config["scoring"]["deterministic_boosts"]
    boosted = score
    lead_identity = normalize_business_identity(lead)
    pos_identity = normalize_business_identity(pos)
    if lead_identity["email"] and lead_identity["email"] == pos_identity["email"]:
        boosted += boosts["email_exact_match"]
    if lead_identity["phone"] and lead_identity["phone"] == pos_identity["phone"]:
        boosted += boosts["phone_exact_match"]
    return min(boosted, boosts["cap"])


def assign_confidence_band(score: float, config: dict[str, Any]) -> dict[str, Any]:
    for band in config["confidence_bands"]["bands"]:
        if band["min_score"] <= score <= band["max_score"]:
            return band
    return config["confidence_bands"]["bands"][-1]


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
        ordered = sorted(rows, key=lambda row: row["final_score"], reverse=True)
        winner = dict(ordered[0])
        if len(ordered) > 1 and winner["final_score"] - ordered[1]["final_score"] <= ambiguity_delta:
            winner["match_type"] = config["resolution"]["ambiguity_match_type"]
            winner["match_result"] = config["resolution"]["ambiguity_state"]
            winner["manual_review_reason"] = "ambiguous_pos_candidate"
        resolved.append(winner)
    return resolved


def select_primary_transaction(matches: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    qualify_min = config["resolution"]["primary_transaction"]["qualifying_min_score"]
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for match in matches:
        match["primary_transaction"] = False
        if match.get("final_score", 0) >= qualify_min:
            grouped.setdefault(match["lead_id"], []).append(match)

    for rows in grouped.values():
        ordered = sorted(
            rows,
            key=lambda row: (
                row.get("fiscal_year") or 0,
                row.get("fiscal_period") or 0,
                row.get("week") or 0,
            ),
        )
        ordered[0]["primary_transaction"] = True
    return matches


def apply_override_policy(
    exact_result: dict[str, Any] | None,
    semantic_result: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    if exact_result and exact_result.get("score", 0) >= config["override_policy"]["exact_qualified_min_score"]:
        return exact_result
    return semantic_result or exact_result


def assign_lifecycle_state(match: dict[str, Any], config: dict[str, Any]) -> str:
    if match.get("closed_existing_flag"):
        return "Closed - Existing"
    score = float(match.get("final_score") or match.get("similarity_score") or 0)
    return assign_confidence_band(score, config)["state"]
