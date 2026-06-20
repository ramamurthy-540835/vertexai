#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ctoteam}"
REGION="${REGION:-us-central1}"
WORKFLOW_NAME="${WORKFLOW_NAME:-lead_match_workflow}"
BUCKET_NAME="${BUCKET_NAME:-lead-match-ctoteam}"
WAREHOUSE="${1:-${WAREHOUSE:-115}}"
WAIT_SECONDS="${WAIT_SECONDS:-15}"
MATCH_RUN_ID="${MATCH_RUN_ID:-workflow-${WAREHOUSE}-$(date -u +%Y%m%d%H%M%S)}"

if [[ -z "${WAREHOUSE}" || "${WAREHOUSE,,}" == "all" ]]; then
  WAREHOUSE_LABEL="all"
elif [[ "${WAREHOUSE}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  WAREHOUSE_LABEL="${WAREHOUSE}"
else
  echo "Invalid warehouse scope: ${WAREHOUSE}" >&2
  exit 1
fi

READY_URI="gs://${BUCKET_NAME}/preflight/lead_match/${PROJECT_ID}/${WAREHOUSE_LABEL}/READY"
REPORT_PREFIX="gs://${BUCKET_NAME}/reports/lead_match/${PROJECT_ID}/${WAREHOUSE_LABEL}/${MATCH_RUN_ID}"
LOCAL_REPORT_DIR="reports/lead_match/${PROJECT_ID}/${WAREHOUSE_LABEL}/${MATCH_RUN_ID}"
export PROJECT_ID REGION WAREHOUSE_LABEL MATCH_RUN_ID BUCKET_NAME

echo "Project          : ${PROJECT_ID}"
echo "Region           : ${REGION}"
echo "Workflow         : ${WORKFLOW_NAME}"
echo "Warehouse        : ${WAREHOUSE_LABEL}"
echo "Match run ID     : ${MATCH_RUN_ID}"
echo "Ready marker     : ${READY_URI}"
echo "Report prefix    : ${REPORT_PREFIX}"

if ! gsutil stat "${READY_URI}" >/dev/null 2>&1; then
  echo "Missing READY marker. Run preflight first: ${READY_URI}" >&2
  exit 1
fi

PAYLOAD="$(python3 -c 'import json, os; print(json.dumps({
  "project": os.environ["PROJECT_ID"],
  "region": os.environ["REGION"],
  "warehouse": os.environ["WAREHOUSE_LABEL"],
  "matchRunId": os.environ["MATCH_RUN_ID"],
  "reportBucket": os.environ["BUCKET_NAME"],
}))')"

START_EPOCH="$(date +%s)"
EXECUTION_NAME="$(gcloud workflows run "${WORKFLOW_NAME}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --data="${PAYLOAD}" \
  --format="value(name)")"

echo "Execution        : ${EXECUTION_NAME}"

while true; do
  STATE="$(gcloud workflows executions describe "${EXECUTION_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --format="value(state)")"
  NOW_EPOCH="$(date +%s)"
  ELAPSED="$((NOW_EPOCH - START_EPOCH))"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] state=${STATE} elapsed=${ELAPSED}s"
  case "${STATE}" in
    SUCCEEDED)
      break
      ;;
    FAILED|CANCELLED)
      gcloud workflows executions describe "${EXECUTION_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --format="yaml(error,result)"
      exit 1
      ;;
    *)
      sleep "${WAIT_SECONDS}"
      ;;
  esac
done

mkdir -p "${LOCAL_REPORT_DIR}"
gsutil cp "${REPORT_PREFIX}/summary.json" "${LOCAL_REPORT_DIR}/summary.json"
gsutil cp "${REPORT_PREFIX}/matches.csv" "${LOCAL_REPORT_DIR}/matches.csv"
gsutil cp "${REPORT_PREFIX}/report.md" "${LOCAL_REPORT_DIR}/report.md"

END_EPOCH="$(date +%s)"
echo "Completed in $((END_EPOCH - START_EPOCH))s"
echo "Local report     : ${LOCAL_REPORT_DIR}"
echo "GCS report       : ${REPORT_PREFIX}"
