"""Cloud Run service entrypoint.

Endpoints:
  GET  /                              → trigger snow_sync_workflow
  GET  /health                        → liveness check 
  POST /servicenow/transaction-update → receive match/unmatch updates from ServiceNow
  POST /servicenow/manual-match           → find candidate transactions for a lead
"""

import logging
import os

from flask import Flask, jsonify, request
from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1

import config
import database
import update_pos
import manual_match
import cloud_run_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def trigger_workflow():
    location = "us-central1"
    workflow_id = "snow_sync_workflow"

    execution_client = executions_v1.ExecutionsClient()
    workflows_client = workflows_v1.WorkflowsClient()
    parent = workflows_client.workflow_path(config.PROJECT_ID, location, workflow_id)

    execution = execution_client.create_execution(request={"parent": parent})
    log.info("Workflow execution started: %s", execution.name)

    return f"Workflow execution started: {execution.name}", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "success",
        "message": "Network reachable. API is healthy.",
        "service": "leadmgmt-workflow-api",
    }), 200


@app.route("/servicenow/transaction-update", methods=["POST"])
def transaction_update():
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    records = payload.get("result")
    if not isinstance(records, list):
        return jsonify({"error": "'result' must be an array"}), 400

    if not records:
        return jsonify({"processed": 0, "succeeded": 0, "failed": 0, "errors": []}), 200

    result = update_pos.process_batch(database.get_engine(), records)
    return jsonify(result), (207 if result["errors"] else 200)


@app.route("/servicenow/manual-match", methods=["POST"])
def manual_match_endpoint():
    """Find candidate transactions for a lead via contains-style matching.

    Required input:  u_warehouse_number
    Optional inputs: u_business_name, u_address_1, u_address_2,
                     u_city, u_state_pos, u_zip_code, u_email

    Fiscal scope is always the last 2 fiscal years (current + previous),
    computed server-side from today's date. Any u_fiscal_year in the
    payload is ignored.

    Returns up to 500 candidates in ServiceNow response format, ordered
    by descending match score. Rows that match no contains fields still
    appear (score=0), at the bottom of the ranking.
    """
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    try:
        rows = manual_match.find_candidates(database.get_engine(), payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("Manual match failed")
        return jsonify({"error": f"Internal error: {e}"}), 500
    
    return jsonify(manual_match.to_servicenow_response(rows)), 200

@app.route("/trigger-drive-sync", methods=["GET"])
def trigger_something_else():
    try:
        operation = cloud_run_jobs.trigger_job(
            project_id = config.PROJECT_ID,
            region     = "us-central1",
            job_name   = "snow-sync-job",
            args       = ["main.py", "drive_to_gcs"],
        )
        return jsonify({
            "status":    "success",
            "message":   "Drive sync job triggered successfully",
            "operation": operation.operation.name,
        }), 200
    except Exception as exc:
        log.exception("Failed to trigger drive sync job")
        return jsonify({"status": "error", "message": str(exc)}), 500

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))