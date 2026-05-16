"""
Field-map loader and per-row mapping logic.

field_map.json supports TWO shapes:

  Flat (simple rename only):
    {
      "<source_column_name>": "<db_column_name>",
      ...
    }

  Nested (with optional type / default / required):
    {
      "<source_column_name>": {
        "target":   "<db_column_name>",
        "type":     "string" | "int" | "float" | "bool" | "date" | "datetime",
        "default":  <optional default if source value is null/empty>,
        "required": true | false   (default false)
      },
      ...
    }

You can mix the two shapes in one file — entries that need type coercion
use the nested form; everything else can stay flat.

apply_field_map returns a dict whose keys are DB column names. Returns None
if a required field is missing — caller should drop that row.
"""
import json
import logging
from datetime import date, datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def load_field_map(path: str) -> Dict[str, Any]:
    """Load field_map.json from a local path or gs:// path."""
    if path.startswith("gs://"):
        return _load_from_gcs(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_from_gcs(gcs_path: str) -> Dict[str, Any]:
    from google.cloud import storage
    _, rest = gcs_path.split("gs://", 1)
    bucket, blob = rest.split("/", 1)
    content = storage.Client().bucket(bucket).blob(blob).download_as_text()
    return json.loads(content)


def apply_field_map(
    raw_row: Dict[str, Any],
    field_map: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Map one raw row's source columns to DB columns. Returns None on error."""
    out: Dict[str, Any] = {}
    for source_col, spec in field_map.items():
        # Normalize spec: flat string → {"target": that_string}
        if isinstance(spec, str):
            spec = {"target": spec}
        elif not isinstance(spec, dict):
            logger.error(
                f"Invalid field_map entry for '{source_col}': {spec!r} "
                f"(must be string or dict)"
            )
            continue

        target_col = spec["target"]
        type_name = spec.get("type", "string")
        default = spec.get("default")
        required = spec.get("required", False)

        raw_val = raw_row.get(source_col)
        if raw_val is None or (isinstance(raw_val, str) and raw_val.strip() == ""):
            if default is not None:
                out[target_col] = default
                continue
            if required:
                logger.warning(f"Missing required field '{source_col}'; dropping row")
                return None
            out[target_col] = None
            continue

        try:
            out[target_col] = _coerce(raw_val, type_name)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Coercion error for '{source_col}'={raw_val!r} as {type_name}: {e}"
            )
            if required:
                return None
            out[target_col] = None

    return out


def _coerce(value: Any, type_name: str) -> Any:
    """Coerce a raw value to the declared type."""
    if type_name == "string":
        return str(value).strip()
    if type_name == "int":
        return int(float(str(value).strip()))  # tolerates "42.0"
    if type_name == "float":
        return float(str(value).strip())
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
        raise ValueError(f"Cannot parse bool from {value!r}")
    if type_name == "date":
        if isinstance(value, date):
            return value
        return _parse_date(str(value).strip()).date()
    if type_name == "datetime":
        if isinstance(value, datetime):
            return value
        return _parse_date(str(value).strip())
    raise ValueError(f"Unknown type: {type_name}")


def _parse_date(s: str) -> datetime:
    """Try a few common date formats, fall back to fromisoformat."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(s)