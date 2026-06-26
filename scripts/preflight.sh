#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load secrets from .env.local if present
ENV_LOCAL="$PROJECT_ROOT/.env.local"
if [[ -f "$ENV_LOCAL" ]]; then
  set -a
  source "$ENV_LOCAL"
  set +a
fi

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

echo "Costco Lead-to-POS Matching Pipeline — Preflight"
echo "Rules: $PROJECT_ROOT/lead_match_runtime/lead_to_pos_match_rules.json"
echo

python3 "$SCRIPT_DIR/preflight_checks.py" "$@"
EXIT_CODE=$?

echo
if [[ $EXIT_CODE -eq 0 ]]; then
  echo "PREFLIGHT_PASS"
else
  echo "PREFLIGHT_FAIL"
fi

exit $EXIT_CODE
